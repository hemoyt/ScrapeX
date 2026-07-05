"""Tests for bring-your-own session cookies (LinkedIn li_at / Instagram sessionid)."""
import pytest
import respx
from httpx import Response

from app.services import runtime_settings


@pytest.fixture(autouse=True)
def _isolate_cookies(tmp_path, monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "settings_file", str(tmp_path / "s.json"))
    runtime_settings._overrides.clear()
    yield
    runtime_settings._overrides.clear()


def test_get_shows_unset_by_default(client):
    body = client.get("/api/v1/settings/cookies").json()
    assert body == {
        "linkedin_cookie_set": False,
        "instagram_sessionid_set": False,
        "instagram_csrftoken_set": False,
    }


def test_set_cookies_never_leaks_value(client):
    body = client.post("/api/v1/settings/cookies", json={
        "linkedin_cookie": "AQEsecret123", "instagram_sessionid": "igsecret456",
    }).json()
    assert body["linkedin_cookie_set"] is True
    assert body["instagram_sessionid_set"] is True
    assert "secret" not in str(body)
    assert runtime_settings.get("linkedin_cookie") == "AQEsecret123"


def test_blank_value_does_not_wipe_existing_cookie(client):
    client.post("/api/v1/settings/cookies", json={"linkedin_cookie": "keep-me"})
    body = client.post("/api/v1/settings/cookies", json={"instagram_sessionid": "new"}).json()
    assert body["linkedin_cookie_set"] is True
    assert runtime_settings.get("linkedin_cookie") == "keep-me"


def test_clear_endpoint_drops_all_cookies(client):
    client.post("/api/v1/settings/cookies", json={
        "linkedin_cookie": "a", "instagram_sessionid": "b", "instagram_csrftoken": "c",
    })
    body = client.post("/api/v1/settings/cookies/clear").json()
    assert body == {
        "linkedin_cookie_set": False,
        "instagram_sessionid_set": False,
        "instagram_csrftoken_set": False,
    }


@respx.mock
def test_linkedin_sends_configured_cookie(client):
    client.post("/api/v1/settings/cookies", json={"linkedin_cookie": "my-li-at-value"})
    route = respx.get("https://www.linkedin.com/in/satyanadella/").mock(
        return_value=Response(200, text="<html><head><title>ok</title></head></html>")
    )
    client.post("/api/v1/social/linkedin", json={"query_type": "profile", "identifier": "satyanadella"})
    assert route.called
    sent_cookie = route.calls[0].request.headers.get("cookie")
    assert sent_cookie == "li_at=my-li-at-value"


@respx.mock
def test_instagram_sends_configured_session_cookie(client):
    client.post("/api/v1/settings/cookies", json={
        "instagram_sessionid": "my-session-id", "instagram_csrftoken": "my-csrf",
    })
    route = respx.get(url__startswith="https://i.instagram.com/api/v1/users/web_profile_info/").mock(
        return_value=Response(200, json={"data": {"user": {"username": "instagram"}}})
    )
    client.post("/api/v1/social/instagram", json={"query_type": "profile", "identifier": "instagram"})
    assert route.called
    headers = route.calls[0].request.headers
    assert "sessionid=my-session-id" in headers.get("cookie")
    assert "csrftoken=my-csrf" in headers.get("cookie")
    assert headers.get("x-csrftoken") == "my-csrf"


@respx.mock
def test_linkedin_error_hints_at_cookie_when_unset(client, monkeypatch):
    from app.services import linkedin_facebook

    async def _none(self, url):
        return None

    monkeypatch.setattr(linkedin_facebook._WalledPlatform, "_render", _none)
    respx.get("https://www.linkedin.com/in/satyanadella/").mock(return_value=Response(999))
    resp = client.post("/api/v1/social/linkedin", json={"query_type": "profile", "identifier": "satyanadella"})
    body = resp.json()
    assert "Session cookies" in body["error"]


@respx.mock
def test_instagram_error_hints_at_cookie_when_unset(client):
    respx.get(url__startswith="https://i.instagram.com/api/v1/users/web_profile_info/").mock(
        return_value=Response(429)
    )
    resp = client.post("/api/v1/social/instagram", json={"query_type": "profile", "identifier": "instagram"})
    body = resp.json()
    assert "sessionid" in body["error"]
