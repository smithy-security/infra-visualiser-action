import json
import os
import subprocess
import sys
import tempfile
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click


@dataclass
class PlanAttempt:
    env_label: str
    var_file: Path | None
    success: bool
    log_path: Path


def find_tfvars_files(repo_root: Path) -> list[Path]:
    """
    This function finds all .tfvars files in the repository.
    """
    tfvars_files: list[Path] = []

    # Skip common hidden or irrelevant dirs
    skip_dirs = {
        ".git",
        ".github",
        ".terraform",
        ".venv",
        "venv",
        "__pycache__",
    }

    for root, dirs, files in os.walk(repo_root):
        # Filter out unwanted directories in-place
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if fname.endswith(".tfvars"):
                tfvars_files.append(Path(root) / fname)

    return sorted(tfvars_files)

def _run_init() -> Path:
    backend_file = Path("backend_override.tf")
    backend_file.write_text("""terraform {
    backend "local" { path = "terraform.tfstate" }
}""", encoding="utf-8")
    init_proc = subprocess.run(["tofu", "init",  "-input=false"], check=False, stdout=sys.stdout, stderr=sys.stderr)
    if init_proc.returncode != 0:
        click.echo(f"  ❌ Failed to run tofu init: {init_proc.returncode}", err=True)
        sys.exit(1)

    return backend_file


def _run_plan(env_label: str, extra_args: list[str]) -> PlanAttempt:
    ts = int(time.time())
    safe_label = env_label.replace(os.sep, "_").replace(" ", "_")
    log_path = Path(tempfile.gettempdir()) / f"tofu_plan_{safe_label}_{ts}.log"

    cmd = ["tofu", "plan", "-out=tfplan", "-input=false"] + extra_args
    click.echo(f"  🔍 Running command: {' '.join(cmd)}")
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"$ {' '.join(cmd)}\n\n")
        proc = subprocess.run(
            cmd,
            stdout=log_file,
            stderr=log_file,
            text=True,
        )

    return PlanAttempt(
        env_label=env_label,
        var_file=(
            extra_args[-1]
            if extra_args and extra_args[-1].endswith(".tfvars")
            else None
        ),
        success=proc.returncode == 0,
        log_path=log_path,
    )


def _generate_plan_and_graph(recipe_dir: Path) -> None:
    # Convert tfplan (if exists) to JSON
    tfplan_path = recipe_dir / "tfplan"
    if tfplan_path.exists():
        with (recipe_dir / "tfplan.json").open("w", encoding="utf-8") as f:
            show_cmd = ["tofu", "show", "-json", "tfplan"]
            subprocess.run(show_cmd, check=False, stdout=f, text=True)
    else:
        # Fallback empty plan
        (recipe_dir / "tfplan.json").write_text("{}", encoding="utf-8")

    # Graph & providers schema
    with (recipe_dir / "terraform_graph.dot").open("w", encoding="utf-8") as f:
        subprocess.run(["tofu", "graph"], check=False, stdout=f, text=True)

    with (recipe_dir / "provider_schema.json").open("w", encoding="utf-8") as f:
        subprocess.run(
            ["tofu", "providers", "schema", "-json"],
            check=False,
            stdout=f,
            text=True,
        )


def run_tofu_plans(
    recipe_dir: Path,
    tfvars_files: list[Path],
) -> tuple[list[PlanAttempt], bool]:
    """
    This function changes the working directory into the recipe directory and
    runs tofu plans for all .tfvars files. It returns a list of attempts and a
    boolean indicating if any attempt was successful. All output from the tofu
    plans is logged to a temporary file.
    """

    original_cwd = Path.cwd()
    attempts: list[PlanAttempt] = []

    if not recipe_dir.is_dir():
        raise click.ClickException(f"Recipe directory does not exist: {recipe_dir}")

    planned_attempts: list[tuple[str, list[str]]] = [("defaults", [])]
    planned_attempts.extend(
        (var_file.name, ["-var-file", str(var_file)]) for var_file in tfvars_files
    )

    backend_file = None

    try:
        click.echo(f"  📁 Changing working directory to {recipe_dir}...")
        os.chdir(recipe_dir)

        click.echo(f"  ⚙️ Running tofu init...")
        backend_file = _run_init()

        for env_label, extra_args in planned_attempts:
            click.echo(f"  ⚙️ Running tofu plan for {env_label}...")
            attempt = _run_plan(env_label, extra_args)
            attempts.append(attempt)
            if attempt.success:
                click.echo(f"  ✅ Successfully ran tofu plan for {env_label}")
                _generate_plan_and_graph(recipe_dir=recipe_dir)
                click.echo(f"  📊 Generated plan and graph for {env_label}")
                return attempts, True

    finally:
        if backend_file and backend_file.exists():
            backend_file.unlink()

        click.echo(f"  📁 Changing working directory to original directory {original_cwd}...")
        os.chdir(original_cwd)

    return attempts, False


def find_local_modules_from_modules_json(
    modules_json_path: Path,
    repo_root: Path,
) -> list[Path]:
    """
    Reads modules.json and returns paths to local modules that exist in repo_root.
    The Terraform/OpenTofu modules.json format has a "Modules" list; we treat
    any entry whose "Source" or "Dir" refers to a local path as a local module.
    """
    if not modules_json_path.is_file():
        return []

    data = json.loads(modules_json_path.read_text(encoding="utf-8"))
    modules = data.get("Modules") or data.get("modules") or []

    local_paths: list[Path] = [modules_json_path]

    for m in modules:
        # Try multiple keys used in practice
        source = m.get("Source") or m.get("source")
        module_dir = m.get("Dir") or m.get("dir")

        candidate = None

        # Prefer explicit directory if present
        if module_dir:
            candidate = repo_root / module_dir
            click.echo(f"  ✅ found terraform module: {module_dir}")
        elif source:
            click.echo(f"  🔎 checking if {module_dir} is a local directory")
            # Heuristic: treat relative or ./ paths as local
            if (
                source.startswith("./")
                or source.startswith("../")
                or not any(
                    source.startswith(prefix)
                    for prefix in (
                        "registry.terraform.io/",
                        "github.com/",
                        "git::",
                        "ssh://",
                        "https://",
                    )
                )
            ):
                click.echo(f"  ✅ adding {module_dir} to list of local modules")
                candidate = repo_root / source

        if candidate and candidate.exists():
            local_paths.append(candidate)

    # Deduplicate
    unique_paths = []
    seen = set[Any]()
    for p in local_paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique_paths.append(rp)

    return unique_paths
