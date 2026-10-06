"""The access model. Most of this is about what must be REFUSED.

The policy is one pure function, `access.decide`, which is why these are cheap
and why there is no excuse for not having them: everything that decides whether
somebody gets a paid tool happens in a function that takes two values and
returns three.
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BEEHIIV_API_KEY", "test-key")
os.environ.setdefault("BEEHIIV_PUBLICATION_ID", "pub_test")
os.environ.setdefault("ME_PUBLIC_SECRET", "test-secret-not-a-real-one")

from core import access, subscribers                       # noqa: E402
from core.config import AccessLevel, level_for_tiers       # noqa: E402

PREM = "tier_premium_0001"
PRO = "tier_pro_0002"


def sub(level, found=True, status="active", error=None):
    return subscribers.Subscriber(email="a@b.com", level=level, found=found,
                                  status=status, error=error)


class TheOrderingIsThePolicy(unittest.TestCase):

    def setUp(self):
        os.environ["BEEHIIV_PREMIUM_TIER_ID"] = PREM
        os.environ["BEEHIIV_PRO_TIER_ID"] = PRO

    def tearDown(self):
        os.environ.pop("BEEHIIV_PREMIUM_TIER_ID", None)
        os.environ.pop("BEEHIIV_PRO_TIER_ID", None)

    def test_a_higher_tier_satisfies_a_lower_requirement(self):
        for held in (AccessLevel.FREE, AccessLevel.PREMIUM, AccessLevel.PRO):
            self.assertTrue(access.decide(sub(held), AccessLevel.FREE)[0],
                            "%s should satisfy free" % held)
        for held in (AccessLevel.PREMIUM, AccessLevel.PRO):
            self.assertTrue(access.decide(sub(held), AccessLevel.PREMIUM)[0])

    def test_a_lower_tier_does_not_satisfy_a_higher_requirement(self):
        self.assertFalse(access.decide(sub(AccessLevel.FREE), AccessLevel.PREMIUM)[0])
        self.assertFalse(access.decide(sub(AccessLevel.PREMIUM), AccessLevel.PRO)[0])

    def test_the_refusal_names_the_tier_and_offers_the_upgrade(self):
        ok, reason, action = access.decide(sub(AccessLevel.FREE), AccessLevel.PREMIUM)
        self.assertFalse(ok)
        self.assertIn("Premium", reason)
        self.assertEqual(action, "upgrade")

    def test_adding_pro_is_configuration_and_not_code(self):
        """The whole point of the design. Pro works with the tier ID set and is
        refused without it, and no module was edited in between."""
        self.assertTrue(access.decide(sub(AccessLevel.PRO), AccessLevel.PRO)[0])
        os.environ.pop("BEEHIIV_PRO_TIER_ID")
        ok, reason, _ = access.decide(sub(AccessLevel.PRO), AccessLevel.PRO)
        self.assertFalse(ok)
        self.assertIn("not available", reason)


class AnUnconfiguredTierFailsClosed(unittest.TestCase):
    """The failure that would hand paid tools to everyone.

    With no BEEHIIV_PREMIUM_TIER_ID there is no tier to compare against. The
    tempting reading is "nothing to check, so let them through"; it would do
    exactly that for as long as the variable stayed unset, which on this
    publication is TODAY - checked 2026-10-06, `list_tiers` returns nothing.
    """

    def test_premium_is_refused_when_no_tier_id_is_set(self):
        os.environ.pop("BEEHIIV_PREMIUM_TIER_ID", None)
        for held in (AccessLevel.FREE, AccessLevel.PREMIUM, AccessLevel.PRO):
            ok, reason, _ = access.decide(sub(held), AccessLevel.PREMIUM)
            self.assertFalse(ok, "%s got in with no tier configured" % held)
            self.assertIn("not available", reason)

    def test_free_still_works_with_no_paid_tiers_at_all(self):
        os.environ.pop("BEEHIIV_PREMIUM_TIER_ID", None)
        os.environ.pop("BEEHIIV_PRO_TIER_ID", None)
        self.assertTrue(access.decide(sub(AccessLevel.FREE), AccessLevel.FREE)[0])


class TheTierArrayIsReadDefensively(unittest.TestCase):
    """Its element shape is UNCONFIRMED - every subscriber on this publication
    reads `tiers: []`, so there has been nothing to observe. Both plausible
    shapes are handled, and an unknown tier grants nothing."""

    def setUp(self):
        os.environ["BEEHIIV_PREMIUM_TIER_ID"] = PREM

    def tearDown(self):
        os.environ.pop("BEEHIIV_PREMIUM_TIER_ID", None)

    def test_bare_ids(self):
        self.assertIs(level_for_tiers([PREM]), AccessLevel.PREMIUM)

    def test_objects_carrying_an_id(self):
        self.assertIs(level_for_tiers([{"id": PREM, "name": "Premium"}]),
                      AccessLevel.PREMIUM)

    def test_an_empty_array_is_free_not_paid(self):
        self.assertIs(level_for_tiers([]), AccessLevel.FREE)

    def test_a_tier_we_do_not_recognise_grants_nothing_extra(self):
        self.assertIs(level_for_tiers(["tier_something_else"]), AccessLevel.FREE)


class AnInactiveSubscriptionIsNotEntitled(unittest.TestCase):
    """status and tiers are INDEPENDENT. A lapsed card can leave one of them
    looking fine, which is why both are read."""

    def test_each_state_gets_its_own_sentence(self):
        for status, fragment in (("pending", "not been confirmed"),
                                 ("inactive", "no longer active"),
                                 ("needs_attention", "needs attention")):
            ok, reason, action = access.decide(
                sub(AccessLevel.NONE, status=status), AccessLevel.FREE)
            self.assertFalse(ok)
            self.assertIn(fragment, reason)
            self.assertEqual(action, "subscribe")

    def test_an_unknown_address_is_pointed_at_subscribing(self):
        ok, _, action = access.decide(sub(AccessLevel.NONE, found=False),
                                      AccessLevel.FREE)
        self.assertFalse(ok)
        self.assertEqual(action, "subscribe")


class AnExactEmailMatchIsRequired(unittest.TestCase):
    """beehiiv's email filter is a PARTIAL match, so a lookup for a@b.com can
    return xa@b.com. Taking the first row would hand one subscriber's
    entitlement to anybody whose address contains theirs.

    THESE DRIVE THE REAL FETCH. The first version of this class reimplemented
    the matching inline and passed whatever the module did, which is a test of
    the test. It stubs the HTTP layer instead, so the assertion is about
    `subscribers._fetch` and nothing else.
    """

    class _Resp:
        status_code = 200
        headers: dict = {}

        def __init__(self, rows):
            self._rows = rows

        def json(self):
            return {"data": self._rows}

        def raise_for_status(self):
            return None

    def _fetch_with(self, rows, email):
        import httpx
        orig = httpx.get
        httpx.get = lambda *a, **k: self._Resp(rows)
        try:
            return subscribers._fetch(email)
        finally:
            httpx.get = orig

    def test_a_substring_row_is_not_treated_as_the_subscriber(self):
        got = self._fetch_with(
            [{"email": "xa@b.com", "status": "active", "tiers": []}], "a@b.com")
        self.assertFalse(got.found, "a substring row was accepted as the subscriber")
        self.assertIs(got.level, AccessLevel.NONE)

    def test_the_exact_row_is_found_among_near_misses(self):
        got = self._fetch_with([{"email": "xa@b.com", "status": "active", "tiers": []},
                                {"email": "a@b.com", "status": "active", "tiers": []}],
                               "a@b.com")
        self.assertTrue(got.found)
        self.assertIs(got.level, AccessLevel.FREE)

    def test_case_is_folded_rather_than_missed(self):
        got = self._fetch_with(
            [{"email": "A@B.Com", "status": "active", "tiers": []}], "a@b.com")
        self.assertTrue(got.found, "the same address in another case was missed")

    def test_an_inactive_exact_row_is_found_but_not_entitled(self):
        got = self._fetch_with(
            [{"email": "a@b.com", "status": "inactive", "tiers": []}], "a@b.com")
        self.assertTrue(got.found)
        self.assertIs(got.level, AccessLevel.NONE)
        self.assertEqual(got.status, "inactive")


class TheSessionCarriesTheEmailAndNotTheTier(unittest.TestCase):
    """If the level were baked into a 24-hour token, a downgrade would keep
    working for a day and the cache TTL would be decorative."""

    def test_the_cookie_payload_holds_no_level(self):
        """DRIVES issue_session, not a token built here.

        The first version of this minted its own token and so passed whatever
        issue_session actually wrote - a mutation adding the tier to the real
        cookie survived it. That is the second test in this file to have had
        the same flaw, and both were found by mutation rather than by reading.
        """
        from fastapi import Response
        resp = Response()
        access.issue_session(resp, "a@b.com")
        raw = resp.headers["set-cookie"]
        from core.config import settings
        tok = raw.split(settings.cookie_name + "=", 1)[1].split(";", 1)[0]
        data = access._serializer(access.SESSION_SALT).loads(tok)
        self.assertEqual(set(data), {"e"},
                         "the session cookie carries more than the email: %r" % data)
        self.assertEqual(data["e"], "a@b.com")

    def test_the_cookie_is_httponly_secure_and_lax(self):
        from fastapi import Response
        resp = Response()
        access.issue_session(resp, "a@b.com")
        raw = resp.headers["set-cookie"].lower()
        self.assertIn("httponly", raw)
        self.assertIn("samesite=lax", raw)
        self.assertIn("secure", raw)

    def test_a_forged_cookie_is_rejected(self):
        other = access.URLSafeTimedSerializer("a different secret",
                                              salt=access.SESSION_SALT)
        tok = other.dumps({"e": "a@b.com"})
        from itsdangerous import BadSignature
        with self.assertRaises(BadSignature):
            access._serializer(access.SESSION_SALT).loads(tok)


class AMagicLinkWorksOnceAndExpires(unittest.TestCase):

    def setUp(self):
        access._USED.clear()

    def test_it_works_once(self):
        tok = access.issue_link_token("a@b.com")
        self.assertEqual(access.consume_link_token(tok), "a@b.com")
        self.assertIsNone(access.consume_link_token(tok),
                          "a sign-in link was accepted twice")

    def test_two_links_for_one_address_are_distinct(self):
        a = access.issue_link_token("a@b.com")
        time.sleep(0.001)
        b = access.issue_link_token("a@b.com")
        self.assertNotEqual(a, b)
        self.assertEqual(access.consume_link_token(a), "a@b.com")
        self.assertEqual(access.consume_link_token(b), "a@b.com")

    def test_a_tampered_link_is_refused(self):
        tok = access.issue_link_token("a@b.com")
        self.assertIsNone(access.consume_link_token(tok[:-2] + "xy"))

    def test_a_session_token_is_not_a_link_token(self):
        """Different salts, so one cannot be presented as the other even though
        both are signed with the same secret."""
        tok = access._serializer(access.SESSION_SALT).dumps({"e": "a@b.com"})
        self.assertIsNone(access.consume_link_token(tok))


class VendorTroubleDoesNotLockPeopleOut(unittest.TestCase):
    """A 429 from beehiiv must not read as "not subscribed". Treating a vendor
    outage as a refusal turns their problem into yours, for paying readers."""

    def setUp(self):
        subscribers._CACHE.clear()
        subscribers._BACKOFF_UNTIL = 0.0

    def tearDown(self):
        subscribers._CACHE.clear()
        subscribers._BACKOFF_UNTIL = 0.0

    def test_a_stale_answer_is_served_when_the_api_fails(self):
        good = subscribers.Subscriber(email="a@b.com", level=AccessLevel.PREMIUM,
                                      found=True, status="active")
        now = time.time()
        subscribers._CACHE["a@b.com"] = subscribers._Entry(
            sub=good, fresh_until=now - 1, stale_until=now + 3600)
        orig = subscribers._fetch
        subscribers._fetch = lambda e: (_ for _ in ()).throw(RuntimeError("429"))
        try:
            got = subscribers.lookup("a@b.com")
        finally:
            subscribers._fetch = orig
        self.assertIs(got.level, AccessLevel.PREMIUM)
        self.assertTrue(got.stale, "a stale answer should say that it is stale")

    def test_with_nothing_cached_it_refuses_rather_than_guessing(self):
        orig = subscribers._fetch
        subscribers._fetch = lambda e: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            got = subscribers.lookup("nobody@b.com")
        finally:
            subscribers._fetch = orig
        self.assertIs(got.level, AccessLevel.NONE)
        self.assertTrue(got.error)
        ok, _, _ = access.decide(got, AccessLevel.FREE)
        self.assertFalse(ok)

    def test_a_negative_answer_is_cached_briefly_not_for_the_full_ttl(self):
        """Somebody who subscribes after a failed attempt should not be locked
        out by their own first try."""
        from core.config import settings
        self.assertLess(settings.miss_ttl, settings.hit_ttl)


class ABadLevelNameIsRefusedNotDefaulted(unittest.TestCase):

    def test_a_typo_does_not_become_the_most_permissive_level(self):
        with self.assertRaises(ValueError):
            AccessLevel.parse("premiun")
        with self.assertRaises(ValueError):
            AccessLevel.parse("")

    def test_the_names_round_trip(self):
        for name in ("free", "premium", "pro"):
            self.assertEqual(AccessLevel.parse(name).name.lower(), name)


if __name__ == "__main__":
    unittest.main()
