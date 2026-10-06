"""me-public - the gated tools site. beehiiv is the only source of truth.

THE TOOL REGISTRY IS THE WHOLE CONFIGURATION. One line per tool naming its
required level; the route body never mentions a tier and never checks one.

    TOOLS = {"vol-crush": "free", "term-structure": "premium"}

Adding Pro later is a tier ID in the environment and `"pro"` in this dict. No
route, no dependency and no policy function changes shape, which was the point.
"""
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core import mail
from core.access import (clear_session, consume_link_token, decide,
                         issue_link_token, issue_session, require,
                         session_email)
from core.config import AccessLevel, configured_levels, settings
from core.subscribers import cache_stats, forget, lookup

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- THE REGISTRY. One line per tool. ----------------------------------------
TOOLS = {
    "vol-crush": "free",
}

app = FastAPI(title="Morning Execution tools")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")),
          name="static")


def tool_level(slug: str) -> AccessLevel:
    if slug not in TOOLS:
        raise HTTPException(status_code=404, detail="No such tool.")
    return AccessLevel.parse(TOOLS[slug])


# ------------------------------------------------------------------- access
@app.post("/api/access/identify")
async def identify(request: Request, response: Response):
    """Email submitted. Free tools sign in here; paid tools get a link posted.

    THE ANSWER IS DELIBERATELY THE SAME SHAPE EITHER WAY for a paid tool,
    whether or not the address qualifies: "if that address is eligible, a link
    is on its way". Saying "not a Premium subscriber" to an unauthenticated
    box turns this endpoint into a free lookup of who pays you.
    """
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    slug = (body.get("tool") or "").strip()
    required = tool_level(slug)
    sub = lookup(email)
    ok, reason, action = decide(sub, required)

    if required is AccessLevel.FREE:
        if not ok:
            return JSONResponse(status_code=403, content={
                "ok": False, "reason": reason, "action": action,
                "subscribe_url": settings.subscribe_url,
                "upgrade_url": settings.upgrade_url})
        issue_session(response, email)
        response.status_code = 200
        return {"ok": True, "level": sub.level.name.lower()}

    # Paid: never sign in from the box alone.
    if ok:
        link = "%s/api/access/verify?token=%s&tool=%s" % (
            settings.site_url, issue_link_token(email), slug)
        try:
            mail.send_magic_link(email, link, settings.link_minutes)
        except Exception as e:                       # noqa: BLE001
            return JSONResponse(status_code=502, content={
                "ok": False,
                "reason": "Could not send the sign-in link (%s). Try again shortly."
                          % type(e).__name__})
    return {"ok": True, "sent": True,
            "reason": "If that address is eligible, a sign-in link is on its way. "
                      "It is good for %d minutes." % settings.link_minutes}


@app.get("/api/access/verify")
def verify(token: str, tool: str = ""):
    email = consume_link_token(token)
    if not email:
        return HTMLResponse(
            "<h1>That link has expired</h1><p>Sign-in links last %d minutes and "
            "work once. Request another from the tool.</p>" % settings.link_minutes,
            status_code=400)
    dest = "/tools/%s" % tool if tool in TOOLS else "/"
    resp = RedirectResponse(dest, status_code=303)
    issue_session(resp, email)
    return resp


@app.post("/api/access/signout")
def signout(response: Response):
    clear_session(response)
    return {"ok": True}


@app.get("/api/access/me")
def whoami(request: Request):
    """What the browser is allowed to know about itself. Drives the UI only;
    nothing here is trusted for access, which is decided server side per route."""
    email = session_email(request)
    if not email:
        return {"signed_in": False}
    sub = lookup(email)
    return {"signed_in": True, "email": sub.email,
            "level": sub.level.name.lower(), "stale": sub.stale}


@app.post("/api/access/refresh")
def refresh(request: Request):
    """Drop the cached answer for the signed-in address.

    The escape hatch for "I just upgraded and the site has not noticed", so
    nobody waits out a TTL or restarts a box. Scoped to the caller's OWN email,
    so it cannot be used to sweep the cache.
    """
    email = session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not signed in.")
    forget(email)
    sub = lookup(email)
    return {"ok": True, "level": sub.level.name.lower()}


# -------------------------------------------------------------------- tools
@app.get("/tools/vol-crush", response_class=HTMLResponse)
def vol_crush(sub=Depends(require("free"))):
    with open(os.path.join(HERE, "tools", "price-the-event.standalone.html"),
              encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ------------------------------------------------------------------- health
@app.get("/api/health")
def health():
    """Enough to tell a broken deployment from a quiet one, and no secrets."""
    return {"ok": True,
            "tools": TOOLS,
            "levels_configured": sorted(l.name.lower() for l in configured_levels()),
            "cache": cache_stats()}
