import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from infra_visualiser_action import client, tofu


def test_get_commit_timestamp_uses_git_and_formats_iso():
    """
    Ensure get_commit_timestamp:
    - calls `git show --no-patch --format=%ct <sha>`
    - converts the UNIX timestamp to an ISO-like string in UTC
    """
    fake_sha = "7e194e274c4adc8d2f626b9051739f7b54b17467"
    fake_unix_ts = "1700000000"  # 2023-11-14T22:13:20Z

    with (
        mock.patch.dict(
            os.environ,
            {"GITHUB_SHA": fake_sha},
            clear=False
        ),
        mock.patch(
            "infra_visualiser_action.client.subprocess.check_output",
            return_value=fake_unix_ts,
        ) as mock_check_output,
        mock.patch(
            "infra_visualiser_action.client.time.gmtime",
            return_value=datetime.fromtimestamp(int(fake_unix_ts), tz=timezone.utc).timetuple(),
        )
    ):
        ts = client.get_commit_timestamp()

    # Assert git was called with the expected arguments
    mock_check_output.assert_called_once_with(
        ["git", "show", "--no-patch", "--format=%ct", fake_sha],
        text=True,
    )

    assert ts == "2023-11-14T22:13:20"


def _attempt(env_label: str, success: bool) -> tofu.PlanAttempt:
    return tofu.PlanAttempt(
        env_label=env_label,
        var_file=None,
        success=success,
        log_path=Path("/tmp/fake.log"),
    )


def test_run_tofu_plans_stops_on_first_success_and_restores_cwd(tmp_path: Path):
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    tfvars_files = [tmp_path / "a.tfvars", tmp_path / "b.tfvars"]

    original_cwd = Path.cwd()

    run_plan_results = [
        _attempt("defaults", False),
        _attempt("a.tfvars", True),
        _attempt("b.tfvars", True),  # should never be used
    ]

    with (
        mock.patch(
            "infra_visualiser_action.tofu._run_plan",
            side_effect=run_plan_results,
        ) as mock_run_plan,
        mock.patch(
            "infra_visualiser_action.tofu._generate_plan_and_graph"
        ) as mock_gen
    ):
        attempts, any_success = tofu.run_tofu_plans(
            recipe_dir=recipe_dir, tfvars_files=tfvars_files
        )

    # First success should short-circuit
    assert any_success is True
    assert [a.env_label for a in attempts] == ["defaults", "a.tfvars"]
    assert all(isinstance(a, tofu.PlanAttempt) for a in attempts)

    # _run_plan called for defaults then first tfvars only
    assert mock_run_plan.call_count == 2
    mock_run_plan.assert_any_call("defaults", [])
    mock_run_plan.assert_any_call("a.tfvars", ["-var-file", str(tfvars_files[0])])

    # Graph generation always called once
    mock_gen.assert_called_once_with(recipe_dir=recipe_dir)

    # Always restore cwd
    assert Path.cwd() == original_cwd


def test_run_tofu_plans_all_fail_returns_all_attempts_and_restores_cwd(tmp_path: Path):
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    tfvars_files = [tmp_path / "a.tfvars", tmp_path / "b.tfvars"]

    original_cwd = Path.cwd()

    run_plan_results = [
        _attempt("defaults", False),
        _attempt("a.tfvars", False),
        _attempt("b.tfvars", False),
    ]

    with (
        mock.patch(
            "infra_visualiser_action.tofu._run_plan",
            side_effect=run_plan_results,
        ) as mock_run_plan,
        mock.patch(
            "infra_visualiser_action.tofu._generate_plan_and_graph"
        ) as mock_gen
    ):
        attempts, any_success = tofu.run_tofu_plans(
            recipe_dir=recipe_dir, tfvars_files=tfvars_files
        )

    assert any_success is False
    assert [a.env_label for a in attempts] == ["defaults", "a.tfvars", "b.tfvars"]

    assert mock_run_plan.call_count == 3
    assert mock_gen.call_count == 0
    assert Path.cwd() == original_cwd


def test_run_tofu_plans_raises_if_recipe_dir_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist"

    with pytest.raises(Exception) as exc:
        tofu.run_tofu_plans(recipe_dir=missing, tfvars_files=[])

    assert "Recipe directory does not exist" in str(exc.value)


def test_find_local_modules_from_modules_json_returns_empty_if_file_not_exists(tmp_path: Path):
    """Test that non-existent modules.json returns empty list"""
    modules_json = tmp_path / "modules.json"
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == []


def test_find_local_modules_from_modules_json_returns_empty_if_no_modules(tmp_path: Path):
    """Test that empty modules list returns empty list"""
    modules_json = tmp_path / "modules.json"
    modules_json.write_text(json.dumps({"Modules": []}), encoding="utf-8")
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == []


def test_find_local_modules_from_modules_json_prefers_dir_over_source(tmp_path: Path):
    """Test that 'Dir' key is preferred over 'Source' key"""
    # Create local module directories
    local_module_dir = tmp_path / "modules" / "local-module"
    local_module_dir.mkdir(parents=True)
    (local_module_dir / "main.tf").touch()

    source_module_dir = tmp_path / "modules" / "source-module"
    source_module_dir.mkdir(parents=True)
    (source_module_dir / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {
                    "Dir": "modules/local-module",
                    "Source": "modules/source-module",  # Should be ignored
                }
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == local_module_dir.resolve()


def test_find_local_modules_from_modules_json_uses_source_with_relative_paths(tmp_path: Path):
    """Test that 'Source' with relative paths (./ or ../) is treated as local"""
    local_module = tmp_path / "modules" / "my-module"
    local_module.mkdir(parents=True)
    (local_module / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Source": "./modules/my-module"},
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == local_module.resolve()


def test_find_local_modules_from_modules_json_ignores_remote_sources(tmp_path: Path):
    """Test that remote sources (registry, git, etc.) are ignored"""
    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Source": "registry.terraform.io/hashicorp/aws"},
                {"Source": "github.com/hashicorp/terraform-aws-modules"},
                {"Source": "git::https://github.com/example/module.git"},
                {"Source": "ssh://git@github.com/example/module.git"},
                {"Source": "https://github.com/example/module.git"},
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == []


def test_find_local_modules_from_modules_json_treats_non_remote_as_local(tmp_path: Path):
    """Test that sources without remote prefixes are treated as local"""
    local_module = tmp_path / "custom-module"
    local_module.mkdir(parents=True)
    (local_module / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Source": "custom-module"},  # No ./ or remote prefix
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == local_module.resolve()


def test_find_local_modules_from_modules_json_filters_nonexistent_paths(tmp_path: Path):
    """Test that only existing paths are returned"""
    existing_module = tmp_path / "existing"
    existing_module.mkdir(parents=True)
    (existing_module / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Dir": "existing"},
                {"Dir": "nonexistent"},  # Should be filtered out
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == existing_module.resolve()


def test_find_local_modules_from_modules_json_deduplicates_paths(tmp_path: Path):
    """Test that duplicate paths are deduplicated"""
    local_module = tmp_path / "modules" / "my-module"
    local_module.mkdir(parents=True)
    (local_module / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Dir": "modules/my-module"},
                {"Source": "./modules/my-module"},  # Same module, different key
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == local_module.resolve()


def test_find_local_modules_from_modules_json_supports_lowercase_keys(tmp_path: Path):
    """Test that lowercase 'modules', 'source', 'dir' keys are supported"""
    local_module = tmp_path / "modules" / "my-module"
    local_module.mkdir(parents=True)
    (local_module / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "modules": [  # lowercase
                {
                    "dir": "modules/my-module",  # lowercase
                    "source": "./modules/my-module",  # lowercase
                }
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 1
    assert result[0] == local_module.resolve()


def test_find_local_modules_from_modules_json_handles_multiple_local_modules(tmp_path: Path):
    """Test finding multiple local modules"""
    module1 = tmp_path / "modules" / "module1"
    module1.mkdir(parents=True)
    (module1 / "main.tf").touch()

    module2 = tmp_path / "modules" / "module2"
    module2.mkdir(parents=True)
    (module2 / "main.tf").touch()

    modules_json = tmp_path / "modules.json"
    modules_json.write_text(
        json.dumps({
            "Modules": [
                {"Dir": "modules/module1"},
                {"Dir": "modules/module2"},
            ]
        }),
        encoding="utf-8",
    )
    repo_root = tmp_path

    result = tofu.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    resolved_paths = {p.resolve() for p in result}
    assert resolved_paths == {module1.resolve(), module2.resolve()}
