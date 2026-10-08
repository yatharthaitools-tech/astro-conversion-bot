"""Uploaded chat photos (payment screenshots, error screens, a face/palm
photo for a reading) — durable S3 storage when configured, local disk
otherwise.

Gated by S3_BUCKET (same env var integrations/s3_client.py already uses
for event logging — shared bucket, separate prefix); unset keeps every
upload on local disk exactly as before. This matters more here than it
did for event logging: a Devtron/Kubernetes pod's filesystem is
ephemeral (same reason dashboard/db.py's conversation storage moved off
SQLite — see that module's own docstring), and with more than one
replica behind a load balancer, a photo saved on the pod that handled
the upload is simply invisible to a later request that lands on a
different pod — a broken-image icon with no error anywhere. Local disk
stays supported for local dev / a genuinely single-replica deployment,
same posture as every other S3_BUCKET-gated integration in this app.
"""
import logging
import os
import uuid

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('S3_BUCKET', '')
S3_UPLOADS_PREFIX = os.environ.get('S3_UPLOADS_PREFIX', 'astro-conversion-bot/uploads').strip('/')
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1')
# Presigned URLs expire rather than being permanently public — these can
# be payment screenshots. Long enough that a CS agent can still open the
# link from a ticket raised days ago, short enough that an old chat-
# history link doesn't stay valid forever.
PRESIGNED_URL_EXPIRY_SECONDS = int(os.environ.get('UPLOAD_URL_EXPIRY_SECONDS', str(7 * 24 * 3600)))

_s3_client = None


def is_configured() -> bool:
    return bool(S3_BUCKET)


def _get_client():
    global _s3_client
    if _s3_client is None:
        import boto3  # imported lazily so boto3 is only required when S3 is actually used
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client


def save(file_storage, ext: str, mime_type: str) -> str:
    """Uploads to S3 and returns a presigned URL the browser can load
    directly — or None if S3 isn't configured, so the caller falls back
    to local disk. Raises on a real S3 failure rather than swallowing it
    (unlike s3_client.log_event's best-effort posture) — app.py's /upload
    route catches this and falls back to local disk for that one upload
    too, but still needs a real exception to know to do that, rather
    than a None indistinguishable from "not configured"."""
    if not is_configured():
        return None
    key = f"{S3_UPLOADS_PREFIX}/{uuid.uuid4().hex}.{ext}"
    client = _get_client()
    client.upload_fileobj(file_storage, S3_BUCKET, key, ExtraArgs={'ContentType': mime_type})
    return client.generate_presigned_url(
        'get_object', Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
    )
