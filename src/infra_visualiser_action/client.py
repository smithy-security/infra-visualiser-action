import tarfile

from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import click
import requests

from infra_visualiser_action.git import get_commit_timestamp

# Directory names that are language/vendor dependency trees; we skip them when finding markdown.
VENDOR_DIR_NAMES = frozenset({
    "vendor",            # Go
    "node_modules",      # Node.js
    ".node_modules",
    "venv",
    ".venv",
    "env",
    ".env",              # Python virtualenvs (avoid .env files dirs if named thus)
    "target",            # Rust/Cargo
    "bower_components",
    ".git",
    "__pycache__",
    ".terraform",        # Terraform/OpenTofu
    ".tofu",
})


def _is_under_vendor_dir(path: Path) -> bool:
    """True if any path component is a known vendor/dependency directory name."""
    return any(part in VENDOR_DIR_NAMES for part in path.parts)


def create_archive(
    repo_root: Path,
    recipe_dir: Path,
    archive_path: Path,
    extra_paths: Iterable[Path] | None = None,
    include_markdown: bool = False,
) -> Path:
    """
    - Adds *.tf, *.json, *.dot under recipe_dir
    - Optionally adds *.md from the repository root when include_markdown is True
    - Adds .terraform/modules/modules.json if present
    - Adds extra_paths if provided
    """
    files_to_add: list[Path] = []

    # All relevant files in recipe_dir
    patterns: list[str] = ["*.tf", "*.json", "*.dot"]

    if include_markdown:
        for p in Path(repo_root).rglob("*.md"):
            if not p.is_file():
                continue
            if _is_under_vendor_dir(p):
                continue
            files_to_add.append(p.resolve())

    for pattern in patterns:
        for p in recipe_dir.glob(pattern):
            if p.is_file():
                files_to_add.append(p.resolve())

    # Extra paths (e.g. local modules)
    if extra_paths:
        for p in extra_paths:
            if not p.exists():
                continue

            if not p.is_dir():
                files_to_add.append(p.resolve())

            for tf_file in p.glob("*.tf"):
                if tf_file.is_file():
                    files_to_add.append(tf_file.resolve())

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for p in files_to_add:
            archive_name = str(p.relative_to(repo_root))
            click.echo(f"Adding file to archive: {p} as path {archive_name}")
            # Store paths relative to repo root for stability
            tar.add(p, arcname=archive_name)

    archive_size = archive_path.stat().st_size
    size_mb = archive_size / (1024 * 1024)
    click.echo(
        f"Archive created: {archive_path} ({size_mb:.2f} MB "
        + "/ {archive_size:,} bytes)"
    )

    return archive_path


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


def notify_server(
    host: str,
    oidc_token: str,
    recipe_path: str,
    recipe_nickname: str,
    artifact_url: str,
) -> None:
    """
    Notifies the server about the available artifact URL.
    """
    commit_ts = get_commit_timestamp()

    data = {
        "commit_timestamp": commit_ts,
        "recipe_path": recipe_path,
        "recipe_nickname": recipe_nickname,
        "archive_url": artifact_url,
    }
    headers = {"Authorization": f"Bearer {oidc_token}"}
    parsed_url = urlparse(host)
    if parsed_url.hostname == "smee.io":
        click.echo(f"Host is smee.io so won't be using own context path but will use as is ({parsed_url.geturl()})")
    else:
        parsed_url = parsed_url._replace(path="/api/v1/notify-terraform-recipe")

    resp = requests.post(
        f"{parsed_url.geturl()}",
        headers=headers,
        json=data,
        timeout=30,
    )

    if not resp.ok:
        raise click.ClickException(
            f"Notification failed with status {resp.status_code}: {resp.text}"
        )
