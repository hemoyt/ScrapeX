from app.config import settings


def test_no_auth_when_keys_unset(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_auth_enforced_when_keys_set(client, monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "sx-test-key,sx-other")

    # Health is not protected
    assert client.get("/health").status_code == 200

    # API routes require a key
    resp = client.post("/api/v1/social/reddit", json={"subreddit": "python"})
    assert resp.status_code == 401
    assert resp.json()["success"] is False

    # Bearer works (empty body -> 400 validation, not 401; no network hit)
    resp = client.post(
        "/api/v1/social/twitter",
        json={},
        headers={"Authorization": "Bearer sx-test-key"},
    )
    assert resp.status_code == 400

    # X-API-Key works
    resp = client.post(
        "/api/v1/social/twitter",
        json={},
        headers={"X-API-Key": "sx-other"},
    )
    assert resp.status_code == 400

    # Wrong key rejected
    resp = client.post(
        "/api/v1/social/twitter",
        json={},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_error_envelope_shape(client):
    resp = client.post("/api/v1/social/twitter", json={})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert isinstance(body["error"], str)
    assert body["code"] == 400
