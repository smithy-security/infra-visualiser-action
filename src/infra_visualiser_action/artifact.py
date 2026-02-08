import base64
import hashlib
import json
import os
import time
import requests
from pathlib import Path
from urllib.parse import urlparse

import click


class GitHubArtifactClient:
    """
    Uploads artifacts using the GitHub Actions Artifact v4 protocol.

    The v4 flow uses Twirp RPC (JSON-over-HTTP) to create and finalize artifacts,
    and uploads file content directly to Azure Blob Storage via a pre-signed SAS URL.
    """

    TWIRP_SERVICE = "github.actions.results.api.v1.ArtifactService"
    RETRYABLE_STATUS_CODES = {502, 503, 504, 429, 500}
    MAX_RETRY_ATTEMPTS = 5
    BASE_RETRY_INTERVAL_S = 3.0
    RETRY_MULTIPLIER = 1.5

    def __init__(self, github_token: str):
        self.runtime_token = os.environ.get("ACTIONS_RUNTIME_TOKEN")
        self.run_id = os.environ.get("GITHUB_RUN_ID")
        self.repo = os.environ.get("GITHUB_REPOSITORY")
        self.github_token = github_token

        if not self.github_token:
            raise click.ClickException("GITHUB_TOKEN is required for GitHubArtifactClient.")

        if not self.runtime_token:
            raise click.ClickException(
                "ACTIONS_RUNTIME_TOKEN is missing. "
                "To use internal artifact upload, you must expose this variable to the container. "
                "See documentation on how to pass 'ACTIONS_RUNTIME_TOKEN' using 'actions/github-script' "
                "or by mapping the environment."
            )

        results_url = os.environ.get("ACTIONS_RESULTS_URL")
        if not results_url:
            raise click.ClickException(
                "ACTIONS_RESULTS_URL is missing. "
                "This environment variable is required for the v4 artifact upload protocol."
            )

        # Extract just the origin (scheme + host) from the results URL
        parsed = urlparse(results_url)
        self.results_url = f"{parsed.scheme}://{parsed.netloc}"

        if not self.repo or not self.run_id:
            raise click.ClickException(
                "GITHUB_REPOSITORY or GITHUB_RUN_ID environment variables missing."
            )

        # Decode backend IDs from the JWT token
        self.backend_ids = self._get_backend_ids_from_token()

    def _get_backend_ids_from_token(self) -> dict:
        """
        Decode the ACTIONS_RUNTIME_TOKEN JWT (without verification) to extract
        workflowRunBackendId and workflowJobRunBackendId from the 'scp' claim.

        The scp claim looks like:
          "Actions.ExampleScope Actions.Results:<runBackendId>:<jobBackendId>"
        """
        try:
            parts = self.runtime_token.split(".")
            if len(parts) < 2:
                raise ValueError("Token does not have a payload segment")

            payload_b64 = parts[1]
            # Add padding for base64 decoding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            decoded = json.loads(base64.urlsafe_b64decode(payload_b64))
            scp = decoded.get("scp", "")

            for scope in scp.split(" "):
                scope_parts = scope.split(":")
                if scope_parts[0] == "Actions.Results" and len(scope_parts) == 3:
                    return {
                        "workflowRunBackendId": scope_parts[1],
                        "workflowJobRunBackendId": scope_parts[2],
                    }

            raise ValueError("No Actions.Results scope found in token claims")
        except Exception as e:
            raise click.ClickException(
                f"Failed to extract backend IDs from ACTIONS_RUNTIME_TOKEN: {e}"
            )

    def _twirp_request(self, method: str, data: dict) -> dict:
        """
        Make a Twirp JSON RPC request to the artifact service with retry logic.

        Args:
            method: The RPC method name (e.g. "CreateArtifact", "FinalizeArtifact").
            data: The JSON request body (snake_case field names).

        Returns:
            The parsed JSON response body.
        """
        url = f"{self.results_url}/twirp/{self.TWIRP_SERVICE}/{method}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.runtime_token}",
        }

        last_error = None
        for attempt in range(self.MAX_RETRY_ATTEMPTS):
            try:
                resp = requests.post(url, json=data, headers=headers)

                if 200 <= resp.status_code < 300:
                    return resp.json()

                if resp.status_code not in self.RETRYABLE_STATUS_CODES:
                    raise click.ClickException(
                        f"Twirp {method} failed: {resp.status_code} {resp.text}"
                    )

                last_error = f"{resp.status_code} {resp.text}"
            except click.ClickException:
                raise
            except requests.RequestException as e:
                last_error = str(e)

            if attempt + 1 < self.MAX_RETRY_ATTEMPTS:
                wait = self.BASE_RETRY_INTERVAL_S * (self.RETRY_MULTIPLIER ** attempt)
                click.echo(
                    f"  Attempt {attempt + 1}/{self.MAX_RETRY_ATTEMPTS} for {method} "
                    f"failed ({last_error}). Retrying in {wait:.1f}s..."
                )
                time.sleep(wait)

        raise click.ClickException(
            f"Twirp {method} failed after {self.MAX_RETRY_ATTEMPTS} attempts: {last_error}"
        )

    def upload_artifact(self, name: str, file_path: Path, retention_days: int = 90) -> str:
        """
        Uploads an artifact using the v4 protocol and returns the download URL.

        Flow:
            1. CreateArtifact  -> get a signed upload URL
            2. PUT file bytes  -> upload directly to Azure Blob Storage
            3. FinalizeArtifact -> confirm upload with size and hash
            4. Poll REST API    -> retrieve the download URL
        """
        click.echo(f"  📦 Preparing to upload artifact '{name}'...")

        # 1. CreateArtifact
        create_req = {
            "workflow_run_backend_id": self.backend_ids["workflowRunBackendId"],
            "workflow_job_run_backend_id": self.backend_ids["workflowJobRunBackendId"],
            "name": name,
            "version": 4,
        }

        create_resp = self._twirp_request("CreateArtifact", create_req)

        if not create_resp.get("ok"):
            raise click.ClickException(
                "CreateArtifact: response from backend was not ok"
            )

        signed_upload_url = create_resp.get("signed_upload_url")
        if not signed_upload_url:
            raise click.ClickException(
                "CreateArtifact: no signed_upload_url in response"
            )

        # 2. Upload file to Azure Blob Storage
        click.echo(f"  Uploading {file_path.name}...")
        file_content = file_path.read_bytes()
        file_size = len(file_content)
        sha256_hash = hashlib.sha256(file_content).hexdigest()

        upload_resp = requests.put(
            signed_upload_url,
            data=file_content,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "application/octet-stream",
            },
        )

        if not upload_resp.ok:
            raise click.ClickException(
                f"Failed to upload file to blob storage: {upload_resp.status_code} {upload_resp.text}"
            )

        # 3. FinalizeArtifact
        click.echo("  🔒 Finalizing artifact...")
        finalize_req = {
            "workflow_run_backend_id": self.backend_ids["workflowRunBackendId"],
            "workflow_job_run_backend_id": self.backend_ids["workflowJobRunBackendId"],
            "name": name,
            "size": str(file_size),
            "hash": {"value": f"sha256:{sha256_hash}"},
        }

        finalize_resp = self._twirp_request("FinalizeArtifact", finalize_req)

        if not finalize_resp.get("ok"):
            raise click.ClickException(
                "FinalizeArtifact: response from backend was not ok"
            )

        artifact_id = finalize_resp.get("artifact_id")
        click.echo(f"  Artifact '{name}' uploaded successfully (ID: {artifact_id}).")

        # 4. Fetch the download URL
        return self.get_artifact_url(name, artifact_id=artifact_id)

    def get_artifact_url(self, artifact_name: str, artifact_id: str | None = None) -> str:
        """
        Fetches the artifact download URL from the GitHub REST API.

        If artifact_id is provided (from FinalizeArtifact), constructs the URL
        directly. Otherwise, polls the API to find the artifact by name.
        """
        if artifact_id:
            url = (
                f"https://api.github.com/repos/{self.repo}"
                f"/actions/artifacts/{artifact_id}/zip"
            )
            click.echo(f"  Artifact download URL: {url}")
            return url

        api_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/artifacts"
        headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        click.echo("  Fetching artifact download URL...")
        for _ in range(5):
            resp = requests.get(api_url, headers=headers)
            if resp.ok:
                data = resp.json()
                artifacts = data.get("artifacts", [])
                for artifact in artifacts:
                    if artifact.get("name") == artifact_name:
                        return artifact.get("archive_download_url")
            time.sleep(2)

        raise click.ClickException(
            "Could not determine artifact download URL after upload."
        )
