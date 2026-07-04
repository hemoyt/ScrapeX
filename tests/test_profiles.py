"""Tests for the Profile Finder (/api/v1/profiles/find)."""
import respx
from httpx import Response

BSKY_PROFILE = {
    "did": "did:plc:abc",
    "handle": "mkbhd.bsky.social",
    "displayName": "Marques",
    "followersCount": 500000,
    "postsCount": 1200,
}

HN_USER = {"username": "mkbhd", "karma": 1234, "about": "videos", "created_at": "2010-01-01"}


@respx.mock
def test_find_across_selected_platforms(client):
    respx.get(url__startswith="https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile").mock(
        return_value=Response(200, json=BSKY_PROFILE)
    )
    respx.get(url__startswith="https://hn.algolia.com/api/v1/users/mkbhd").mock(
        return_value=Response(200, json=HN_USER)
    )
    resp = client.post(
        "/api/v1/profiles/find",
        json={"username": "@mkbhd", "platforms": ["bluesky", "hackernews"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert sorted(body["found"]) == ["bluesky", "hackernews"]
    assert body["username"] == "mkbhd"
    assert body["results"]["bluesky"]["profile"]["followers"] == 500000
    assert body["results"]["hackernews"]["profile"]["extra"]["karma"] == 1234


@respx.mock
def test_find_reports_misses_honestly(client):
    respx.get(url__startswith="https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile").mock(
        return_value=Response(400, json={"error": "Profile not found"})
    )
    respx.get(url__startswith="https://hn.algolia.com/api/v1/users/").mock(
        return_value=Response(404)
    )
    resp = client.post(
        "/api/v1/profiles/find",
        json={"username": "definitely-not-a-real-handle-xyz", "platforms": ["bluesky", "hackernews"]},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["found"] == []
    assert body["checked"] == ["bluesky", "hackernews"]
    # each miss carries an explanation, not silence
    for r in body["results"].values():
        assert r["success"] is False
        assert r["error"]


def test_find_rejects_bogus_platform_list(client):
    resp = client.post(
        "/api/v1/profiles/find",
        json={"username": "x", "platforms": ["myspace", "friendster"]},
    )
    assert resp.status_code == 400
    assert "Available" in resp.json()["error"]


def test_find_validates_username(client):
    resp = client.post("/api/v1/profiles/find", json={"username": ""})
    assert resp.status_code == 422


def test_identifier_normalization():
    from app.routes.profiles import _identifier_for, profile_capable_platforms

    assert _identifier_for("youtube", "mkbhd") == "@mkbhd"
    assert _identifier_for("youtube", "@mkbhd") == "@mkbhd"
    assert _identifier_for("linkedin", "satya") == "in/satya"
    assert _identifier_for("bluesky", "@bsky.app") == "bsky.app"
    caps = profile_capable_platforms()
    assert "bluesky" in caps and "youtube" in caps
    assert "reddit" not in caps  # reddit has no profile endpoint
