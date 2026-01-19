import os

import click
import requests


def get_oidc_token_for_host(host: str) -> str:
    """
    Uses GitHub's OIDC endpoint inside Actions to get a token with the given
    audience.
    Requires:
      - ACTIONS_ID_TOKEN_REQUEST_URL
      - ACTIONS_ID_TOKEN_REQUEST_TOKEN
    """
    req_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    req_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")

    if not req_url or not req_token:
        raise click.ClickException(
            "ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN "
            + "must be set in GitHub Actions."
        )

    # Ensure audience param is appended
    if "audience=" in req_url:
        url = req_url
    else:
        sep = "&" if "?" in req_url else "?"
        url = f"{req_url}{sep}audience={host}"

    resp = requests.get(
        url,
        headers={"Authorization": f"bearer {req_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("value")
    if not token:
        raise click.ClickException("Failed to obtain OIDC token from GitHub.")

    return token
