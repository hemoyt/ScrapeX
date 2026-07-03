"""YouTube Innertube parser tests (trimmed real response shapes)."""
import respx
from httpx import Response

from app.services.youtube import YouTubeService, _parse_count

SEARCH_RESPONSE = {
    "contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": [
            {"videoRenderer": {
                "videoId": "kyB68hS-vco",
                "title": {"runs": [{"text": "The Complete Claude Anthropic Tutorial"}]},
                "ownerText": {"runs": [{"text": "Learn With Shopify"}]},
                "viewCountText": {"simpleText": "54,395 views"},
                "publishedTimeText": {"simpleText": "7 months ago"},
                "lengthText": {"simpleText": "14:09"},
                "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/kyB68hS-vco/hq720.jpg"}]},
            }},
        ]}},
    ]}}}},
}

PLAYER_RESPONSE = {
    "playabilityStatus": {"status": "OK"},
    "videoDetails": {
        "videoId": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "author": "Rick Astley",
        "viewCount": "1788949934",
        "shortDescription": "The official video",
        "lengthSeconds": "213",
        "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
        "keywords": ["rick", "astley"],
        "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg"}]},
    },
}

RESOLVE_RESPONSE = {
    "endpoint": {"browseEndpoint": {"browseId": "UCBJycsmduvYEL83R_U4JriQ"}},
}

BROWSE_VIDEOS_RESPONSE = {
    "metadata": {"channelMetadataRenderer": {
        "title": "Marques Brownlee",
        "description": "Quality Tech Videos",
        "vanityChannelUrl": "http://www.youtube.com/@mkbhd",
        "avatar": {"thumbnails": [{"url": "https://yt3.ggpht.com/a.jpg"}]},
    }},
    "header": {"pageHeaderRenderer": {"content": {"metadata": {"rows": [
        {"text": {"content": "21M subscribers"}},
    ]}}}},
    "contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"richGridRenderer": {"contents": [
        {"richItemRenderer": {"content": {"lockupViewModel": {
            "contentId": "7KRabLH7jcE",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {"thumbnailViewModel": {"overlays": [
                {"thumbnailBottomOverlayViewModel": {"badges": [
                    {"thumbnailBadgeViewModel": {"text": "16:31"}},
                ]}},
            ]}},
            "metadata": {"lockupMetadataViewModel": {
                "title": {"content": "The Best Car I've Ever Driven"},
                "metadata": {"contentMetadataViewModel": {"metadataRows": [
                    {"metadataParts": [{"text": {"content": "685K views"}}, {"text": {"content": "20 hours ago"}}]},
                ]}},
            }},
        }}}},
    ]}}}}]}},
}


def test_parse_count():
    assert _parse_count("54,395 views") == 54395
    assert _parse_count("1.6M views") == 1600000
    assert _parse_count("685K") == 685000
    assert _parse_count("21M subscribers") == 21000000
    assert _parse_count("") is None


def test_extract_video_id():
    ex = YouTubeService._extract_video_id
    assert ex("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert ex("https://youtu.be/dQw4w9WgXcQ?t=1") == "dQw4w9WgXcQ"
    assert ex("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert ex("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


@respx.mock
def test_youtube_search(client):
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/search").mock(
        return_value=Response(200, json=SEARCH_RESPONSE)
    )
    resp = client.post(
        "/api/v1/social/youtube",
        json={"query_type": "search", "identifier": "claude tutorial"},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["id"] == "kyB68hS-vco"
    assert post["author"] == "Learn With Shopify"
    assert post["stats"]["views"] == 54395
    assert post["extra"]["length"] == "14:09"


@respx.mock
def test_youtube_video(client):
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/player").mock(
        return_value=Response(200, json=PLAYER_RESPONSE)
    )
    resp = client.post(
        "/api/v1/social/youtube",
        json={"query_type": "post", "identifier": "https://youtu.be/dQw4w9WgXcQ"},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["author"] == "Rick Astley"
    assert post["stats"]["views"] == 1788949934
    assert post["extra"]["description"] == "The official video"


@respx.mock
def test_youtube_channel_videos_lockup_layout(client):
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/navigation/resolve_url").mock(
        return_value=Response(200, json=RESOLVE_RESPONSE)
    )
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/browse").mock(
        return_value=Response(200, json=BROWSE_VIDEOS_RESPONSE)
    )
    resp = client.post(
        "/api/v1/social/youtube",
        json={"query_type": "posts", "identifier": "@mkbhd", "limit": 5},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["id"] == "7KRabLH7jcE"
    assert post["text"] == "The Best Car I've Ever Driven"
    assert post["stats"]["views"] == 685000
    assert post["created_at"] == "20 hours ago"
    assert post["extra"]["length"] == "16:31"
    assert post["author"] == "Marques Brownlee"  # filled from channel metadata


@respx.mock
def test_youtube_channel_profile(client):
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/navigation/resolve_url").mock(
        return_value=Response(200, json=RESOLVE_RESPONSE)
    )
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/browse").mock(
        return_value=Response(200, json=BROWSE_VIDEOS_RESPONSE)
    )
    resp = client.post(
        "/api/v1/social/youtube",
        json={"query_type": "profile", "identifier": "@mkbhd"},
    )
    body = resp.json()
    assert body["success"] is True
    p = body["profile"]
    assert p["display_name"] == "Marques Brownlee"
    assert p["followers"] == 21000000
    assert p["username"] == "@mkbhd"


@respx.mock
def test_youtube_unplayable_video_errors_honestly(client):
    respx.post(url__startswith="https://www.youtube.com/youtubei/v1/player").mock(
        return_value=Response(200, json={"playabilityStatus": {"status": "LOGIN_REQUIRED"}})
    )
    resp = client.post(
        "/api/v1/social/youtube",
        json={"query_type": "post", "identifier": "dQw4w9WgXcQ"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "error"
    assert "LOGIN_REQUIRED" in body["error"]
