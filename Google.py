"""Build an authenticated Google API service, handling OAuth token storage."""

import logging
import os
import pickle

# Required for the localhost http redirect during interactive OAuth.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)


def Create_Service(client_secret_file, api_name, api_version, *scopes):
    scope_list = list(scopes[0])
    pickle_file = f"token_{api_name}_{api_version}.pickle"
    log.info("Creating %s %s service (scopes=%s)", api_name, api_version, scope_list)

    cred = None
    if os.path.exists(pickle_file):
        with open(pickle_file, "rb") as token:
            cred = pickle.load(token)

    if not cred or not cred.valid:
        refreshed = False
        if cred and cred.expired and cred.refresh_token:
            try:
                cred.refresh(Request())
                refreshed = True
                log.info("Refreshed existing OAuth token")
            except Exception as exc:
                if os.getenv("HEADLESS") == "1":
                    raise RuntimeError(
                        "YouTube OAuth token expired. Re-authenticate locally with: "
                        "python3 scripts/reauth_youtube.py"
                    ) from exc
                log.warning("Token refresh failed (%s); starting new login", exc)
                cred = None

        if not refreshed:
            if os.getenv("HEADLESS") == "1" or not os.environ.get("DISPLAY"):
                raise RuntimeError(
                    "No valid YouTube token found. Re-authenticate locally with: "
                    "python3 scripts/reauth_youtube.py"
                )
            if os.path.exists(pickle_file):
                os.remove(pickle_file)
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scope_list)
            cred = flow.run_local_server()

        with open(pickle_file, "wb") as token:
            pickle.dump(cred, token)

    try:
        service = build(api_name, api_version, credentials=cred)
        log.info("%s service created", api_name)
        return service
    except Exception as exc:
        log.error("Unable to build %s service: %s", api_name, exc)
        return None
