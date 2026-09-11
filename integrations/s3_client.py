"""Conversion-funnel event logging to S3, for Redash to query.

Gated by S3_BUCKET: when it's unset, log_event() writes newline-delimited
JSON to a local file instead so the app still works fully without AWS
credentials. Writing an event never raises — a logging failure must never
break the visitor's chat reply, same posture as astrohelp's best-effort
Slack attachment upload.

Layout in S3 (one object per event, so Redash/Athena can query the bucket
directly as a partitioned JSON table):

    s3://{S3_BUCKET}/{S3_PREFIX}/dt=YYYY-MM-DD/{session_id}-{uuid}.json
"""
import datetime
import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('S3_BUCKET', '')
S3_PREFIX = os.environ.get('S3_PREFIX', 'astro-conversion-bot/events').strip('/')
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1')
LOCAL_FALLBACK_PATH = os.environ.get('EVENT_LOG_FALLBACK_PATH', 'logs/events.jsonl')

_s3_client = None


def is_configured() -> bool:
    return bool(S3_BUCKET)


def _get_client():
    global _s3_client
    if _s3_client is None:
        import boto3  # imported lazily so boto3 is only required when S3 is actually used

        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client


def _write_local_fallback(event: dict) -> None:
    os.makedirs(os.path.dirname(LOCAL_FALLBACK_PATH) or '.', exist_ok=True)
    with open(LOCAL_FALLBACK_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')


def log_event(event: dict) -> None:
    """Best-effort. Logs a warning and returns on any failure."""
    payload = dict(event)
    payload.setdefault('timestamp', datetime.datetime.utcnow().isoformat() + 'Z')

    if not is_configured():
        try:
            _write_local_fallback(payload)
        except OSError as exc:
            logger.warning('Failed to write event to local fallback log: %s', exc)
        return

    dt = payload['timestamp'][:10]
    session_id = payload.get('session_id', 'anon')
    key = f"{S3_PREFIX}/dt={dt}/{session_id}-{uuid.uuid4().hex}.json"

    try:
        client = _get_client()
        client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as exc:  # noqa: BLE001 - never let analytics logging break the chat
        logger.warning('Failed to write event to S3 (bucket=%s): %s', S3_BUCKET, exc)
