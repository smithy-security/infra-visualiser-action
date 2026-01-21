import os
import subprocess
import tarfile
import time

from pathlib import Path
from typing import Iterable

import click
import requests


def create_archive(
    repo_root: Path,
    recipe_dir: Path,
    archive_path: Path,
    extra_paths: Iterable[Path] | None = None,
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
                files_to_add.append(p.resolve())

    # Extra paths (e.g. local modules)
    if extra_paths:
        click.echo(f"Extra paths to archive are the following: {extra_paths}")
        for p in extra_paths:
            if p.exists():
                files_to_add.append(p)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for p in files_to_add:
            archive_name = str(p.relative_to(repo_root))
            click.echo(f"Adding file to archive: {p} as path {archive_name}")
            # Store paths relative to repo root for stability
            tar.add(p, arcname=archive_name)

    return archive_path


def mark_dir_safe(dir: Path) -> None:
    """
    Mark the given directory as safe to avoid "dubious ownership" errors
    """
    try:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", str(dir)],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        raise click.ClickException(
            f"Could not mark repository as safe: {e}"
        )

def has_terraform_changes_in_paths(
    candidate_dirs: Iterable[Path],
    repo_root: Path,
) -> bool:
    """
    Check the git diff for the current commit/PR and determine whether any
    Terraform-related files (*.tf, *.tfvars) have changed in one of the
    given directories.

    Returns True if at least one of the candidate directories contains a
    changed Terraform-related file, otherwise False.
    """

    sha = os.environ.get("GITHUB_SHA")
    base_sha = os.environ.get("GITHUB_BASE_SHA")

    # Prefer PR base/head SHAs when available, otherwise fall back to last commit
    if base_sha and sha:
        diff_range = f"{base_sha}...{sha}"
    elif sha:
        diff_range = f"{sha}"
    else:
        diff_range = "HEAD~1...HEAD"

    try:
        changed_files_output: str = subprocess.check_output(
            ["git", "diff", "--name-only", diff_range],
            text=True,
            cwd=repo_root,
        )
    except subprocess.CalledProcessError as e:
        # Provide detailed context when git diff fails (e.g. exit code 128)
        stderr = getattr(e, "stderr", "") or ""
        stdout = getattr(e, "output", "") or ""
        msg = (
            "Failed to run 'git diff --name-only' to detect Terraform changes.\n"
            f"Exit code: {e.returncode}\n"
            f"Command: {e.cmd}\n"
            f"Diff range: {diff_range}\n"
        )
        if stdout:
            msg += f"stdout:\n{stdout}\n"
        if stderr:
            msg += f"stderr:\n{stderr}\n"
        raise click.ClickException(msg)

    # we also want to check if the workflow itself changed
    github_workflow_ref: str | None = os.environ.get("GITHUB_WORKFLOW_REF")
    github_workflow_path: str | None = None
    if github_workflow_ref:
        github_workflow_ref = github_workflow_ref.split("@")[0]
        if ".github" in github_workflow_ref:
            github_workflow_ref = github_workflow_ref[github_workflow_ref.index(".github"):]

        if Path(github_workflow_ref).exists():
            github_workflow_path = github_workflow_ref

    terraform_dirs: set[Path] = set()
    for line in changed_files_output.splitlines():
        rel = line.strip()
        if not rel:
            continue

        file_path = (repo_root / rel).resolve().relative_to(repo_root)
        click.echo(f"File path: {str(file_path)} == {github_workflow_path} ? {str(file_path) == github_workflow_path}")
        if str(file_path) == github_workflow_path:
            click.echo(f"GitHub workflow file changed: {file_path}")
            return True

        if file_path.suffix in {".tf", ".tfvars"}:
            terraform_dirs.add(file_path.parent)

    if not terraform_dirs:
        return False

    return len(set(candidate_dirs) & terraform_dirs) > 0


def get_commit_timestamp() -> str:
    """
    Gets the commit timestamp from Git metadata if available.
    """
    sha = os.environ.get("GITHUB_SHA", "unknown")
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")

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
