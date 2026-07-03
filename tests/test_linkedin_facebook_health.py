"""LinkedIn/Facebook honest degradation + /health platform matrix."""
import respx
from httpx import Response

LINKEDIN_PUBLIC_HTML = """
<html><head>
<title>Anthropic | LinkedIn</title>
<meta property="og:title" content="Anthropic | LinkedIn"/>
<meta property="og:description" content="AI safety and research company"/>
<meta property="og:image" content="https://media.licdn.com/logo.png"/>
</head><body>public page</body></html>
"""

WALL_HTML = """
<html><head><title>Log In or Sign Up</title></head>
<body><form id="login_form">log in to facebook</form></body></html>
"""


@respx.mock
def test_linkedin_salvages_og_tags(client, monkeypatch):
    # Avoid a real Playwright launch if wall detection misfires
    from app.services import linkedin_facebook
    monkeypatch.setattr(linkedin_facebook._WalledPlatform, "_render", lambda self, url: _none())

    respx.get("https://www.linkedin.com/company/anthropic/").mock(
        return_value=Response(200, text=LINKEDIN_PUBLIC_HTML)
    )
    resp = client.post(
        "/api/v1/social/linkedin",
        json={"query_type": "profile", "identifier": "company/anthropic"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "partial"
    assert body["profile"]["display_name"] == "Anthropic | LinkedIn"
    assert body["profile"]["bio"] == "AI safety and research company"


async def _none():
    return None


@respx.mock
def test_facebook_wall_reports_blocked(client, monkeypatch):
    from app.services import linkedin_facebook
    monkeypatch.setattr(
        linkedin_facebook._WalledPlatform, "_render", lambda self, url: _none()
    )
    respx.get("https://www.facebook.com/nasa/").mock(
        return_value=Response(200, text=WALL_HTML)
    )
    resp = client.post(
        "/api/v1/social/facebook",
        json={"query_type": "profile", "identifier": "nasa"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "blocked"
    assert "login-walls" in body["error"]
    assert body["profile"] is None  # junk wall titles are not profile data


@respx.mock
def test_linkedin_999_bot_block(client, monkeypatch):
    from app.services import linkedin_facebook
    monkeypatch.setattr(linkedin_facebook._WalledPlatform, "_render", lambda self, url: _none())
    respx.get("https://www.linkedin.com/in/satyanadella/").mock(return_value=Response(999))
    resp = client.post(
        "/api/v1/social/linkedin",
        json={"query_type": "profile", "identifier": "satyanadella"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "blocked"


def test_linkedin_posts_unsupported(client):
    resp = client.post(
        "/api/v1/social/linkedin",
        json={"query_type": "posts", "identifier": "someone"},
    )
    body = resp.json()
    assert body["success"] is False
    assert "does not support" in body["error"]


def test_health_platform_matrix(client):
    resp = client.get("/health")
    body = resp.json()
    assert body["status"] == "healthy"
    platforms = body["platforms"]
    for name in ("twitter", "reddit", "bluesky", "hackernews", "mastodon",
                 "youtube", "instagram", "tiktok", "linkedin", "facebook"):
        assert name in platforms, f"missing {name}"
    assert platforms["bluesky"]["search"] == "reliable"
    assert platforms["twitter"]["posts"] == "best_effort"
    assert platforms["linkedin"] == {"profile": "best_effort"}
