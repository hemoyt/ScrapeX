import time

from app.services.cache import TTLCache


def test_set_get():
    c = TTLCache()
    c.set("a", {"x": 1}, ttl=10)
    assert c.get("a") == {"x": 1}


def test_expiry():
    c = TTLCache()
    c.set("a", 1, ttl=0.01)
    time.sleep(0.02)
    assert c.get("a") is None


def test_missing():
    assert TTLCache().get("nope") is None


def test_eviction():
    c = TTLCache(maxsize=2)
    c.set("a", 1, ttl=1)   # expires soonest
    c.set("b", 2, ttl=100)
    c.set("c", 3, ttl=100)  # evicts "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_overwrite_does_not_evict():
    c = TTLCache(maxsize=2)
    c.set("a", 1, ttl=100)
    c.set("b", 2, ttl=100)
    c.set("a", 10, ttl=100)
    assert c.get("a") == 10
    assert c.get("b") == 2
