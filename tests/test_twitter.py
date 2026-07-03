"""Twitter service tests — fallback chain, token math, honest degradation."""
import respx
from httpx import Response

from app.services.twitter import TwitterService, syndication_token

FX_TWEET = {
    "code": 200,
    "message": "OK",
    "tweet": {
        "url": "https://x.com/jack/status/20",
        "id": "20",
        "text": "just setting up my twttr",
        "author": {"screen_name": "jack", "name": "jack"},
        "replies": 17947,
        "retweets": 125136,
        "likes": 307876,
        "quotes": 6687,
        "created_at": "Tue Mar 21 20:50:14 +0000 2006",
        "lang": "en",
    },
}

VX_TWEET = {
    "tweetID": "20",
    "tweetURL": "https://twitter.com/jack/status/20",
    "text": "just setting up my twttr",
    "user_screen_name": "jack",
    "user_name": "jack",
    "date": "Tue Mar 21 20:50:14 +0000 2006",
    "likes": 308176,
    "retweets": 125256,
    "replies": 17949,
    "media_extended": [],
}

SYND_TWEET = {
    "__typename": "Tweet",
    "id_str": "20",
    "text": "just setting up my twttr",
    "created_at": "2006-03-21T20:50:14.000Z",
    "favorite_count": 307876,
    "conversation_count": 17947,
    "user": {"id_str": "12", "name": "jack", "screen_name": "jack"},
}

FX_USER = {
    "code": 200,
    "message": "OK",
    "user": {
        "screen_name": "jack",
        "url": "https://x.com/jack",
        "name": "jack",
        "description": "no state is the best state",
        "followers": 9531114,
        "following": 3,
        "tweets": 105157,
        "avatar_url": "https://pbs.twimg.com/a.jpg",
        "joined": "Tue Mar 21 20:50:14 +0000 2006",
        "verification": {"verified": True},
    },
}


def test_syndication_token():
    # Reference value verified against the live syndication endpoint
    assert syndication_token(20) == "6dq1"


def test_tweet_id_extraction():
    ex = TwitterService._extract_tweet_id
    assert ex("https://x.com/jack/status/20") == "20"
    assert ex("https://twitter.com/jack/status/20?s=46") == "20"
    assert ex("20") == "20"


@respx.mock
def test_tweet_via_fxtwitter(client):
    respx.get("https://api.fxtwitter.com/status/20").mock(
        return_value=Response(200, json=FX_TWEET)
    )
    resp = client.post("/api/v1/social/twitter", json={"tweet_url": "https://x.com/jack/status/20"})
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "fxtwitter"
    post = body["posts"][0]
    assert post["text"] == "just setting up my twttr"
    assert post["stats"]["likes"] == 307876


@respx.mock
def test_tweet_fallback_to_vxtwitter(client):
    respx.get("https://api.fxtwitter.com/status/20").mock(
        return_value=Response(404, json={"code": 404, "message": "NOT_FOUND", "tweet": None})
    )
    respx.get("https://api.vxtwitter.com/Twitter/status/20").mock(
        return_value=Response(200, json=VX_TWEET)
    )
    resp = client.post(
        "/api/v1/social/twitter",
        json={"query_type": "post", "identifier": "20"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "vxtwitter"


@respx.mock
def test_tweet_fallback_to_syndication(client):
    respx.get("https://api.fxtwitter.com/status/20").mock(return_value=Response(500))
    respx.get("https://api.vxtwitter.com/Twitter/status/20").mock(return_value=Response(500))
    respx.get(url__startswith="https://cdn.syndication.twimg.com/tweet-result").mock(
        return_value=Response(200, json=SYND_TWEET)
    )
    resp = client.post(
        "/api/v1/social/twitter",
        json={"query_type": "post", "identifier": "20"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "syndication"
    assert body["posts"][0]["author"] == "jack"


@respx.mock
def test_tweet_all_sources_fail(client):
    respx.get(url__startswith="https://api.fxtwitter.com").mock(return_value=Response(500))
    respx.get(url__startswith="https://api.vxtwitter.com").mock(return_value=Response(500))
    respx.get(url__startswith="https://cdn.syndication.twimg.com").mock(return_value=Response(500))
    resp = client.post(
        "/api/v1/social/twitter",
        json={"query_type": "post", "identifier": "20"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "error"
    assert "All tweet sources failed" in body["error"]


@respx.mock
def test_profile_via_fxtwitter(client):
    respx.get("https://api.fxtwitter.com/jack").mock(return_value=Response(200, json=FX_USER))
    resp = client.post(
        "/api/v1/social/twitter",
        json={"query_type": "profile", "identifier": "@jack"},
    )
    body = resp.json()
    assert body["success"] is True
    p = body["profile"]
    assert p["username"] == "jack"
    assert p["followers"] == 9531114
    assert p["verified"] is True


@respx.mock
def test_timeline_blocked_when_nitter_dead(client):
    respx.get(url__startswith="https://nitter.net").mock(return_value=Response(200, text=""))
    respx.get(url__startswith="https://xcancel.com").mock(return_value=Response(503))
    respx.get(url__startswith="https://nitter.poast.org").mock(return_value=Response(503))
    resp = client.post("/api/v1/social/twitter", json={"username": "jack", "max_tweets": 5})
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "blocked"
    assert "no reliable keyless source" in body["error"]
    assert body["posts"] == []  # never fabricate


NITTER_HTML = """
<div class="timeline">
  <div class="timeline-item">
    <a class="tweet-link" href="/jack/status/20#m"></a>
    <a class="username" href="/jack">@jack</a>
    <span class="tweet-date"><a href="/jack/status/20" title="Mar 21, 2006 · 8:50 PM UTC">Mar 21</a></span>
    <div class="tweet-content media-body">just setting up my twttr</div>
    <div class="tweet-stats">
      <span class="tweet-stat"><div class="icon-container"><span class="icon-comment"></span> 17,947</div></span>
      <span class="tweet-stat"><div class="icon-container"><span class="icon-retweet"></span> 125,136</div></span>
      <span class="tweet-stat"><div class="icon-container"><span class="icon-heart"></span> 307,876</div></span>
    </div>
  </div>
</div>
"""


@respx.mock
def test_timeline_via_working_nitter(client):
    respx.get("https://nitter.net/jack").mock(return_value=Response(200, text=NITTER_HTML))
    resp = client.post("/api/v1/social/twitter", json={"username": "jack", "max_tweets": 5})
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == "nitter.net"
    post = body["posts"][0]
    assert post["text"] == "just setting up my twttr"
    assert post["id"] == "20"
    assert post["url"] == "https://x.com/jack/status/20"
    assert post["stats"] == {"replies": 17947, "retweets": 125136, "likes": 307876}
