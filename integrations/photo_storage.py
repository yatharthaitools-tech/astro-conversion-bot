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
import urllib.parse
import uuid

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('S3_BUCKET', '')
S3_UPLOADS_PREFIX = os.environ.get('S3_UPLOADS_PREFIX', 'astro-conversion-bot/uploads').strip('/')
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1')

_s3_client = None
_uploads_host = None


def is_configured() -> bool:
    return bool(S3_BUCKET)


def _get_client():
    global _s3_client
    if _s3_client is None:
        import boto3  # imported lazily so boto3 is only required when S3 is actually used
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client


def uploads_host() -> str:
    """The real hostname a presigned GET URL for this bucket actually
    has — media_path_for_url() below uses this to recognize an OLD
    presigned marker URL (saved before the /media proxy existed) as
    pointing at OUR bucket, and to pull its S3 key back out. Deliberately
    NOT hardcoded as f"{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com" —
    boto3's virtual-hosted-style endpoint can omit the region entirely
    (confirmed against this exact bucket: the real host is
    astrolokal.s3.amazonaws.com, not astrolokal.s3.ap-south-1.amazonaws.com),
    so that guessed format silently never matched. generate_presigned_url
    is pure local HMAC signing — no network call — so computing a
    throwaway one just to read its host back is free."""
    global _uploads_host
    if _uploads_host is None:
        probe_url = _get_client().generate_presigned_url(
            'get_object', Params={'Bucket': S3_BUCKET, 'Key': '__uploads_host_probe__'}, ExpiresIn=60,
        )
        _uploads_host = urllib.parse.urlparse(probe_url).hostname
    return _uploads_host


def save(file_storage, ext: str, mime_type: str) -> str:
    """Uploads to S3 and returns our OWN app's /media/<key> path — never
    a presigned S3 URL. AWS caps a SigV4 presigned URL's lifetime at 7
    days (X-Amz-Expires can't exceed 604800 seconds) no matter what's
    configured, which is far shorter than how long a photo needs to stay
    viewable (chat history, a support ticket raised against it weeks
    later). Instead the app itself proxies the GET through its own
    long-lived IAM credentials (see /media/<key> in app.py) — no
    client-facing expiry at all. Returns None if S3 isn't configured, so
    the caller falls back to local disk. Raises on a real S3 failure
    rather than swallowing it (unlike s3_client.log_event's best-effort
    posture) — app.py's /upload route catches this and falls back to
    local disk for that one upload too, but still needs a real exception
    to know to do that, rather than a None indistinguishable from "not
    configured"."""
    if not is_configured():
        return None
    key = f"{S3_UPLOADS_PREFIX}/{uuid.uuid4().hex}.{ext}"
    client = _get_client()
    client.upload_fileobj(file_storage, S3_BUCKET, key, ExtraArgs={'ContentType': mime_type})
    return f"/media/{key}"


def get_object(key: str):
    """Fetches the raw bytes + content-type for a key previously
    returned by save() (without its leading '/media/'). Returns
    (bytes, content_type) or None if S3 isn't configured, the key isn't
    one of ours, or the object doesn't exist. Used by app.py's /media
    route (serving to a browser) and load_current_photo (feeding the
    bytes to Gemini vision) — both now read S3 directly instead of
    issuing an HTTP GET against a presigned URL."""
    if not is_configured() or not key.startswith(S3_UPLOADS_PREFIX + '/'):
        return None
    client = _get_client()
    try:
        obj = client.get_object(Bucket=S3_BUCKET, Key=key)
        data = obj['Body'].read()
    except Exception:
        logger.exception('Failed to fetch uploaded photo from S3: %r', key)
        return None
    content_type = obj.get('ContentType') or 'application/octet-stream'
    return data, content_type


def media_path_for_url(url: str) -> str:
    """Normalizes any marker URL to our own /media/<key> app path. Old
    chat history saved before this proxy existed still has the full
    presigned S3 URL (with its now-likely-expired query-string
    signature) baked into the message text — this pulls the S3 key back
    out of that URL's path so even that old history renders through the
    proxy instead of a dead, expired S3 link. A /static/uploads/...
    (local-disk) marker, or anything that isn't one of our own S3 URLs,
    is returned unchanged."""
    if url.startswith('/media/'):
        return url
    if not is_configured():
        return url
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname == uploads_host():
        return f"/media/{parsed.path.lstrip('/')}"
    return url
