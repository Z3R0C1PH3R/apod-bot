"""Upload generated videos to Azure Blob Storage for public (Instagram) access."""

import logging
import os

from azure.core.exceptions import AzureError
from azure.storage.blob import BlobServiceClient, ContentSettings

from utils import retry

log = logging.getLogger(__name__)


@retry(exceptions=(AzureError, OSError), tries=4, delay=5.0)
def upload_video(local_path: str, blob_name: str) -> str:
    """Upload a local MP4 and return its public blob URL."""
    conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    container = os.getenv("AZURE_STORAGE_CONTAINER", "videos")

    client = BlobServiceClient.from_connection_string(conn)
    blob = client.get_blob_client(container=container, blob=blob_name)

    log.info("Uploading %s -> container '%s' as '%s'", local_path, container, blob_name)
    with open(local_path, "rb") as video_file:
        blob.upload_blob(
            video_file,
            overwrite=True,
            content_settings=ContentSettings(content_type="video/mp4"),
        )
    log.info("Uploaded to %s", blob.url)
    return blob.url
