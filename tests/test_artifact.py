import os

from unittest.mock import MagicMock, patch

import pytest
import click

from infra_visualiser_action.artifact import GitHubArtifactClient

@pytest.fixture
def mock_env():
    env_vars = {
        "ACTIONS_RUNTIME_URL": "https://runtime.example.com/",
        "ACTIONS_RUNTIME_TOKEN": "runtime_token_123",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars

@pytest.fixture
def mock_missing_runtime_env():
    env_vars = {
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    # Ensure runtime vars are NOT present
    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars

def test_init_raises_if_github_token_missing(mock_env):
    """Test that __init__ raises ClickException if github_token is None or empty."""
    with pytest.raises(click.ClickException, match="GITHUB_TOKEN is required"):
        GitHubArtifactClient(github_token="")

def test_init_raises_if_runtime_env_missing(mock_missing_runtime_env):
    """Test that __init__ raises ClickException if runtime vars are missing."""
    with pytest.raises(click.ClickException, match="ACTIONS_RUNTIME_URL or ACTIONS_RUNTIME_TOKEN is missing"):
        GitHubArtifactClient(github_token="gh_token")

def test_init_success(mock_env):
    """Test successful initialization with all required inputs."""
    client = GitHubArtifactClient(github_token="gh_token")
    assert client.runtime_url == "https://runtime.example.com/"
    assert client.runtime_token == "runtime_token_123"
    assert client.run_id == "12345"
    assert client.repo == "owner/repo"
    assert client.github_token == "gh_token"

@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_success(mock_requests, mock_env, tmp_path):
    """Test successful artifact upload flow."""
    # Setup
    client = GitHubArtifactClient(github_token="gh_token")
    
    # Create a dummy file to upload
    file_path = tmp_path / "archive.tar.gz"
    file_path.write_bytes(b"content")
    file_size = file_path.stat().st_size
    
    # Mock responses
    # 1. POST create container
    mock_post_container = MagicMock()
    mock_post_container.ok = True
    mock_post_container.json.return_value = {
        "fileContainerResourceUrl": "https://blob.example.com/container"
    }
    
    # 2. PUT upload file
    mock_put_upload = MagicMock()
    mock_put_upload.ok = True
    
    # 3. PATCH finalize
    mock_patch_finalize = MagicMock()
    mock_patch_finalize.ok = True
    
    # 4. GET artifact URL (polling)
    mock_get_url = MagicMock()
    mock_get_url.ok = True
    mock_get_url.json.return_value = {
        "artifacts": [
            {"name": "other-artifact", "archive_download_url": "http://other.com"},
            {"name": "my-artifact", "archive_download_url": "https://api.github.com/artifact/zip"}
        ]
    }

    # Configure mock side effects for requests calls
    # We need to distinguish calls based on method or args, but simpler to just 
    # assign return values to the mock objects returned by calls if order is strictly sequential
    # or use side_effect with a function to route based on URL.
    
    def request_side_effect(method, url, **kwargs):
        if method == "POST" and "pipelines/workflows" in url:
            return mock_post_container
        if method == "PUT" and "blob.example.com" in url:
            return mock_put_upload
        if method == "PATCH" and "pipelines/workflows" in url:
            return mock_patch_finalize
        if method == "GET" and "api.github.com" in url:
            return mock_get_url
        return MagicMock(ok=False, status_code=404)

    mock_requests.post.side_effect = lambda url, **k: request_side_effect("POST", url, **k)
    mock_requests.put.side_effect = lambda url, **k: request_side_effect("PUT", url, **k)
    mock_requests.patch.side_effect = lambda url, **k: request_side_effect("PATCH", url, **k)
    mock_requests.get.side_effect = lambda url, **k: request_side_effect("GET", url, **k)

    # Execute
    url = client.upload_artifact("my-artifact", file_path)

    # Verify
    assert url == "https://api.github.com/artifact/zip"
    
    # Verify calls
    assert mock_requests.post.call_count == 1
    assert mock_requests.put.call_count == 1
    assert mock_requests.patch.call_count == 1
    assert mock_requests.get.call_count == 1 # Should find it on first try

@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_container_creation_fails(mock_requests, mock_env, tmp_path):
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.txt"
    file_path.touch()

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 500
    mock_resp.text = "Server Error"
    mock_requests.post.return_value = mock_resp

    with pytest.raises(click.ClickException, match="Failed to create artifact container"):
        client.upload_artifact("test", file_path)

@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_file_upload_fails(mock_requests, mock_env, tmp_path):
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.txt"
    file_path.write_text("content")

    # POST succeeds
    mock_post = MagicMock()
    mock_post.ok = True
    mock_post.json.return_value = {"fileContainerResourceUrl": "http://blob"}
    mock_requests.post.return_value = mock_post

    # PUT fails
    mock_put = MagicMock()
    mock_put.ok = False
    mock_put.status_code = 403
    mock_put.text = "Forbidden"
    mock_requests.put.return_value = mock_put

    with pytest.raises(click.ClickException, match="Failed to upload file content"):
        client.upload_artifact("test", file_path)

@patch("infra_visualiser_action.artifact.time.sleep")
@patch("infra_visualiser_action.artifact.requests")
def test_get_artifact_url_retries(mock_requests, mock_sleep, mock_env, tmp_path):
    """Test that get_artifact_url retries if artifact is not found immediately."""
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.txt"
    file_path.write_text("content")

    # Setup successful upload sequence
    mock_post = MagicMock(ok=True)
    mock_post.json.return_value = {"fileContainerResourceUrl": "http://blob"}
    
    mock_put = MagicMock(ok=True)
    
    mock_patch = MagicMock(ok=True)

    # Setup GET sequence: 2 empty responses, then 1 successful
    mock_get_empty = MagicMock(ok=True)
    mock_get_empty.json.return_value = {"artifacts": []}
    
    mock_get_success = MagicMock(ok=True)
    mock_get_success.json.return_value = {
        "artifacts": [{"name": "test", "archive_download_url": "http://final-url"}]
    }

    mock_requests.post.return_value = mock_post
    mock_requests.put.return_value = mock_put
    mock_requests.patch.return_value = mock_patch
    
    # Side effect for GET: fail twice, then succeed
    mock_requests.get.side_effect = [mock_get_empty, mock_get_empty, mock_get_success]

    # Execute
    url = client.upload_artifact("test", file_path)

    assert url == "http://final-url"
    assert mock_requests.get.call_count == 3
