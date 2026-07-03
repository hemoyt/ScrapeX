"""Instagram + TikTok parser and degradation tests."""
import json

import respx
from httpx import Response

IG_PROFILE = {
    "data": {"user": {
        "username": "instagram",
        "full_name": "Instagram",
        "biography": "Bringing you closer",
        "is_private": False,
        "is_verified": True,
        "profile_pic_url_hd": "https://scontent.cdninstagram.com/avatar.jpg",
        "edge_followed_by": {"count": 685848209},
        "edge_follow": {"count": 100},
        "edge_owner_to_timeline_media": {
            "count": 8511,
            "edges": [
                {"node": {
                    "id": "1",
                    "shortcode": "DaTSSukB-Lb",
                    "is_video": True,
                    "display_url": "https://scontent.cdninstagram.com/p1.jpg",
                    "taken_at_timestamp": 1751500000,
                    "video_view_count": 999,
                    "edge_liked_by": {"count": 371450},
                    "edge_media_to_comment": {"count": 3000},
                    "edge_media_to_caption": {"edges": [{"node": {"text": "this glow >>>"}}]},
                }},
            ],
        },
    }},
}


@respx.mock
def test_instagram_profile_and_posts(client):
    respx.get(url__startswith="https://i.instagram.com/api/v1/users/web_profile_info/").mock(
        return_value=Response(200, json=IG_PROFILE)
    )
    resp = client.post(
        "/api/v1/social/instagram",
        json={"query_type": "profile", "identifier": "@instagram"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["profile"]["followers"] == 685848209
    assert body["profile"]["verified"] is True

    resp = client.post(
        "/api/v1/social/instagram",
        json={"query_type": "posts", "identifier": "instagram"},
    )
    body = resp.json()
    post = body["posts"][0]
    assert post["text"] == "this glow >>>"
    assert post["stats"] == {"likes": 371450, "comments": 3000, "views": 999}
    assert post["url"] == "https://www.instagram.com/p/DaTSSukB-Lb/"


@respx.mock
def test_instagram_429_reports_blocked(client):
    respx.get(url__startswith="https://i.instagram.com/api/v1/users/web_profile_info/").mock(
        return_value=Response(429)
    )
    resp = client.post(
        "/api/v1/social/instagram",
        json={"query_type": "profile", "identifier": "instagram"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "blocked"
    assert "rate-limiting" in body["error"]
    assert body["posts"] == []


@respx.mock
def test_instagram_private_account_blocked(client):
    private = json.loads(json.dumps(IG_PROFILE))
    private["data"]["user"]["is_private"] = True
    respx.get(url__startswith="https://i.instagram.com/api/v1/users/web_profile_info/").mock(
        return_value=Response(200, json=private)
    )
    resp = client.post(
        "/api/v1/social/instagram",
        json={"query_type": "posts", "identifier": "someone"},
    )
    body = resp.json()
    assert body["status"] == "blocked"
    assert "private" in body["error"]


IG_EMBED_HTML = """
<html><body>
<div class="EmbeddedMedia">
  <img class="EmbeddedMediaImage" src="https://scontent.cdninstagram.com/p1.jpg"/>
  <div class="Caption">
    <a class="CaptionUsername"><span class="UsernameText">instagram</span></a>
    this glow &gt;&gt;&gt;
  </div>
</div>
</body></html>
"""


@respx.mock
def test_instagram_single_post_embed(client):
    respx.get("https://www.instagram.com/p/DaTSSukB-Lb/embed/captioned/").mock(
        return_value=Response(200, text=IG_EMBED_HTML)
    )
    resp = client.post(
        "/api/v1/social/instagram",
        json={"query_type": "post", "identifier": "https://www.instagram.com/p/DaTSSukB-Lb/"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "embed"
    post = body["posts"][0]
    assert post["author"] == "instagram"
    assert "this glow" in post["text"]


def _tiktok_profile_html():
    blob = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {
        "user": {
            "id": "1", "uniqueId": "tiktok", "nickname": "TikTok",
            "signature": "One TikTok can make a big impact",
            "verified": True, "avatarLarger": "https://p16.tiktokcdn.com/a.jpg",
        },
        "stats": {"followerCount": 94700000, "followingCount": 0, "videoCount": 1464, "heartCount": 461200000},
    }}}}
    return (
        '<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(blob) + "</script></body></html>"
    )


def _tiktok_video_html():
    blob = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": {
        "id": "7300000000000000000",
        "desc": "magic trick",
        "createTime": 1751000000,
        "author": {"uniqueId": "zachking"},
        "stats": {"diggCount": 1000000, "commentCount": 5000, "shareCount": 20000, "playCount": 30000000},
        "video": {"cover": "https://p16.tiktokcdn.com/cover.jpg", "duration": 15},
        "music": {"title": "original sound"},
    }}}}}
    return (
        '<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(blob) + "</script></body></html>"
    )


@respx.mock
def test_tiktok_profile(client):
    respx.get("https://www.tiktok.com/@tiktok").mock(
        return_value=Response(200, text=_tiktok_profile_html())
    )
    resp = client.post(
        "/api/v1/social/tiktok",
        json={"query_type": "profile", "identifier": "@tiktok"},
    )
    body = resp.json()
    assert body["success"] is True
    p = body["profile"]
    assert p["username"] == "tiktok"
    assert p["followers"] == 94700000
    assert p["extra"]["hearts"] == 461200000


@respx.mock
def test_tiktok_video(client):
    respx.get("https://www.tiktok.com/@zachking/video/7300000000000000000").mock(
        return_value=Response(200, text=_tiktok_video_html())
    )
    resp = client.post(
        "/api/v1/social/tiktok",
        json={"query_type": "post", "identifier": "https://www.tiktok.com/@zachking/video/7300000000000000000"},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["author"] == "zachking"
    assert post["text"] == "magic trick"
    assert post["stats"]["views"] == 30000000
    assert post["extra"]["music"] == "original sound"


@respx.mock
def test_tiktok_blocked_ip(client):
    respx.get("https://www.tiktok.com/@tiktok").mock(return_value=Response(403))
    resp = client.post(
        "/api/v1/social/tiktok",
        json={"query_type": "profile", "identifier": "tiktok"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "blocked"
