"""Durable S3 photo storage (integrations/photo_storage.py) and its two
call sites: app.py's /upload route (saves there when S3 is configured,
falls back to local disk otherwise) and load_current_photo() (reads an
S3-stored photo back for Gemini vision). Photos are served through the
app's own /media/<key> proxy rather than a presigned S3 URL — AWS caps a
presigned URL's lifetime at 7 days (SigV4's hard limit on X-Amz-Expires),
far short of how long a photo needs to stay viewable. Mocks boto3 the
same way test_zoho_ticket_fields.py mocks requests.post — no real AWS
needed.
"""
import io
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
from integrations import photo_storage


def test_is_configured_false_without_bucket(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "")
    assert photo_storage.is_configured() is False


def test_is_configured_true_with_bucket(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    assert photo_storage.is_configured() is True


def test_save_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "")
    assert photo_storage.save(MagicMock(), "png", "image/png") is None


def test_save_uploads_and_returns_our_own_media_path(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "S3_UPLOADS_PREFIX", "astro-conversion-bot/uploads")
    monkeypatch.setattr(photo_storage, "AWS_REGION", "ap-south-1")
    monkeypatch.setattr(photo_storage, "_s3_client", None)

    fake_client = MagicMock()
    file_storage = MagicMock()

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        url = photo_storage.save(file_storage, "png", "image/png")

    # Never a presigned S3 URL — our own app path, proxied through /media.
    assert url.startswith("/media/astro-conversion-bot/uploads/")
    assert url.endswith(".png")
    fake_client.generate_presigned_url.assert_not_called()
    fake_client.upload_fileobj.assert_called_once()
    args, kwargs = fake_client.upload_fileobj.call_args
    assert args[0] is file_storage
    assert args[1] == "astrolokal-uploads"
    assert args[2].startswith("astro-conversion-bot/uploads/")
    assert args[2].endswith(".png")
    assert kwargs["ExtraArgs"] == {"ContentType": "image/png"}


def test_save_raises_on_s3_failure_instead_of_swallowing(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    fake_client = MagicMock()
    fake_client.upload_fileobj.side_effect = RuntimeError("boom")

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        with pytest.raises(RuntimeError):
            photo_storage.save(MagicMock(), "png", "image/png")


def test_get_object_fetches_bytes_and_content_type(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "S3_UPLOADS_PREFIX", "astro-conversion-bot/uploads")

    fake_body = MagicMock()
    fake_body.read.return_value = b"real-image-bytes"
    fake_client = MagicMock()
    fake_client.get_object.return_value = {"Body": fake_body, "ContentType": "image/png"}

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        result = photo_storage.get_object("astro-conversion-bot/uploads/abc.png")

    assert result == (b"real-image-bytes", "image/png")
    fake_client.get_object.assert_called_once_with(
        Bucket="astrolokal-uploads", Key="astro-conversion-bot/uploads/abc.png",
    )


def test_get_object_refuses_a_key_outside_our_own_prefix(monkeypatch):
    # A crafted key must never let a caller read an arbitrary object out
    # of the shared bucket (other prefixes may hold unrelated data).
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "S3_UPLOADS_PREFIX", "astro-conversion-bot/uploads")
    fake_client = MagicMock()

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        assert photo_storage.get_object("some/other/prefix/secret.png") is None

    fake_client.get_object.assert_not_called()


def test_get_object_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "")
    assert photo_storage.get_object("astro-conversion-bot/uploads/abc.png") is None


def test_get_object_returns_none_on_s3_failure(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "S3_UPLOADS_PREFIX", "astro-conversion-bot/uploads")
    fake_client = MagicMock()
    fake_client.get_object.side_effect = RuntimeError("not found")

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        assert photo_storage.get_object("astro-conversion-bot/uploads/missing.png") is None


def test_media_path_for_url_passes_through_an_already_normalized_path():
    assert photo_storage.media_path_for_url("/media/astro-conversion-bot/uploads/abc.png") == (
        "/media/astro-conversion-bot/uploads/abc.png"
    )


def test_media_path_for_url_extracts_the_key_from_an_old_presigned_url(monkeypatch):
    # Old chat history saved before the /media proxy existed still has a
    # full presigned S3 URL baked in (and it's likely expired by now,
    # since AWS caps those at 7 days) — this must still resolve to a key
    # our own proxy can serve, not a dead link.
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "_uploads_host", "astrolokal-uploads.s3.amazonaws.com")

    old_url = (
        "https://astrolokal-uploads.s3.amazonaws.com/astro-conversion-bot/uploads/abc.png"
        "?X-Amz-Signature=expired-long-ago"
    )
    assert photo_storage.media_path_for_url(old_url) == "/media/astro-conversion-bot/uploads/abc.png"


def test_media_path_for_url_leaves_a_foreign_url_unchanged(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "_uploads_host", "astrolokal-uploads.s3.amazonaws.com")

    other_url = "https://evil.example.com/x.png"
    assert photo_storage.media_path_for_url(other_url) == other_url


def test_media_path_for_url_leaves_a_local_disk_path_unchanged(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "")
    assert photo_storage.media_path_for_url("/static/uploads/x.png") == "/static/uploads/x.png"


def test_upload_route_uses_s3_when_configured(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        app_module.photo_storage, "save",
        lambda file, ext, mime_type: "/media/astro-conversion-bot/uploads/x.png",
    )

    client = app_module.app.test_client()
    resp = client.post(
        "/upload", data={"file": (io.BytesIO(b"fake-bytes"), "photo.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert resp.get_json()["url"] == "/media/astro-conversion-bot/uploads/x.png"


def test_upload_route_accepts_heic_and_heif(monkeypatch):
    # iPhone's default camera format — previously rejected outright with
    # "Unsupported file type", a large share of real uploads from iOS
    # since script.js's client-side compression falls back to the
    # original file untouched when the WebView can't decode it.
    monkeypatch.setattr(app_module.photo_storage, "is_configured", lambda: False)
    client = app_module.app.test_client()
    for filename in ("photo.heic", "photo.HEIF"):
        resp = client.post(
            "/upload", data={"file": (io.BytesIO(b"fake-bytes"), filename)},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_json()
        url = resp.get_json()["url"]
        import os
        saved_path = os.path.join(app_module.UPLOAD_FOLDER, url.rsplit("/", 1)[-1])
        assert os.path.exists(saved_path)
        os.remove(saved_path)


def test_upload_route_falls_back_to_local_disk_on_s3_failure(monkeypatch):
    # QA: "the uploaded image often doesn't come through" traced to a
    # real, 100%-reproducible S3 PutObject AccessDenied in production (an
    # IAM policy gap) — previously any S3 exception hard-failed the whole
    # upload with a 502 and no fallback, so every photo share was broken
    # for as long as that gap existed. A real S3 failure must now still
    # succeed via local disk rather than losing the photo outright.
    monkeypatch.setattr(app_module.photo_storage, "is_configured", lambda: True)

    def boom(file, ext, mime_type):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module.photo_storage, "save", boom)

    client = app_module.app.test_client()
    resp = client.post(
        "/upload", data={"file": (io.BytesIO(b"fake-bytes"), "photo.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    url = resp.get_json()["url"]
    assert url.startswith(f"{app_module.app.static_url_path}/uploads/")

    import os
    saved_name = url.rsplit("/", 1)[-1]
    saved_path = os.path.join(app_module.UPLOAD_FOLDER, saved_name)
    assert os.path.exists(saved_path)
    with open(saved_path, "rb") as f:
        assert f.read() == b"fake-bytes"
    os.remove(saved_path)


def test_upload_route_falls_back_to_local_disk_when_s3_not_configured(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "is_configured", lambda: False)

    client = app_module.app.test_client()
    resp = client.post(
        "/upload", data={"file": (io.BytesIO(b"fake-bytes"), "photo.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    url = resp.get_json()["url"]
    assert url.startswith(f"{app_module.app.static_url_path}/uploads/")

    import os
    saved_name = url.rsplit("/", 1)[-1]
    saved_path = os.path.join(app_module.UPLOAD_FOLDER, saved_name)
    assert os.path.exists(saved_path)
    os.remove(saved_path)


def test_load_current_photo_reads_from_our_own_media_path(monkeypatch):
    monkeypatch.setattr(
        app_module.photo_storage, "media_path_for_url",
        lambda url: url,
    )
    monkeypatch.setattr(
        app_module.photo_storage, "get_object",
        lambda key: (b"real-image-bytes", "image/png") if key == "astro-conversion-bot/uploads/abc.png" else None,
    )

    data, mime = app_module.load_current_photo(
        "[Shared a photo: /media/astro-conversion-bot/uploads/abc.png]"
    )

    assert mime == "image/png"
    import base64
    assert base64.b64decode(data) == b"real-image-bytes"


def test_load_current_photo_normalizes_an_old_presigned_url_through_s3(monkeypatch):
    # Old history still holding a (likely expired) presigned URL must
    # still resolve to a key and be read straight from S3, not fetched
    # over HTTP against the stale signed URL.
    old_url = "https://astrolokal-uploads.s3.amazonaws.com/astro-conversion-bot/uploads/abc.png?X-Amz-Signature=old"
    monkeypatch.setattr(
        app_module.photo_storage, "media_path_for_url",
        lambda url: "/media/astro-conversion-bot/uploads/abc.png" if url == old_url else url,
    )
    monkeypatch.setattr(
        app_module.photo_storage, "get_object",
        lambda key: (b"real-image-bytes", "image/png") if key == "astro-conversion-bot/uploads/abc.png" else None,
    )

    data, mime = app_module.load_current_photo(f"[Shared a photo: {old_url}]")

    assert mime == "image/png"
    import base64
    assert base64.b64decode(data) == b"real-image-bytes"


def test_load_current_photo_returns_none_when_object_missing(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "media_path_for_url", lambda url: url)
    monkeypatch.setattr(app_module.photo_storage, "get_object", lambda key: None)

    data, mime = app_module.load_current_photo(
        "[Shared a photo: /media/astro-conversion-bot/uploads/missing.png]"
    )

    assert data is None and mime is None


def test_load_current_photo_rejects_non_image_content_type(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "media_path_for_url", lambda url: url)
    monkeypatch.setattr(
        app_module.photo_storage, "get_object",
        lambda key: (b"<html>not an image</html>", "text/html"),
    )

    data, mime = app_module.load_current_photo(
        "[Shared a photo: /media/astro-conversion-bot/uploads/x.png]"
    )

    assert data is None and mime is None


def test_load_current_photo_ignores_a_url_that_isnt_ours(monkeypatch):
    # media_path_for_url leaves a foreign URL unchanged (not /media/...),
    # so it's never handed to get_object at all.
    monkeypatch.setattr(app_module.photo_storage, "media_path_for_url", lambda url: url)
    get_object = MagicMock()
    monkeypatch.setattr(app_module.photo_storage, "get_object", get_object)

    data, mime = app_module.load_current_photo(
        "[Shared a photo: http://169.254.169.254/latest/meta-data/iam/security-credentials/]"
    )

    get_object.assert_not_called()
    assert data is None and mime is None


def test_serve_photo_route_streams_the_image(monkeypatch):
    monkeypatch.setattr(
        app_module.photo_storage, "get_object",
        lambda key: (b"real-image-bytes", "image/png") if key == "astro-conversion-bot/uploads/abc.png" else None,
    )
    client = app_module.app.test_client()
    resp = client.get("/media/astro-conversion-bot/uploads/abc.png")

    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data == b"real-image-bytes"


def test_serve_photo_route_404s_for_a_missing_key(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "get_object", lambda key: None)
    client = app_module.app.test_client()
    resp = client.get("/media/astro-conversion-bot/uploads/missing.png")

    assert resp.status_code == 404
