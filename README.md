# me-public - the gated tools site

beehiiv is the **only** source of truth for who may see what. There is no user
table here and there must not be one: a second record of who subscribes is a
second thing to reconcile, and it is wrong within a day of the first upgrade.

## Adding a tool

One line in `TOOLS` in `main.py`, and the route declares its level:

    TOOLS = {"vol-crush": "free", "term-structure": "premium"}

    @app.get("/tools/term-structure")
    def term_structure(sub = Depends(require("premium"))): ...

Nothing else in the codebase names a tier. **Adding Pro is one entry in
`TIER_ENV` (already there) plus `BEEHIIV_PRO_TIER_ID` in the environment.**

## Environment

    BEEHIIV_API_KEY            required
    BEEHIIV_PUBLICATION_ID     required
    ME_PUBLIC_SECRET           required - signs sessions AND magic links
    BEEHIIV_PREMIUM_TIER_ID    optional; absent means Premium is UNAVAILABLE
    BEEHIIV_PRO_TIER_ID        optional; same
    PUBLIC_SMTP_HOST/USER/PASS magic links; falls back to the ALERT_SMTP_* set
    SITE_URL, SUBSCRIBE_URL, UPGRADE_URL

## The three decisions worth knowing

**The session carries the EMAIL, never the tier.** Baking the level into a
24-hour token would make the cache TTL decorative: a cancelled card would keep
working for a day. The cookie proves only that an address was verified; the
level is re-read on every request, so the TTL is the real propagation delay for
upgrades and downgrades.

**Two doors, because they are worth different amounts.** Free tools take an
email box - the address IS the product, and anyone who knows a subscriber's
address getting in costs nothing. Paid tools email a one-time link, because
there the same weakness means one leaked address unlocks paid tools for
everyone, permanently, with nothing to detect or revoke.

**An unconfigured tier fails CLOSED.** With no `BEEHIIV_PREMIUM_TIER_ID` there
is no tier to compare against, and the tempting reading - nothing to check, so
let them through - would hand paid tools to everyone for as long as the variable
stayed unset. Which on this publication is today: `list_tiers` returns nothing.

## What is NOT confirmed

`tiers` element shape. Every subscriber reads `tiers: []`, so there has been
nothing to observe. Both plausible shapes are handled in `config.level_for_tiers`
and that is the one place to change if a third turns up.

## Tests

    python -m pytest tests/ -q      # 28, mutation-checked eight ways
