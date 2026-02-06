import os

from pathlib import Path
from unittest import mock

from infra_visualiser_action import git


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
            "infra_visualiser_action.git.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = git.has_terraform_changes_in_paths(candidate_dirs, repo_root)
    
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
            "infra_visualiser_action.git.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = git.has_terraform_changes_in_paths(candidate_dirs, repo_root)

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
            "infra_visualiser_action.git.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = git.has_terraform_changes_in_paths(candidate_dirs, repo_root)

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
            "infra_visualiser_action.git.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
        mock.patch.object(
            Path,
            "exists",
            side_effect=mock_exists,
            autospec=True,
        ),
    ):
        result = git.has_terraform_changes_in_paths(candidate_dirs, repo_root)

    assert result
    mock_check_output.assert_called_once()


def test_has_terraform_changes_in_paths_returns_true_when_recipe_dir_has_changes(tmp_path: Path):
    """Test that function returns True when recipe directory has Terraform file changes"""
    repo_root = tmp_path.resolve()

    # Recipe directory is in candidate directories
    recipe_dir = Path("terraform") / "my-recipe"
    candidate_dirs = [recipe_dir]

    # Mock git diff output with Terraform file in recipe directory
    git_diff_output = "terraform/my-recipe/main.tf\n"

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
            "infra_visualiser_action.git.subprocess.check_output",
            return_value=git_diff_output,
        ) as mock_check_output,
    ):
        result = git.has_terraform_changes_in_paths(candidate_dirs, repo_root)

    assert result
    mock_check_output.assert_called_once()
