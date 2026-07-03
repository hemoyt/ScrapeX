"""Parser tests for the public-API platforms (Bluesky, HN, Mastodon)."""
import json

import respx
from httpx import Response

BSKY_PROFILE = {
    "did": "did:plc:z72i7hdynmk6r22z27h6tvur",
    "handle": "bsky.app",
    "displayName": "Bluesky",
    "description": "Official account",
    "followersCount": 3000000,
    "followsCount": 10,
    "postsCount": 300,
    "avatar": "https://cdn.bsky.app/avatar.jpg",
}

BSKY_POST = {
    "uri": "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/3l6oveex3ii2l",
    "author": {"handle": "bsky.app", "displayName": "Bluesky"},
    "record": {"text": "hello world", "createdAt": "2026-01-01T00:00:00Z"},
    "likeCount": 42,
    "repostCount": 7,
    "replyCount": 3,
    "quoteCount": 1,
}


@respx.mock
def test_bluesky_profile(client):
    respx.get(url__startswith="https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile").mock(
        return_value=Response(200, json=BSKY_PROFILE)
    )
    resp = client.post(
        "/api/v1/social/bluesky",
        json={"query_type": "profile", "identifier": "@bsky.app"},
    )
    body = resp.json()
    assert body["success"] is True
    p = body["profile"]
    assert p["username"] == "bsky.app"
    assert p["followers"] == 3000000
    assert p["url"] == "https://bsky.app/profile/bsky.app"


@respx.mock
def test_bluesky_posts(client):
    respx.get(url__startswith="https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed").mock(
        return_value=Response(200, json={"feed": [{"post": BSKY_POST}]})
    )
    resp = client.post(
        "/api/v1/social/bluesky",
        json={"query_type": "posts", "identifier": "bsky.app", "limit": 5},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["text"] == "hello world"
    assert post["author"] == "bsky.app"
    assert post["stats"]["likes"] == 42
    assert post["url"] == "https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l"


@respx.mock
def test_bluesky_search_falls_back_to_second_appview(client):
    respx.get(url__startswith="https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts").mock(
        return_value=Response(403, text="<html>403 Forbidden</html>")
    )
    respx.get(url__startswith="https://api.bsky.app/xrpc/app.bsky.feed.searchPosts").mock(
        return_value=Response(200, json={"posts": [BSKY_POST]})
    )
    resp = client.post(
        "/api/v1/social/bluesky",
        json={"query_type": "search", "identifier": "anthropic"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "api.bsky.app"
    assert len(body["posts"]) == 1


HN_HIT = {
    "objectID": "38900001",
    "title": "Show HN: I built a thing",
    "author": "builder",
    "created_at": "2026-06-01T10:00:00Z",
    "points": 256,
    "num_comments": 89,
    "url": "https://example.com/thing",
}


@respx.mock
def test_hackernews_search(client):
    respx.get(url__startswith="https://hn.algolia.com/api/v1/search").mock(
        return_value=Response(200, json={"hits": [HN_HIT]})
    )
    resp = client.post(
        "/api/v1/social/hackernews",
        json={"query_type": "search", "identifier": "show hn"},
    )
    body = resp.json()
    assert body["success"] is True
    post = body["posts"][0]
    assert post["stats"] == {"points": 256, "comments": 89}
    assert post["url"] == "https://news.ycombinator.com/item?id=38900001"
    assert post["extra"]["story_url"] == "https://example.com/thing"


@respx.mock
def test_hackernews_item_with_comments(client):
    respx.get("https://hn.algolia.com/api/v1/items/38900001").mock(
        return_value=Response(200, json={
            "id": 38900001,
            "title": "Show HN: I built a thing",
            "author": "builder",
            "created_at": "2026-06-01T10:00:00Z",
            "points": 256,
            "children": [
                {"author": "commenter", "text": "<p>Nice <i>work</i>!</p>", "created_at": "2026-06-01T11:00:00Z"},
            ],
        })
    )
    resp = client.post(
        "/api/v1/social/hn",  # alias
        json={"query_type": "post", "identifier": "https://news.ycombinator.com/item?id=38900001"},
    )
    body = resp.json()
    assert body["success"] is True
    comments = body["posts"][0]["extra"]["comments"]
    assert comments[0]["author"] == "commenter"
    assert "Nice" in comments[0]["text"] and "<p>" not in comments[0]["text"]


MASTO_ACCOUNT = {
    "id": "1",
    "username": "Gargron",
    "acct": "Gargron",
    "display_name": "Eugen",
    "note": "<p>Mastodon founder</p>",
    "followers_count": 500000,
    "following_count": 400,
    "statuses_count": 70000,
    "avatar": "https://files.mastodon.social/avatar.png",
    "url": "https://mastodon.social/@Gargron",
}

MASTO_STATUS = {
    "id": "111222333",
    "created_at": "2026-05-01T12:00:00Z",
    "url": "https://mastodon.social/@Gargron/111222333",
    "content": "<p>Hello <b>fediverse</b></p>",
    "favourites_count": 100,
    "reblogs_count": 20,
    "replies_count": 5,
    "account": MASTO_ACCOUNT,
    "media_attachments": [],
}


@respx.mock
def test_mastodon_profile_and_posts(client):
    respx.get(url__startswith="https://mastodon.social/api/v1/accounts/lookup").mock(
        return_value=Response(200, json=MASTO_ACCOUNT)
    )
    respx.get(url__startswith="https://mastodon.social/api/v1/accounts/1/statuses").mock(
        return_value=Response(200, json=[MASTO_STATUS])
    )

    resp = client.post(
        "/api/v1/social/mastodon",
        json={"query_type": "profile", "identifier": "Gargron@mastodon.social"},
    )
    body = resp.json()
    assert body["profile"]["username"] == "Gargron@mastodon.social"
    assert body["profile"]["bio"] == "Mastodon founder"

    resp = client.post(
        "/api/v1/social/mastodon",
        json={"query_type": "posts", "identifier": "Gargron"},
    )
    post = resp.json()["posts"][0]
    assert post["text"] == "Hello fediverse"
    assert post["stats"]["likes"] == 100


@respx.mock
def test_mastodon_gated_search_falls_back_to_hashtag(client):
    respx.get(url__startswith="https://mastodon.social/api/v2/search").mock(
        return_value=Response(200, json={"accounts": [], "statuses": [], "hashtags": []})
    )
    respx.get(url__startswith="https://mastodon.social/api/v1/timelines/tag/python").mock(
        return_value=Response(200, json=[MASTO_STATUS])
    )
    resp = client.post(
        "/api/v1/social/mastodon",
        json={"query_type": "search", "identifier": "python"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "partial"
    assert "hashtag" in body["error"]
    assert len(body["posts"]) == 1
