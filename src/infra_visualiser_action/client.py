import os
import subprocess
import tarfile
import time

from pathlib import Path

import click
import requests


def create_archive(
    recipe_dir: Path,
    archive_path: Path,
    extra_paths: list[Path] | None = None,
) -> Path:
    """
    - Adds *.tf, *.json, *.dot under recipe_dir
    - Adds .terraform/modules/modules.json if present
    - Adds extra_paths if provided
    """
    files_to_add: list[Path] = []

    # All relevant files in recipe_dir
    for pattern in ("*.tf", "*.json", "*.dot"):
        for p in recipe_dir.glob(pattern):
            if p.is_file():
                files_to_add.append(p)

    # modules.json (if exists)
    modules_json = recipe_dir / ".terraform" / "modules" / "modules.json"
    if modules_json.is_file():
        files_to_add.append(modules_json)

    # Extra paths (e.g. local modules)
    if extra_paths:
        for p in extra_paths:
            if p.exists():
                files_to_add.append(p)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for p in files_to_add:
            # Store paths relative to repo root/recipe dir for stability
            tar.add(p, arcname=str(p.relative_to(recipe_dir.parent)))

    return archive_path


def get_commit_timestamp() -> str:
    """
    Gets the commit timestamp from Git metadata if available.
    """
    sha = os.environ.get("GITHUB_SHA", "unknown")
    try:
        commit_ts = subprocess.check_output(
            ["git", "show", "--no-patch", "--format=%ct", sha],
            text=True,
        ).strip()
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(commit_ts)))
    except Exception as e:
        raise click.ClickException(
            f"Failed to get commit timestamp from Git metadata: {e}"
        )


def upload_archive_to_host(
    host: str,
    archive_path: Path,
    oidc_token: str,
    recipe_path: str,
    recipe_nickname: str,
) -> None:
    """
    Uploads the tarball to the given host, using the OIDC token as bearer auth.
    Assumes the host exposes /api/v1/upload-terraform-recipe accepting
    multipart form.
    """
    url = host.rstrip("/")
    commit_ts = get_commit_timestamp()

    with archive_path.open("rb") as f:
        files = {"file": (archive_path.name, f, "application/gzip")}
        data = {
            "commit_timestamp": commit_ts,
            "recipe_path": recipe_path,
            "recipe_nickname": recipe_nickname,
        }
        headers = {"Authorization": f"Bearer {oidc_token}"}

        resp = requests.post(
            f"{url}/api/v1/upload-terraform-recipe",
            headers=headers,
            files=files,
            data=data,
            timeout=300,
        )

        if not resp.ok:
            raise click.ClickException(
                f"Upload failed with status {resp.status_code}: {resp.text}"
            )
