"""Subscriber lookup: the beehiiv call, the cache around it, and the resolution.

beehiiv is the ONLY source of truth. There is no user table here and there must
not be one: a second store of who is subscribed is a second thing to reconcile,
and it would be wrong within a day of the first upgrade.

THE CACHE IS IN MEMORY, AND THAT IS A SIZED DECISION. This publication has four
subscribers and runs on one droplet. Redis would be a whole service to
provision, secure and monitor so a handful of entries could be shared between
processes that may not exist; a Postgres table would put public-site auth state
into the research database and add a network hop and an outage mode to a site
whose virtue is having almost no dependencies. A dict is a few lines and cannot
be the thing that goes down.

    IT IS PER PROCESS. Run uvicorn with more than one worker and each gets its
    own, which costs a few extra API calls and nothing else. Said plainly
    because it is the one property that would surprise someone later.

STALE BEATS LOCKED OUT. Every entry keeps a long stale window, and if beehiiv
errors, rate limits, or times out, the last known answer is served rather than
denying a paying subscriber. The alternative - treating a vendor 429 as "not
subscribed" - turns their outage into your outage, visibly and for the wrong
people. Only a genuinely unknown email with nothing cached is refused.
"""
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from core.config import AccessLevel, level_for_tiers, settings

API = "https://api.beehiiv.com/v2"


@dataclass
class Subscriber:
    email: str
    level: AccessLevel
    status: str = ""
    found: bool = False
    stale: bool = False          # served from cache after a failed refresh
    error: Optional[str] = None  # set only when nothing could be determined


@dataclass
class _Entry:
    sub: Subscriber
    fresh_until: float
    stale_until: float


_CACHE: dict = {}
# Set when beehiiv rate limits us; until it passes, lookups go straight to
# cache rather than queueing more requests into a closed door.
_BACKOFF_UNTIL = 0.0


def _key(email: str) -> str:
    return (email or "").strip().lower()


def _fetch(email: str) -> Subscriber:
    """One beehiiv lookup. Raises on anything that is not a clean answer."""
    global _BACKOFF_UNTIL
    r = httpx.get(
        "%s/publications/%s/subscriptions" % (API, settings.publication_id),
        params={"email": email, "limit": 10},
        headers={"Authorization": "Bearer %s" % settings.api_key,
                 "Accept": "application/json"},
        timeout=8.0,
    )
    if r.status_code == 429:
        # Respect the vendor's own number when it gives one.
        wait = float(r.headers.get("Retry-After") or 30)
        _BACKOFF_UNTIL = time.time() + min(wait, 300)
        raise RuntimeError("rate limited")
    r.raise_for_status()
    rows = (r.json() or {}).get("data") or []

    # EXACT MATCH, CASE FOLDED. beehiiv's email filter is documented as a
    # PARTIAL match, so a lookup for a@b.com can return xa@b.com. Taking rows[0]
    # would hand one subscriber's entitlement to anyone whose address contains
    # theirs, which is a gate that opens to a substring.
    want = _key(email)
    row = next((x for x in rows if _key(x.get("email")) == want), None)
    if row is None:
        return Subscriber(email=want, level=AccessLevel.NONE, found=False)

    status = str(row.get("status") or "").lower()
    if status != "active":
        # pending, inactive and needs_attention are all "not entitled today".
        # They are kept in `status` so the page can say which rather than a flat
        # "not subscribed", because the fix differs: confirm your address,
        # resubscribe, or update your card.
        return Subscriber(email=want, level=AccessLevel.NONE,
                          status=status, found=True)
    return Subscriber(email=want, level=level_for_tiers(row.get("tiers")),
                      status=status, found=True)


def lookup(email: str) -> Subscriber:
    """Cached subscriber resolution. Never raises."""
    k = _key(email)
    if not k or "@" not in k:
        return Subscriber(email=k, level=AccessLevel.NONE, error="that does not look like an email address")

    now = time.time()
    hit = _CACHE.get(k)
    if hit and now < hit.fresh_until:
        return hit.sub
    if now < _BACKOFF_UNTIL and hit:
        return _stale(hit)

    try:
        sub = _fetch(k)
    except Exception as e:                       # noqa: BLE001 - deliberate
        if hit and now < hit.stale_until:
            return _stale(hit)
        return Subscriber(email=k, level=AccessLevel.NONE,
                          error="could not reach the subscriber list (%s)"
                                % type(e).__name__)
    ttl = settings.hit_ttl if sub.found else settings.miss_ttl
    _CACHE[k] = _Entry(sub=sub, fresh_until=now + ttl,
                       stale_until=now + settings.stale_ttl)
    return sub


def _stale(entry: _Entry) -> Subscriber:
    s = entry.sub
    return Subscriber(email=s.email, level=s.level, status=s.status,
                      found=s.found, stale=True)


def forget(email: str) -> None:
    """Drop one cached answer. The escape hatch for "I just upgraded and the
    site has not noticed", so nobody has to wait out a TTL or restart a box."""
    _CACHE.pop(_key(email), None)


def cache_stats() -> dict:
    now = time.time()
    return {"entries": len(_CACHE),
            "fresh": sum(1 for e in _CACHE.values() if now < e.fresh_until),
            "backoff_seconds": max(0, int(_BACKOFF_UNTIL - now))}
