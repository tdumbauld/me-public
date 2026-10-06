"""Sessions, magic links, and the one dependency that gates a route.

THE SESSION CARRIES THE EMAIL AND NEVER THE TIER. This is the decision worth
reading twice. Baking the level into a 24-hour token makes the cache TTL
decorative: a cancelled card or a downgrade keeps working for a full day, and
nothing on the server can tell. So the cookie proves exactly one thing - this
address was verified - and the level is re-read from `subscribers.lookup` on
every request. The cache TTL is then the real, stated propagation delay for
upgrades and downgrades, which is what it was supposed to be.

A SIGNED COOKIE, NOT A JWT. A JWT buys verification by a party that does not
hold the secret, which on one droplet is nobody, and it costs the one property
that matters here: a JWT cannot be revoked. Rotating ME_PUBLIC_SECRET
invalidates every session and every outstanding magic link at once.

TWO DOORS, BECAUSE THEY ARE WORTH DIFFERENT AMOUNTS. A free tool takes an email
box: the address is the product, and the weakness - anyone who knows a
subscriber's address can type it - costs nothing because the thing behind the
door is free. A paid tool emails a one-time link instead, because there the same
weakness means one leaked address unlocks paid tools for everyone, permanently,
with nothing to detect or revoke. Emails are not secrets and must not be
treated as passwords for anything that costs money.
"""
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from core.config import AccessLevel, configured_levels, settings
from core.subscribers import Subscriber, lookup

SESSION_SALT = "me-public.session"
LINK_SALT = "me-public.magic-link"

# Magic links are single use. Consumed ids are held in memory with their own
# expiry, which is exact on one worker and per-worker beyond that - the same
# property the lookup cache has, and acceptable for the same reason. A replay
# would in any case need the recipient's inbox inside the link's 15 minutes.
_USED: dict = {}


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret, salt=salt)


# ------------------------------------------------------------------ session
def issue_session(resp: Response, email: str) -> None:
    token = _serializer(SESSION_SALT).dumps({"e": email.strip().lower()})
    resp.set_cookie(
        settings.cookie_name, token,
        max_age=settings.session_hours * 3600,
        httponly=True,              # no script on the page needs to read it
        secure=settings.cookie_secure,
        samesite="lax",             # survives a click in from the newsletter
        path="/",
    )


def clear_session(resp: Response) -> None:
    resp.delete_cookie(settings.cookie_name, path="/")


def session_email(request: Request) -> Optional[str]:
    tok = request.cookies.get(settings.cookie_name)
    if not tok:
        return None
    try:
        data = _serializer(SESSION_SALT).loads(
            tok, max_age=settings.session_hours * 3600)
    except (BadSignature, SignatureExpired):
        return None                 # expired and forged are the same answer here
    return (data or {}).get("e")


# --------------------------------------------------------------- magic link
def issue_link_token(email: str) -> str:
    return _serializer(LINK_SALT).dumps(
        {"e": email.strip().lower(), "n": "%x" % int(time.time() * 1e6)})


def consume_link_token(token: str) -> Optional[str]:
    """-> the email, or None. Single use."""
    try:
        data = _serializer(LINK_SALT).loads(
            token, max_age=settings.link_minutes * 60)
    except (BadSignature, SignatureExpired):
        return None
    nonce = (data or {}).get("n")
    now = time.time()
    for k, exp in list(_USED.items()):           # cheap sweep, the set is tiny
        if exp < now:
            _USED.pop(k, None)
    if not nonce or nonce in _USED:
        return None
    _USED[nonce] = now + settings.link_minutes * 60
    return data.get("e")


# ------------------------------------------------------------------- policy
def decide(sub: Subscriber, required: AccessLevel) -> tuple:
    """(allowed, reason, action) - pure, so the whole policy is one testable fn.

    `action` is what the page should offer: subscribe, upgrade, or nothing.
    """
    if required not in configured_levels():
        # The tier ID is not set, so this level cannot be checked. FAIL CLOSED.
        # Treating an unconfigured tier as "nothing to check" would hand paid
        # tools to everyone for exactly as long as the variable stayed unset.
        return (False, "%s access is not available yet." % required.name.title(), None)
    if sub.error:
        return (False, sub.error, None)
    if not sub.found:
        return (False, "That address is not on the subscriber list.", "subscribe")
    if sub.level is AccessLevel.NONE:
        msg = {"pending": "That subscription has not been confirmed yet. "
                          "Check your inbox for the confirmation email.",
               "inactive": "That subscription is no longer active.",
               "needs_attention": "That subscription needs attention - usually a "
                                  "payment method that needs updating."}
        return (False, msg.get(sub.status, "That subscription is not active."),
                "subscribe")
    if sub.level >= required:
        return (True, "", None)
    return (False,
            "That tool needs %s. Your subscription is %s."
            % (required.name.title(), sub.level.name.title()),
            "upgrade")


# --------------------------------------------------------------- dependency
def require(level):
    """FastAPI dependency factory. A route declares its tier and nothing else.

        @app.get("/tools/vol-crush")
        def tool(sub = Depends(require("free"))): ...

    Adding Pro is a tier ID in the environment; no route changes shape.
    """
    required = level if isinstance(level, AccessLevel) else AccessLevel.parse(level)

    def dep(request: Request) -> Subscriber:
        email = session_email(request)
        if not email:
            raise HTTPException(status_code=401, detail={
                "need": required.name.lower(),
                "reason": "Enter the email you subscribe with.",
                "action": "identify"})
        sub = lookup(email)
        ok, reason, action = decide(sub, required)
        if not ok:
            raise HTTPException(status_code=403, detail={
                "need": required.name.lower(),
                "have": sub.level.name.lower(),
                "reason": reason,
                "action": action,
                "subscribe_url": settings.subscribe_url,
                "upgrade_url": settings.upgrade_url})
        return sub

    return dep


# Convenience, so a route reads `Depends(FREE)` when it wants the default.
FREE = require(AccessLevel.FREE)
PREMIUM = require(AccessLevel.PREMIUM)
PRO = require(AccessLevel.PRO)
