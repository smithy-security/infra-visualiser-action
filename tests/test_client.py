import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from infra_visualiser_action import client, tf


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


def _attempt(env_label: str, success: bool) -> tf.PlanAttempt:
    return tf.PlanAttempt(
        env_label=env_label,
        var_file=None,
        success=success,
        log_path=Path("/tmp/fake.log"),
    )


def test_run_plans_stops_on_first_success_and_restores_cwd(tmp_path: Path):
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    tfvars_files = [tmp_path / "a.tfvars", tmp_path / "b.tfvars"]

    original_cwd = Path.cwd()

    override_file = recipe_dir / "backend_override.tf"

    run_plan_results = [
        _attempt("defaults", False),
        _attempt("a.tfvars", True),
        _attempt("b.tfvars", True),  # should never be used
    ]

    with (
        mock.patch(
            "infra_visualiser_action.tf._run_init",
            side_effect=[override_file],
        ) as mock_init,
        mock.patch(
            "infra_visualiser_action.tf._run_plan",
            side_effect=run_plan_results,
        ) as mock_run_plan,
        mock.patch(
            "infra_visualiser_action.tf._generate_plan_and_graph"
        ) as mock_gen
    ):
        attempts, any_success = tf.run_plans(
            recipe_dir=recipe_dir, tfvars_files=tfvars_files
        )

    assert mock_init.call_count == 1
    mock_init.assert_called_once_with(use_terraform=False)
    assert not override_file.exists()

    # First success should short-circuit
    assert any_success is True
    assert [a.env_label for a in attempts] == ["defaults", "a.tfvars"]
    assert all(isinstance(a, tf.PlanAttempt) for a in attempts)

    # _run_plan called for defaults then first tfvars only
    assert mock_run_plan.call_count == 2
    mock_run_plan.assert_any_call("defaults", [], use_terraform=False)
    mock_run_plan.assert_any_call("a.tfvars", ["-var-file", str(tfvars_files[0])], use_terraform=False)

    # Graph generation always called once
    mock_gen.assert_called_once_with(recipe_dir=recipe_dir, use_terraform=False)

    # Always restore cwd
    assert Path.cwd() == original_cwd


def test_run_plans_all_fail_returns_all_attempts_and_restores_cwd(tmp_path: Path):
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    tfvars_files = [tmp_path / "a.tfvars", tmp_path / "b.tfvars"]

    original_cwd = Path.cwd()

    override_file = recipe_dir / "backend_override.tf"

    run_plan_results = [
        _attempt("defaults", False),
        _attempt("a.tfvars", False),
        _attempt("b.tfvars", False),
    ]

    with (
        mock.patch(
            "infra_visualiser_action.tf._run_init",
            side_effect=[override_file],
        ) as mock_init,
        mock.patch(
            "infra_visualiser_action.tf._run_plan",
            side_effect=run_plan_results,
        ) as mock_run_plan,
        mock.patch(
            "infra_visualiser_action.tf._generate_plan_and_graph"
        ) as mock_gen
    ):
        attempts, any_success = tf.run_plans(
            recipe_dir=recipe_dir, tfvars_files=tfvars_files
        )

    assert mock_init.call_count == 1
    assert not override_file.exists()

    assert any_success is False
    assert [a.env_label for a in attempts] == ["defaults", "a.tfvars", "b.tfvars"]

    assert mock_run_plan.call_count == 3
    assert mock_gen.call_count == 0
    assert Path.cwd() == original_cwd


def test_run_plans_raises_if_recipe_dir_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist"

    with pytest.raises(Exception) as exc:
        tf.run_plans(recipe_dir=missing, tfvars_files=[])

    assert "Recipe directory does not exist" in str(exc.value)


def test_run_plans_uses_terraform_when_flag_is_set(tmp_path: Path):
    """Test that run_plans uses terraform binary when use_terraform=True"""
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    tfvars_files = []

    original_cwd = Path.cwd()

    override_file = recipe_dir / "backend_override.tf"

    run_plan_results = [
        _attempt("defaults", True),
    ]

    with (
        mock.patch(
            "infra_visualiser_action.tf._run_init",
            side_effect=[override_file],
        ) as mock_init,
        mock.patch(
            "infra_visualiser_action.tf._run_plan",
            side_effect=run_plan_results,
        ) as mock_run_plan,
        mock.patch(
            "infra_visualiser_action.tf._generate_plan_and_graph"
        ) as mock_gen
    ):
        attempts, any_success = tf.run_plans(
            recipe_dir=recipe_dir, tfvars_files=tfvars_files, use_terraform=True
        )

    assert mock_init.call_count == 1
    mock_init.assert_called_once_with(use_terraform=True)
    assert not override_file.exists()

    assert any_success is True
    assert [a.env_label for a in attempts] == ["defaults"]

    mock_run_plan.assert_called_once_with("defaults", [], use_terraform=True)
    mock_gen.assert_called_once_with(recipe_dir=recipe_dir, use_terraform=True)

    assert Path.cwd() == original_cwd


def test_find_local_modules_from_modules_json_returns_empty_if_file_not_exists(tmp_path: Path):
    """Test that non-existent modules.json returns empty list"""
    modules_json = tmp_path / "modules.json"
    repo_root = tmp_path

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == []


def test_find_local_modules_from_modules_json_returns_empty_if_no_modules(tmp_path: Path):
    """Test that empty modules list returns empty list"""
    modules_json = tmp_path / "modules.json"
    modules_json.write_text(json.dumps({"Modules": []}), encoding="utf-8")
    repo_root = tmp_path

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == set([modules_json])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), local_module_dir.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), local_module.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert result == set([modules_json.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), local_module.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), existing_module.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), local_module.resolve()])


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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 2
    assert result == set([modules_json.resolve(), local_module.resolve()])

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

    result = tf.find_local_modules_from_modules_json(modules_json, repo_root)
    assert len(result) == 3
    resolved_paths = {p.resolve() for p in result}
    assert resolved_paths == {
        modules_json.resolve(),
        module1.resolve(),
        module2.resolve()
    }


def test_has_terraform_changes_in_paths_returns_false_when_no_terraform_files_changed(tmp_path: Path):
    """Test that function returns False when diff contains no Terraform files"""
    repo_root = tmp_path.resolve()
    candidate_dirs = [tmp_path / "terraform" / "app", tmp_path / "terraform" / "network"]
    
    # Mock git diff output with non-terraform files
    git_diff_output = "src/main.py\nREADME.md\nconfig.yaml\n"
    
    with (
        mock.patch.dict(
            os.environ,
            {
                "GITHUB_SHA": "abc123",
                "GITHUB_WORKFLOW_REF": "octocat/hello-world/.github/workflows/my-workflow.yml@refs/heads/my_branch",
            },
            clear=False
        ),
        mock.patch(
            "infra_visualiser_action.client.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = client.has_terraform_changes_in_paths(candidate_dirs, repo_root)
    
    assert not result
    # Verify git diff was called with correct arguments
    mock_check_output.assert_called_once()
    call_args = mock_check_output.call_args[0][0]  # First positional arg is the command list
    assert call_args[0] == "git"
    assert "diff" in call_args
    assert "--name-only" in call_args


def test_has_terraform_changes_in_paths_returns_false_when_terraform_changes_not_in_candidate_dirs(tmp_path: Path):
    """Test that function returns False when Terraform files changed but not in candidate directories"""
    repo_root = tmp_path.resolve()
    
    # Create candidate directories (but no changes will be in these)
    candidate_dirs = [tmp_path / "terraform" / "app", tmp_path / "terraform" / "network"]
    
    # Mock git diff output with Terraform files in different directories
    git_diff_output = "other-terraform/module1/main.tf\nother-terraform/module2/variables.tfvars\n"

    with (
        mock.patch.dict(
            os.environ,
            {
                "GITHUB_SHA": "abc123",
                "GITHUB_WORKFLOW_REF": "octocat/hello-world/.github/workflows/my-workflow.yml@refs/heads/my_branch",
            },
            clear=False
        ),
        mock.patch(
            "infra_visualiser_action.client.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = client.has_terraform_changes_in_paths(candidate_dirs, repo_root)

    assert not result
    mock_check_output.assert_called_once()


def test_has_terraform_changes_in_relevant_paths_returns_true(tmp_path: Path):
    """Test that function returns True when Terraform files changed in candidate directories"""
    repo_root = tmp_path.resolve()

    # Create candidate directories (but no changes will be in these)
    candidate_dirs = [
        Path("terraform") / "app",
        Path("terraform") / "module1",
        Path("terraform") / "network"
    ]

    # Mock git diff output with Terraform files in different directories
    git_diff_output = "terraform/module1/main.tf\nother-terraform/module2/variables.tfvars\n"

    with (
        mock.patch.dict(
            os.environ,
            {
                "GITHUB_SHA": "abc123",
                "GITHUB_WORKFLOW_REF": "octocat/hello-world/.github/workflows/my-workflow.yml@refs/heads/my_branch",
            },
            clear=False
        ),
        mock.patch(
            "infra_visualiser_action.client.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = client.has_terraform_changes_in_paths(candidate_dirs, repo_root)

    assert result
    mock_check_output.assert_called_once()


def test_has_no_terraform_changes_but_workflow_changed(tmp_path: Path):
    """Test that function returns True when Terraform files changed in candidate directories"""
    repo_root = tmp_path.resolve()

    # Create candidate directories (but no changes will be in these)
    candidate_dirs = [
        Path("terraform") / "app",
        Path("terraform") / "module1",
        Path("terraform") / "network"
    ]

    # Mock git diff output with Terraform files in different directories
    git_diff_output = "other-terraform/module1/main.tf\nother-terraform/module2/variables.tfvars\n.github/workflows/my-workflow.yml\n"

    def mock_exists(self: Path) -> bool:
        """Mock Path.exists() to return True only for the workflow path"""
        return str(self) == ".github/workflows/my-workflow.yml"

    with (
        mock.patch.dict(
            os.environ,
            {
                "GITHUB_SHA": "abc123",
                "GITHUB_WORKFLOW_REF": "octocat/hello-world/.github/workflows/my-workflow.yml@refs/heads/my_branch",
            },
            clear=False
        ),
        mock.patch(
            "infra_visualiser_action.client.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
        mock.patch.object(
            Path,
            "exists",
            side_effect=mock_exists,
            autospec=True,
        ),
    ):
        result = client.has_terraform_changes_in_paths(candidate_dirs, repo_root)

    assert result
    mock_check_output.assert_called_once()


def test_create_archive_includes_matching_files_from_recipe_dir(tmp_path: Path):
    """Test that create_archive includes *.tf, *.json, *.dot files from recipe_dir"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe" / "nested"
    recipe_dir.mkdir(parents=True)
    
    # Create matching files
    (recipe_dir / "main.tf").write_text("terraform content")
    (recipe_dir / "variables.tf").write_text("variables content")
    (recipe_dir / "config.json").write_text('{"key": "value"}')
    (recipe_dir / "graph.dot").write_text("digraph G {}")
    
    # Create non-matching file (should be excluded)
    (recipe_dir / "README.md").write_text("readme content")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    result = client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
    )
    
    assert result == archive_path
    assert archive_path.exists()
    
    # Verify archive contents
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    expected_files = set([
        "recipe/nested/main.tf",
        "recipe/nested/variables.tf",
        "recipe/nested/config.json",
        "recipe/nested/graph.dot",
    ])
    assert members.issuperset(expected_files), members.difference(expected_files)


def test_create_archive_includes_extra_paths_as_files(tmp_path: Path):
    """Test that extra_paths files are included in archive"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    extra_file = repo_root / "extra" / "file.json"
    extra_file.parent.mkdir()
    extra_file.write_text('{"extra": "data"}')
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[extra_file],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members =   set([member.name for member in tar.getmembers()])
    
    assert set(["recipe/main.tf", "extra/file.json"]) == set(members)


def test_create_archive_includes_extra_paths_as_directories(tmp_path: Path):
    """Test that extra_paths directories recursively include *.tf files"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    # Create extra directory with nested .tf files
    extra_dir = repo_root / "modules" / "local-module"
    extra_dir.mkdir(parents=True)
    (extra_dir / "main.tf").write_text("module content")
    (extra_dir / "variables.tf").write_text("module vars")
    (extra_dir / "nested").mkdir()
    (extra_dir / "nested" / "sub.tf").write_text("nested content")
    
    # Non-matching file in extra_dir (should not be included)
    (extra_dir / "README.md").write_text("readme")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[extra_dir],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    assert set(["recipe/main.tf", "modules/local-module/main.tf", "modules/local-module/variables.tf", "modules/local-module/nested/sub.tf"]) == set(members)


def test_create_archive_skips_nonexistent_extra_paths(tmp_path: Path):
    """Test that non-existent extra_paths are skipped"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    nonexistent = repo_root / "nonexistent" / "file.tf"
    existing = repo_root / "existing.tf"
    existing.write_text("existing content")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[nonexistent, existing],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    assert set(["recipe/main.tf", "existing.tf"]) == members
