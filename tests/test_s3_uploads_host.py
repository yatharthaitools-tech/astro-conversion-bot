"""integrations/photo_storage.py's uploads_host() — the real presigned-URL
hostname for this bucket, used by app.py's load_current_photo() to gate
which URLs it will fetch (an SSRF guard). Regression test for a real
bug: the previous hardcoded f"{bucket}.s3.{region}.amazonaws.com" guess
never matched the actual presigned URL boto3 produces for this bucket —
confirmed live against the real bucket, whose presigned URLs come back
as astrolokal.s3.amazonaws.com (no region segment at all). Mocks boto3's
generate_presigned_url the same way other tests here mock S3 calls — no
real AWS call needed, since presigning is pure local HMAC signing.
"""
from unittest.mock import MagicMock, patch

from integrations import photo_storage


def test_uploads_host_matches_the_real_presigned_url_hostname(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal")
    monkeypatch.setattr(photo_storage, "_s3_client", None)
    monkeypatch.setattr(photo_storage, "_uploads_host", None)

    fake_client = MagicMock()
    # The real, confirmed-live shape: no region segment, even though
    # AWS_REGION is set — this is exactly the case the old hardcoded
    # f"{bucket}.s3.{region}.amazonaws.com" guess got wrong.
    fake_client.generate_presigned_url.return_value = (
        "https://astrolokal.s3.amazonaws.com/astro-conversion-bot/uploads/"
        "__uploads_host_probe__?X-Amz-Signature=abc"
    )

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        host = photo_storage.uploads_host()

    assert host == "astrolokal.s3.amazonaws.com"


def test_uploads_host_is_cached_after_first_call(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal")
    monkeypatch.setattr(photo_storage, "_s3_client", None)
    monkeypatch.setattr(photo_storage, "_uploads_host", None)

    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://astrolokal.s3.amazonaws.com/x?sig=1"

    with patch.object(photo_storage, "_get_client", return_value=fake_client) as get_client:
        photo_storage.uploads_host()
        photo_storage.uploads_host()

    assert get_client.call_count == 1
