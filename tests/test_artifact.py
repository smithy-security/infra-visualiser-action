import base64
import json
import os

from unittest.mock import MagicMock, patch, call

import pytest
import click

from infra_visualiser_action.artifact import GitHubArtifactClient


def _make_jwt_token(
    run_backend_id="ce7f54c7-61c7-4aae-887f-30da475f5f1a",
    job_backend_id="ca395085-040a-526b-2ce8-bdc85f692774",
):
    """Build a fake JWT with an Actions.Results scp claim."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload_data = {
        "scp": f"Actions.ExampleScope Actions.Results:{run_backend_id}:{job_backend_id}",
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    signature = "sig"
    return f"{header}.{payload}.{signature}"


FAKE_RUN_BACKEND_ID = "ce7f54c7-61c7-4aae-887f-30da475f5f1a"
FAKE_JOB_BACKEND_ID = "ca395085-040a-526b-2ce8-bdc85f692774"
FAKE_TOKEN = _make_jwt_token(FAKE_RUN_BACKEND_ID, FAKE_JOB_BACKEND_ID)


@pytest.fixture
def mock_env():
    env_vars = {
        "ACTIONS_RUNTIME_TOKEN": FAKE_TOKEN,
        "ACTIONS_RESULTS_URL": "https://results.example.com/",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_missing_token_env():
    env_vars = {
        "ACTIONS_RESULTS_URL": "https://results.example.com/",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars


@pytest.fixture
def mock_missing_results_url_env():
    env_vars = {
        "ACTIONS_RUNTIME_TOKEN": FAKE_TOKEN,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars


# ---------------------------------------------------------------------------
# Init tests
# ---------------------------------------------------------------------------

def test_init_raises_if_github_token_missing(mock_env):
    """__init__ raises ClickException if github_token is empty."""
    with pytest.raises(click.ClickException, match="GITHUB_TOKEN is required"):
        GitHubArtifactClient(github_token="")


def test_init_raises_if_runtime_token_missing(mock_missing_token_env):
    """__init__ raises ClickException if ACTIONS_RUNTIME_TOKEN is missing."""
    with pytest.raises(click.ClickException, match="ACTIONS_RUNTIME_TOKEN is missing"):
        GitHubArtifactClient(github_token="gh_token")


def test_init_raises_if_results_url_missing(mock_missing_results_url_env):
    """__init__ raises ClickException if ACTIONS_RESULTS_URL is missing."""
    with pytest.raises(click.ClickException, match="ACTIONS_RESULTS_URL is missing"):
        GitHubArtifactClient(github_token="gh_token")


def test_init_success(mock_env):
    """Successful initialization with valid JWT and env vars."""
    client = GitHubArtifactClient(github_token="gh_token")
    assert client.results_url == "https://results.example.com"
    assert client.runtime_token == FAKE_TOKEN
    assert client.run_id == "12345"
    assert client.repo == "owner/repo"
    assert client.github_token == "gh_token"
    assert client.backend_ids["workflowRunBackendId"] == FAKE_RUN_BACKEND_ID
    assert client.backend_ids["workflowJobRunBackendId"] == FAKE_JOB_BACKEND_ID


# ---------------------------------------------------------------------------
# JWT decoding tests
# ---------------------------------------------------------------------------

def test_init_raises_if_jwt_has_no_results_scope():
    """ClickException if the JWT has no Actions.Results scope."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"scp": "Actions.SomeOtherScope"}).encode()).rstrip(b"=").decode()
    bad_token = f"{header}.{payload}.sig"

    env_vars = {
        "ACTIONS_RUNTIME_TOKEN": bad_token,
        "ACTIONS_RESULTS_URL": "https://results.example.com/",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars):
        with pytest.raises(click.ClickException, match="Failed to extract backend IDs"):
            GitHubArtifactClient(github_token="gh_token")


def test_init_raises_if_jwt_is_malformed():
    """ClickException if the token is not a valid JWT."""
    env_vars = {
        "ACTIONS_RUNTIME_TOKEN": "not-a-jwt",
        "ACTIONS_RESULTS_URL": "https://results.example.com/",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.dict(os.environ, env_vars):
        with pytest.raises(click.ClickException, match="Failed to extract backend IDs"):
            GitHubArtifactClient(github_token="gh_token")


# ---------------------------------------------------------------------------
# Upload artifact tests (full flow)
# ---------------------------------------------------------------------------

@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_success(mock_requests, mock_env, tmp_path):
    """Test the full v4 upload flow: CreateArtifact -> Blob PUT -> FinalizeArtifact."""
    client = GitHubArtifactClient(github_token="gh_token")

    file_path = tmp_path / "archive.tar.gz"
    file_path.write_bytes(b"terraform-archive-content")

    # Mock CreateArtifact (Twirp POST)
    mock_create_resp = MagicMock()
    mock_create_resp.status_code = 200
    mock_create_resp.json.return_value = {
        "ok": True,
        "signed_upload_url": "https://blob.example.com/upload?sig=abc",
    }

    # Mock FinalizeArtifact (Twirp POST)
    mock_finalize_resp = MagicMock()
    mock_finalize_resp.status_code = 200
    mock_finalize_resp.json.return_value = {
        "ok": True,
        "artifact_id": "99887766",
    }

    # Route Twirp POSTs by URL
    def post_side_effect(url, **kwargs):
        if "CreateArtifact" in url:
            return mock_create_resp
        if "FinalizeArtifact" in url:
            return mock_finalize_resp
        return MagicMock(status_code=404)

    mock_requests.post.side_effect = post_side_effect

    # Mock Blob PUT
    mock_put_resp = MagicMock()
    mock_put_resp.ok = True
    mock_requests.put.return_value = mock_put_resp

    # Execute
    url = client.upload_artifact("my-artifact", file_path)

    # Verify artifact URL is constructed from artifact_id
    assert url == "https://api.github.com/repos/owner/repo/actions/artifacts/99887766/zip"

    # Verify Twirp calls
    assert mock_requests.post.call_count == 2
    create_call_args = mock_requests.post.call_args_list[0]
    assert "CreateArtifact" in create_call_args[0][0]
    assert create_call_args[1]["json"]["name"] == "my-artifact"
    assert create_call_args[1]["json"]["version"] == 4
    assert create_call_args[1]["json"]["workflow_run_backend_id"] == FAKE_RUN_BACKEND_ID
    assert create_call_args[1]["json"]["workflow_job_run_backend_id"] == FAKE_JOB_BACKEND_ID

    finalize_call_args = mock_requests.post.call_args_list[1]
    assert "FinalizeArtifact" in finalize_call_args[0][0]
    assert finalize_call_args[1]["json"]["name"] == "my-artifact"
    assert finalize_call_args[1]["json"]["size"] == str(len(b"terraform-archive-content"))

    # Verify blob upload
    assert mock_requests.put.call_count == 1
    put_call_args = mock_requests.put.call_args
    assert put_call_args[0][0] == "https://blob.example.com/upload?sig=abc"
    assert put_call_args[1]["headers"]["x-ms-blob-type"] == "BlockBlob"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_create_fails(mock_requests, mock_env, tmp_path):
    """ClickException when CreateArtifact returns a non-retryable error."""
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.tar.gz"
    file_path.touch()

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"
    mock_requests.post.return_value = mock_resp

    with pytest.raises(click.ClickException, match="Twirp CreateArtifact failed: 403"):
        client.upload_artifact("test", file_path)


@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_create_returns_not_ok(mock_requests, mock_env, tmp_path):
    """ClickException when CreateArtifact response has ok=false."""
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.tar.gz"
    file_path.touch()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": False}
    mock_requests.post.return_value = mock_resp

    with pytest.raises(click.ClickException, match="CreateArtifact: response from backend was not ok"):
        client.upload_artifact("test", file_path)


@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_blob_upload_fails(mock_requests, mock_env, tmp_path):
    """ClickException when the blob storage PUT fails."""
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.tar.gz"
    file_path.write_bytes(b"content")

    # CreateArtifact succeeds
    mock_create = MagicMock()
    mock_create.status_code = 200
    mock_create.json.return_value = {
        "ok": True,
        "signed_upload_url": "https://blob.example.com/upload?sig=abc",
    }
    mock_requests.post.return_value = mock_create

    # Blob PUT fails
    mock_put = MagicMock()
    mock_put.ok = False
    mock_put.status_code = 403
    mock_put.text = "Forbidden"
    mock_requests.put.return_value = mock_put

    with pytest.raises(click.ClickException, match="Failed to upload file to blob storage"):
        client.upload_artifact("test", file_path)


@patch("infra_visualiser_action.artifact.requests")
def test_upload_artifact_finalize_fails(mock_requests, mock_env, tmp_path):
    """ClickException when FinalizeArtifact returns not ok."""
    client = GitHubArtifactClient(github_token="gh_token")
    file_path = tmp_path / "test.tar.gz"
    file_path.write_bytes(b"content")

    # CreateArtifact succeeds
    mock_create = MagicMock()
    mock_create.status_code = 200
    mock_create.json.return_value = {
        "ok": True,
        "signed_upload_url": "https://blob.example.com/upload?sig=abc",
    }

    # FinalizeArtifact returns not ok
    mock_finalize = MagicMock()
    mock_finalize.status_code = 200
    mock_finalize.json.return_value = {"ok": False}

    def post_side_effect(url, **kwargs):
        if "CreateArtifact" in url:
            return mock_create
        if "FinalizeArtifact" in url:
            return mock_finalize
        return MagicMock(status_code=404)

    mock_requests.post.side_effect = post_side_effect

    # Blob PUT succeeds
    mock_requests.put.return_value = MagicMock(ok=True)

    with pytest.raises(click.ClickException, match="FinalizeArtifact: response from backend was not ok"):
        client.upload_artifact("test", file_path)


# ---------------------------------------------------------------------------
# Twirp retry logic tests
# ---------------------------------------------------------------------------

@patch("infra_visualiser_action.artifact.time.sleep")
@patch("infra_visualiser_action.artifact.requests")
def test_twirp_retries_on_transient_error(mock_requests, mock_sleep, mock_env):
    """_twirp_request retries on 502/503/500 and succeeds on subsequent attempt."""
    client = GitHubArtifactClient(github_token="gh_token")

    mock_fail = MagicMock()
    mock_fail.status_code = 502
    mock_fail.text = "Bad Gateway"

    mock_success = MagicMock()
    mock_success.status_code = 200
    mock_success.json.return_value = {"ok": True, "signed_upload_url": "https://blob.example.com"}

    mock_requests.post.side_effect = [mock_fail, mock_fail, mock_success]

    result = client._twirp_request("CreateArtifact", {"name": "test"})
    assert result == {"ok": True, "signed_upload_url": "https://blob.example.com"}
    assert mock_requests.post.call_count == 3
    assert mock_sleep.call_count == 2


@patch("infra_visualiser_action.artifact.time.sleep")
@patch("infra_visualiser_action.artifact.requests")
def test_twirp_exhausts_retries(mock_requests, mock_sleep, mock_env):
    """_twirp_request raises after exhausting all retry attempts."""
    client = GitHubArtifactClient(github_token="gh_token")

    mock_fail = MagicMock()
    mock_fail.status_code = 503
    mock_fail.text = "Service Unavailable"
    mock_requests.post.return_value = mock_fail

    with pytest.raises(click.ClickException, match="failed after 5 attempts"):
        client._twirp_request("CreateArtifact", {"name": "test"})

    assert mock_requests.post.call_count == 5


# ---------------------------------------------------------------------------
# get_artifact_url tests
# ---------------------------------------------------------------------------

def test_get_artifact_url_with_artifact_id(mock_env):
    """When artifact_id is provided, URL is constructed directly."""
    client = GitHubArtifactClient(github_token="gh_token")
    url = client.get_artifact_url("my-artifact", artifact_id="99887766")
    assert url == "https://api.github.com/repos/owner/repo/actions/artifacts/99887766/zip"


@patch("infra_visualiser_action.artifact.time.sleep")
@patch("infra_visualiser_action.artifact.requests")
def test_get_artifact_url_polls_rest_api(mock_requests, mock_sleep, mock_env):
    """When no artifact_id, polls the REST API and finds artifact by name."""
    client = GitHubArtifactClient(github_token="gh_token")

    mock_get_empty = MagicMock(ok=True)
    mock_get_empty.json.return_value = {"artifacts": []}

    mock_get_success = MagicMock(ok=True)
    mock_get_success.json.return_value = {
        "artifacts": [
            {"name": "test", "archive_download_url": "https://final-url.com/zip"},
        ]
    }

    mock_requests.get.side_effect = [mock_get_empty, mock_get_empty, mock_get_success]

    url = client.get_artifact_url("test")
    assert url == "https://final-url.com/zip"
    assert mock_requests.get.call_count == 3
    assert mock_sleep.call_count == 2


@patch("infra_visualiser_action.artifact.time.sleep")
@patch("infra_visualiser_action.artifact.requests")
def test_get_artifact_url_raises_after_polling_exhausted(mock_requests, mock_sleep, mock_env):
    """Raises ClickException if artifact URL cannot be determined after polling."""
    client = GitHubArtifactClient(github_token="gh_token")

    mock_get_empty = MagicMock(ok=True)
    mock_get_empty.json.return_value = {"artifacts": []}
    mock_requests.get.return_value = mock_get_empty

    with pytest.raises(click.ClickException, match="Could not determine artifact download URL"):
        client.get_artifact_url("nonexistent-artifact")

    assert mock_requests.get.call_count == 5
