"""Gmail API access: OAuth credentials and fetching raw messages."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

from django.conf import settings


class GmailAuthError(RuntimeError):
    pass


def load_credentials(interactive: bool = False):
    """Return valid credentials, refreshing (or, if interactive, re-consenting) as needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_file = settings.GMAIL_TOKEN_FILE
    scopes = settings.GMAIL_SCOPES
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.valid and creds.has_scopes(scopes):
        return creds
    if creds and creds.expired and creds.refresh_token and creds.has_scopes(scopes):
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except RefreshError as exc:
            if not interactive:
                raise GmailAuthError(f"token refresh failed ({exc}); run: manage.py gmail_auth")
    if not interactive:
        raise GmailAuthError("no usable Gmail token; run: manage.py gmail_auth")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(settings.GMAIL_CLIENT_FILE), scopes)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save(creds)
    return creds


def _save(creds) -> None:
    path = settings.GMAIL_TOKEN_FILE
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def build_service(creds=None):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds or load_credentials(), cache_discovery=False)


def list_message_ids(service, query: str) -> Iterator[str]:
    """Yield ids of all messages matching `query`, newest first."""
    token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=token, maxResults=500)
            .execute()
        )
        for m in resp.get("messages", []):
            yield m["id"]
        token = resp.get("nextPageToken")
        if not token:
            return


def fetch_raw(service, gmail_id: str) -> bytes:
    resp = service.users().messages().get(userId="me", id=gmail_id, format="raw").execute()
    return base64.urlsafe_b64decode(resp["raw"])
