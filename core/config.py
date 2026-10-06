"""Access levels, tier IDs and settings. THE ONE PLACE TIERS ARE DEFINED.

Adding Pro later is two lines: an entry in `TIER_ENV` below and the matching
environment variable. Nothing else in the codebase names a tier, and nothing
compares tiers by string - routes ask for an AccessLevel and the comparison is
ordinal.

WHY THE MODEL IS TWO FIELDS AND NOT ONE. beehiiv keeps a subscription's
LIFECYCLE and its ENTITLEMENT separately, and they are genuinely independent:

    status   active | inactive | pending | needs_attention
    tiers    [] for a free reader, or the paid tiers they hold

A single "unsubscribed / free / paid" enum collapses those, and the first thing
it gets wrong is a lapsed card: beehiiv can leave `status` active while the tier
falls away, and a collapsed model would keep serving paid tools. So a subscriber
is resolved from BOTH fields, every time.

Checked against the live publication 2026-10-06: every subscriber reads
`status: "active"` with `tiers: []`, and `list_tiers` returns NOTHING - there is
no paid tier configured yet. That is why an unset tier ID must fail CLOSED (see
`configured_levels`): a missing `BEEHIIV_PREMIUM_TIER_ID` means Premium is not
available, never that everyone qualifies.
"""
import enum
import os
from typing import Optional


class AccessLevel(enum.IntEnum):
    """Ordered, so the whole access rule is `held >= required`.

    IntEnum rather than Enum deliberately: the ordering IS the policy, and
    spelling it out as a comparison in one place beats a table of which level
    satisfies which. A fourth tier slots in by number with no rule to rewrite.
    """

    NONE = 0        # not a subscriber, or no longer one
    FREE = 10
    PREMIUM = 20
    PRO = 30

    @classmethod
    def parse(cls, name: str) -> "AccessLevel":
        """'free' -> AccessLevel.FREE. Raises on anything unknown.

        REFUSED BY NAME rather than defaulted. A typo in a route's required
        level must not quietly become the most permissive thing in the enum,
        which is what a `getattr(..., FREE)` would do.
        """
        try:
            return cls[str(name).strip().upper()]
        except KeyError:
            raise ValueError(
                "unknown access level %r; expected one of %s"
                % (name, ", ".join(l.name.lower() for l in cls if l is not cls.NONE)))


# PAID LEVELS AND THE ENVIRONMENT VARIABLE THAT CARRIES EACH TIER ID.
# This dict is the entire tier configuration. FREE is absent on purpose: it is
# not a tier, it is the absence of one, and giving it an ID would invite a
# lookup that can never match.
TIER_ENV = {
    AccessLevel.PREMIUM: "BEEHIIV_PREMIUM_TIER_ID",
    AccessLevel.PRO: "BEEHIIV_PRO_TIER_ID",
}


def tier_ids() -> dict:
    """{AccessLevel: tier_id} for every paid level that is actually configured.

    Read at call time, not at import, so a deployment can set a variable and
    restart without this module caching an empty answer from boot.
    """
    out = {}
    for level, var in TIER_ENV.items():
        val = (os.environ.get(var) or "").strip()
        if val:
            out[level] = val
    return out


def configured_levels() -> set:
    """Levels this deployment can actually grant.

    FREE always; a paid level only once its tier ID is set. A route requiring an
    unconfigured level must DENY and say so - the alternative, treating a
    missing ID as "no tier to check, let them in", is the failure mode that
    would hand paid tools to everyone the day before the tier is created.
    """
    return {AccessLevel.FREE} | set(tier_ids())


def level_for_tiers(held: list) -> AccessLevel:
    """The highest configured level among the tiers a subscriber holds.

    `held` is beehiiv's `tiers` array. ITS ELEMENT SHAPE IS NOT CONFIRMED - every
    subscriber on this publication reads `[]`, so there has been nothing to
    observe. It is handled as either bare ids or objects carrying one, which
    covers both plausible shapes; if a third turns up this is the one place to
    change.
    """
    ids = set()
    for t in held or []:
        if isinstance(t, str):
            ids.add(t)
        elif isinstance(t, dict):
            for key in ("id", "tier_id", "uuid"):
                if t.get(key):
                    ids.add(str(t[key]))
                    break
    best = AccessLevel.FREE
    for level, tid in tier_ids().items():
        if tid in ids and level > best:
            best = level
    return best


# ----------------------------------------------------------------- settings
def _env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError("%s is not set" % name)
    return v or ""


class Settings:
    """Read per access, so nothing is frozen at import time."""

    @property
    def api_key(self) -> str:
        return _env("BEEHIIV_API_KEY", required=True)

    @property
    def publication_id(self) -> str:
        return _env("BEEHIIV_PUBLICATION_ID", required=True)

    @property
    def secret(self) -> str:
        """Signs sessions AND magic links. Rotating it invalidates every one of
        both at once, which is the revocation story a JWT would not give."""
        return _env("ME_PUBLIC_SECRET", required=True)

    # Cache. A POSITIVE answer is held longer than a negative one: somebody who
    # has just subscribed should not be locked out for twenty minutes by their
    # own failed first attempt, while an upgrade propagating in under half an
    # hour is fine.
    hit_ttl = int(_env("ACCESS_HIT_TTL", "1200"))        # 20 min
    miss_ttl = int(_env("ACCESS_MISS_TTL", "120"))       # 2 min
    # How long a stale entry may still be served when beehiiv is unreachable or
    # rate limiting. Being throttled must not log a paying subscriber out.
    stale_ttl = int(_env("ACCESS_STALE_TTL", "86400"))   # 24 h

    session_hours = int(_env("SESSION_HOURS", "24"))
    link_minutes = int(_env("MAGIC_LINK_MINUTES", "15"))

    cookie_name = _env("SESSION_COOKIE", "me_access")
    # False only for local http development.
    cookie_secure = _env("COOKIE_SECURE", "1") != "0"

    subscribe_url = _env("SUBSCRIBE_URL", "https://www.morningexecution.com/")
    upgrade_url = _env("UPGRADE_URL", "https://www.morningexecution.com/upgrade")
    site_url = _env("SITE_URL", "https://tools.morningexecution.com")


settings = Settings()
