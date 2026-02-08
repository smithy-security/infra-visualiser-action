import os
import time
import requests

from pathlib import Path

import click

class GitHubArtifactClient:
    def __init__(self, github_token: str):
        self.runtime_url = os.environ.get("ACTIONS_RUNTIME_URL")
        self.runtime_token = os.environ.get("ACTIONS_RUNTIME_TOKEN")
        self.run_id = os.environ.get("GITHUB_RUN_ID")
        self.repo = os.environ.get("GITHUB_REPOSITORY")
        self.github_token = github_token

        if not self.github_token:
             raise click.ClickException("GITHUB_TOKEN is required for GitHubArtifactClient.")

        # Check if we are in a GitHub Action environment and have necessary tokens
        if not self.runtime_url or not self.runtime_token:
            raise click.ClickException(
                "ACTIONS_RUNTIME_URL or ACTIONS_RUNTIME_TOKEN is missing. "
                "To use internal artifact upload, you must expose these variables to the container. "
                "See documentation on how to pass 'ACTIONS_RUNTIME_TOKEN' using 'actions/github-script' "
                "or by mapping the environment."
            )
        
        if not self.repo or not self.run_id:
             raise click.ClickException("GITHUB_REPOSITORY or GITHUB_RUN_ID environment variables missing.")

    def _get_headers(self, content_type="application/json"):
        return {
            "Authorization": f"Bearer {self.runtime_token}",
            "Content-Type": content_type,
            "Accept": "application/json;api-version=6.0-preview"
        }

    def upload_artifact(self, name: str, file_path: Path, retention_days: int = 90) -> str:
        """
        Uploads the artifact and returns the download URL.
        """
        click.echo(f"  📦 Preparing to upload artifact '{name}'...")
        
        # 1. Create the container
        container_url = f"{self.runtime_url}_apis/pipelines/workflows/{self.run_id}/artifacts?api-version=6.0-preview"
        data = {
            "type": "actions_storage",
            "name": name
        }
        
        resp = requests.post(container_url, json=data, headers=self._get_headers())
        if not resp.ok:
            raise click.ClickException(f"Failed to create artifact container: {resp.status_code} {resp.text}")
        
        result = resp.json()
        file_container_resource_url = result.get("fileContainerResourceUrl")
        
        if not file_container_resource_url:
            raise click.ClickException("Failed to get fileContainerResourceUrl from response")

        # 2. Upload the file
        click.echo(f"  ⬆️ Uploading {file_path.name}...")
        file_size = file_path.stat().st_size
        
        with file_path.open("rb") as f:
            # Uploading as a single chunk (simple implementation)
            # For very large files, chunking would be better
            upload_url = f"{file_container_resource_url}?itemPath={name}/{file_path.name}"
            
            headers = self._get_headers(content_type="application/octet-stream")
            headers["Content-Range"] = f"bytes 0-{file_size-1}/{file_size}"
            
            upload_resp = requests.put(upload_url, data=f, headers=headers)
            
            if not upload_resp.ok:
                 raise click.ClickException(f"Failed to upload file content: {upload_resp.status_code} {upload_resp.text}")

        # 3. Patch the size to finalize
        click.echo("  🔒 Finalizing artifact...")
        patch_url = f"{self.runtime_url}_apis/pipelines/workflows/{self.run_id}/artifacts?api-version=6.0-preview&artifactName={name}"
        patch_data = {
            "size": file_size
        }
        
        patch_resp = requests.patch(patch_url, json=patch_data, headers=self._get_headers())
        if not patch_resp.ok:
             raise click.ClickException(f"Failed to finalize artifact: {patch_resp.status_code} {patch_resp.text}")
             
        click.echo(f"  ✅ Artifact '{name}' uploaded successfully.")
        
        # 4. Fetch the download URL
        return self.get_artifact_url(name)

    def get_artifact_url(self, artifact_name: str) -> str:
        """
        Fetches the artifact download URL from the GitHub REST API.
        """  
        api_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/artifacts"
        headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # We might need to wait a moment or retry if it's not immediately consistent
        click.echo("  🔍 Fetching artifact download URL...")
        for _ in range(5):
            resp = requests.get(api_url, headers=headers)
            if resp.ok:
                data = resp.json()
                artifacts = data.get("artifacts", [])
                for artifact in artifacts:
                    if artifact.get("name") == artifact_name:
                        return artifact.get("archive_download_url")
            time.sleep(2)
            
        raise click.ClickException("Could not determine artifact download URL after upload.")
