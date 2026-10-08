"""Durable S3 photo storage (integrations/photo_storage.py) and its two
call sites: app.py's /upload route (saves there when S3 is configured,
falls back to local disk otherwise) and load_current_photo() (reads an
S3-stored photo back for Gemini vision — only ever from OUR OWN bucket
host, since blindly GETing any URL found in client-supplied text would
be an SSRF hole). Mocks boto3/requests the same way test_zoho_ticket_fields.py
mocks requests.post — no real AWS/network needed.
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


def test_save_uploads_and_returns_presigned_url(monkeypatch):
    monkeypatch.setattr(photo_storage, "S3_BUCKET", "astrolokal-uploads")
    monkeypatch.setattr(photo_storage, "S3_UPLOADS_PREFIX", "astro-conversion-bot/uploads")
    monkeypatch.setattr(photo_storage, "AWS_REGION", "ap-south-1")
    monkeypatch.setattr(photo_storage, "_s3_client", None)

    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = (
        "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/astro-conversion-bot/uploads/abc.png?sig=1"
    )
    file_storage = MagicMock()

    with patch.object(photo_storage, "_get_client", return_value=fake_client):
        url = photo_storage.save(file_storage, "png", "image/png")

    assert url == fake_client.generate_presigned_url.return_value
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


def test_upload_route_uses_s3_when_configured(monkeypatch):
    monkeypatch.setattr(app_module.photo_storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        app_module.photo_storage, "save",
        lambda file, ext, mime_type: "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/x.png?sig=1",
    )

    client = app_module.app.test_client()
    resp = client.post(
        "/upload", data={"file": (io.BytesIO(b"fake-bytes"), "photo.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert resp.get_json()["url"] == "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/x.png?sig=1"


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


def test_load_current_photo_fetches_from_own_s3_bucket(monkeypatch):
    s3_url = "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/astro-conversion-bot/uploads/abc.png?sig=1"
    monkeypatch.setattr(app_module, "_S3_UPLOADS_HOST", "astrolokal-uploads.s3.ap-south-1.amazonaws.com")

    class FakeResponse:
        headers = {"Content-Type": "image/png"}
        content = b"real-image-bytes"

        def raise_for_status(self):
            pass

    with patch.object(app_module.requests, "get", return_value=FakeResponse()) as fake_get:
        data, mime = app_module.load_current_photo(f"[Shared a photo: {s3_url}]")

    fake_get.assert_called_once_with(s3_url, timeout=10)
    assert mime == "image/png"
    import base64
    assert base64.b64decode(data) == b"real-image-bytes"


def test_load_current_photo_refuses_url_from_a_different_host(monkeypatch):
    # The SSRF guard: a crafted marker pointing anywhere other than our
    # own configured upload bucket must never be fetched.
    monkeypatch.setattr(app_module, "_S3_UPLOADS_HOST", "astrolokal-uploads.s3.ap-south-1.amazonaws.com")

    with patch.object(app_module.requests, "get") as fake_get:
        data, mime = app_module.load_current_photo(
            "[Shared a photo: http://169.254.169.254/latest/meta-data/iam/security-credentials/]"
        )

    fake_get.assert_not_called()
    assert data is None and mime is None


def test_load_current_photo_s3_branch_disabled_when_s3_not_configured(monkeypatch):
    monkeypatch.setattr(app_module, "_S3_UPLOADS_HOST", None)

    with patch.object(app_module.requests, "get") as fake_get:
        data, mime = app_module.load_current_photo(
            "[Shared a photo: https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/x.png]"
        )

    fake_get.assert_not_called()
    assert data is None and mime is None


def test_load_current_photo_returns_none_on_fetch_failure(monkeypatch):
    s3_url = "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/x.png?sig=1"
    monkeypatch.setattr(app_module, "_S3_UPLOADS_HOST", "astrolokal-uploads.s3.ap-south-1.amazonaws.com")

    with patch.object(app_module.requests, "get", side_effect=app_module.requests.RequestException("timeout")):
        data, mime = app_module.load_current_photo(f"[Shared a photo: {s3_url}]")

    assert data is None and mime is None


def test_load_current_photo_rejects_non_image_content_type(monkeypatch):
    s3_url = "https://astrolokal-uploads.s3.ap-south-1.amazonaws.com/x.png?sig=1"
    monkeypatch.setattr(app_module, "_S3_UPLOADS_HOST", "astrolokal-uploads.s3.ap-south-1.amazonaws.com")

    class FakeResponse:
        headers = {"Content-Type": "text/html"}
        content = b"<html>not an image</html>"

        def raise_for_status(self):
            pass

    with patch.object(app_module.requests, "get", return_value=FakeResponse()):
        data, mime = app_module.load_current_photo(f"[Shared a photo: {s3_url}]")

    assert data is None and mime is None
