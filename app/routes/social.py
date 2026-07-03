"""Social media routes — Twitter/X and Reddit."""
from fastapi import APIRouter, HTTPException
from app.models import TwitterRequest, RedditRequest, SocialResponse
from app.services import TwitterService, RedditService

router = APIRouter()


@router.post("/twitter", response_model=SocialResponse)
async def scrape_twitter(req: TwitterRequest):
    """Scrape Twitter/X profile tweets or a specific tweet."""
    if not req.username and not req.tweet_url:
        raise HTTPException(status_code=400, detail="Provide either username or tweet_url")

    twitter = TwitterService()
    try:
        if req.tweet_url:
            data = twitter.get_tweet_by_url(req.tweet_url)
            tweets = [data] if data else []
        else:
            tweets = twitter.get_tweets(req.username, max_tweets=req.max_tweets)

        return SocialResponse(
            success=len(tweets) > 0,
            platform="twitter",
            data=tweets,
            error=tweets[0].get("_error") if tweets and "_error" in tweets[0] else None,
        )
    except Exception as e:
        return SocialResponse(success=False, platform="twitter", error=str(e))
    finally:
        twitter.close()


@router.post("/reddit", response_model=SocialResponse)
async def scrape_reddit(req: RedditRequest):
    """Scrape a subreddit or a specific Reddit post."""
    if not req.subreddit and not req.post_url:
        raise HTTPException(status_code=400, detail="Provide either subreddit or post_url")

    reddit = RedditService()
    try:
        if req.post_url:
            data = reddit.get_post(req.post_url)
            return SocialResponse(success=True, platform="reddit", data=[data])
        else:
            posts = reddit.get_subreddit(req.subreddit, listing=req.listing, limit=req.limit)
            return SocialResponse(success=True, platform="reddit", data=posts)
    except Exception as e:
        return SocialResponse(success=False, platform="reddit", error=str(e))
    finally:
        reddit.close()
