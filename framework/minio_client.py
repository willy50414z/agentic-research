"""
framework/minio_client.py

MinIO client wrapper for artifact storage.

Two buckets:
  planka-attachments  — Planka S3 backend (managed by Planka; framework reads via attachment API)
  research-artifacts  — framework artifacts uploaded directly (reports, models, etc.)

Usage:
  key = upload_artifact("proj-1", 3, "report.html", data, "text/html")
  # returns "minio://research-artifacts/proj-1/loop_3/report.html"

  data = download_artifact("minio://research-artifacts/proj-1/loop_3/report.html")
"""

import logging
import os
from io import BytesIO

logger = logging.getLogger(__name__)


def _get_client():
    from minio import Minio
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    # Strip http:// or https:// prefix — Minio SDK takes host:port only
    endpoint = endpoint.replace("http://", "").replace("https://", "")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
    return Minio(
        endpoint,
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=secure,
    )


def upload_artifact(
    project_id: str,
    loop_index: int,
    name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload a research artifact to MinIO.

    Returns the minio:// path string, e.g.:
        "minio://research-artifacts/proj-1/loop_3/report.html"
    """
    bucket = os.getenv("MINIO_ARTIFACTS_BUCKET", "research-artifacts")
    key = f"{project_id}/loop_{loop_index}/{name}"
    try:
        client = _get_client()
        _ensure_bucket(client, bucket)
        client.put_object(
            bucket, key, BytesIO(data), len(data), content_type=content_type
        )
        path = f"minio://{bucket}/{key}"
        logger.debug("Uploaded artifact: %s", path)
        return path
    except Exception as e:
        logger.warning("MinIO upload failed (%s/%s): %s", bucket, key, e)
        return f"minio://{bucket}/{key}"  # return path even on failure for DB record


def download_artifact(minio_path: str) -> bytes:
    """
    Download artifact bytes from a minio:// path.

    Raises on failure (caller must handle).
    """
    path = minio_path.replace("minio://", "")
    bucket, key = path.split("/", 1)
    client = _get_client()
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _ensure_bucket(client, bucket: str) -> None:
    """Create bucket if it does not exist. Non-blocking."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Created MinIO bucket: %s", bucket)
    except Exception as e:
        logger.warning("Could not ensure MinIO bucket '%s': %s", bucket, e)
