"""Part C tests. Fakes stand in for Google Places, Socrata and web pages: no network, no key.

Run from the repo root:
    python -m unittest discover -s tests -v
"""
import ast
import copy
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import action_links  # noqa: E402
import audit  # noqa: E402
import inspections  # noqa: E402
import places  # noqa: E402
import resolve_place_ids  # noqa: E402
import statements  # noqa: E402
from check_candidates import check_candidates, evaluate  # noqa: E402
from timecheck import TZ, describe_time, is_open_at, open_at, parse_when  # noqa: E402

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=TZ)          # a Sunday afternoon
FRIDAY_7PM = datetime(2026, 11, 27, 19, 0, tzinfo=TZ)
SCOUT = json.loads((ROOT / "tests/fixtures/scout_output.json").read_text(encoding="utf-8"))
BANNED = re.compile(r"\b(safe|safely|allergen[- ]free|guaranteed?|no risk)\b", re.IGNORECASE)
PART_C_MODULES = ["action_links.py", "audit.py", "check_candidates.py", "inspections.py",
                  "matching.py", "places.py", "resolve_place_ids.py", "statements.py", "timecheck.py",
                  "checker_tool.py"]


# ---------- fakes ----------
def daily(open_hour, close_hour, days=range(7)):
    """Google weekly periods. close_hour <= open_hour means it closes after midnight."""
    out = []
    for d in days:
        close_day = d if close_hour > open_hour else (d + 1) % 7
        out.append({"open": {"day": d, "hour": open_hour, "minute": 0},
                    "close": {"day": close_day, "hour": close_hour, "minute": 0}})
    return out


def google_json(status="OPERATIONAL", periods=None, dine_in=True, takeout=True, delivery=None,
                reservable=None):
    data = {"businessStatus": status, "dineIn": dine_in, "takeout": takeout, "delivery": delivery,
            "reservable": reservable, "nationalPhoneNumber": "(315) 555-0100",
            "websiteUri": "https://example.com/", "googleMapsUri": "https://maps.google.com/?cid=1"}
    if periods is not None:
        data["regularOpeningHours"] = {"periods": periods,
                                       "weekdayDescriptions": [f"{d}: 11:00 AM – 9:00 PM" for d in
                                                               ["Monday", "Tuesday", "Wednesday", "Thursday",
                                                                "Friday", "Saturday", "Sunday"]]}
    return {k: v for k, v in data.items() if v is not None}


def place(**kw):
    return places.summarize_place(google_json(**kw), 0, "fake-place-id", NOW)


def lookup_from(table):
    """table: {restaurant_id: place dict}; anything missing fails like an outage."""
    return lambda rid: table.get(rid, {"ok": False, "error": "Google Places call failed (Timeout)"})


def run(candidates, request, table, links=None, inspection=None):
    return check_candidates(candidates, request, place_lookup=lookup_from(table),
                            inspection_lookup=inspection or (lambda rid: None),
                            links=links or {}, last_verified={}, now=NOW)


ALL_OPEN = {r["restaurant_id"]: place(periods=daily(11, 21)) for r in SCOUT["restaurants"]}


# ---------- time parsing ----------
class ParseWhen(unittest.TestCase):
    def check(self, text, expected):
        got, _ = parse_when(text, NOW)
        self.assertEqual(got, expected, text)

    def test_examples(self):
        self.check("Friday 7 PM", FRIDAY_7PM)
        self.check("Fri 7pm", FRIDAY_7PM)
        self.check("friday 7:00 p.m.", FRIDAY_7PM)
        self.check("tonight 8", datetime(2026, 11, 22, 20, 0, tzinfo=TZ))
        self.check("tomorrow at noon", datetime(2026, 11, 23, 12, 0, tzinfo=TZ))
        self.check("now", NOW)
        self.check("Saturday 19:00", datetime(2026, 11, 28, 19, 0, tzinfo=TZ))
        self.check("Friday 7:30 pm", datetime(2026, 11, 27, 19, 30, tzinfo=TZ))

    def test_bare_hour_is_read_as_pm_and_said(self):
        got, note = parse_when("Friday at 7", NOW)
        self.assertEqual(got, FRIDAY_7PM)
        self.assertIn("read as PM", note)

    def test_same_day_already_passed_means_next_week(self):
        self.check("Sunday 2 PM", datetime(2026, 11, 29, 14, 0, tzinfo=TZ))
        self.check("Sunday 6 PM", datetime(2026, 11, 22, 18, 0, tzinfo=TZ))

    def test_unreadable_or_blank_gives_none_and_a_note(self):
        for text in ["", "   ", None, "sometime soon", "Friday"]:
            got, note = parse_when(text, NOW)
            self.assertIsNone(got, text)
            self.assertIn("not checked", note)

    def test_describe(self):
        self.assertEqual(describe_time(FRIDAY_7PM), "Friday at 7 PM")
        self.assertEqual(describe_time(datetime(2026, 11, 27, 12, 30, tzinfo=TZ)), "Friday at 12:30 PM")


# ---------- opening hours ----------
class Hours(unittest.TestCase):
    def test_simple_day(self):
        p = daily(11, 21)
        self.assertTrue(is_open_at(p, FRIDAY_7PM))
        self.assertFalse(is_open_at(p, datetime(2026, 11, 27, 22, 0, tzinfo=TZ)))
        self.assertFalse(is_open_at(p, datetime(2026, 11, 27, 10, 59, tzinfo=TZ)))

    def test_past_midnight(self):
        p = daily(17, 2, days=[5, 6])          # Fri 5 PM - Sat 2 AM, Sat 5 PM - Sun 2 AM
        self.assertTrue(is_open_at(p, datetime(2026, 11, 28, 1, 30, tzinfo=TZ)))     # Sat 1:30 AM
        self.assertFalse(is_open_at(p, datetime(2026, 11, 28, 3, 0, tzinfo=TZ)))     # Sat 3 AM
        self.assertTrue(is_open_at(p, datetime(2026, 11, 29, 1, 0, tzinfo=TZ)))      # Sun 1 AM
        self.assertTrue(is_open_at(p, datetime(2026, 11, 22, 1, 0, tzinfo=TZ)))      # Sun 1 AM, week before

    def test_sunday_night_into_monday_wraps_the_week(self):
        p = daily(22, 2, days=[0])             # Sun 10 PM - Mon 2 AM
        self.assertTrue(is_open_at(p, datetime(2026, 11, 23, 1, 0, tzinfo=TZ)))      # Mon 1 AM
        self.assertTrue(is_open_at(p, datetime(2026, 11, 22, 23, 0, tzinfo=TZ)))     # Sun 11 PM
        self.assertFalse(is_open_at(p, datetime(2026, 11, 23, 3, 0, tzinfo=TZ)))

    def test_always_open_and_no_hours(self):
        self.assertTrue(is_open_at([{"open": {"day": 0, "hour": 0, "minute": 0}}], FRIDAY_7PM))
        self.assertIsNone(is_open_at([], FRIDAY_7PM))

    def test_current_hours_override_for_a_holiday(self):
        thanksgiving = datetime(2026, 11, 26, 19, 0, tzinfo=TZ)
        dated = [{"open": {"day": 5, "hour": 11, "minute": 0, "date": {"year": 2026, "month": 11, "day": 27}},
                  "close": {"day": 5, "hour": 21, "minute": 0, "date": {"year": 2026, "month": 11, "day": 27}}}]
        p = {"regular_periods": daily(11, 21), "current_periods": dated}
        self.assertFalse(open_at(p, thanksgiving, NOW))      # closed Thursday in current hours
        self.assertTrue(open_at(p, FRIDAY_7PM, NOW))


# ---------- check_place ----------
class CheckPlace(unittest.TestCase):
    def setUp(self):
        places.clear_cache()

    def test_success_and_cache(self):
        calls = []

        def fetch(pid, key):
            calls.append(pid)
            return google_json(periods=daily(11, 21), reservable=True)
        r1 = places.check_place(1, fetch=fetch, key="k", place_ids={1: "abc"}, now=NOW)
        r2 = places.check_place(1, fetch=fetch, key="k", place_ids={1: "abc"}, now=NOW)
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["services"]["reservable"], True)
        self.assertEqual(r1, r2)
        self.assertEqual(calls, ["abc"])

    def test_failures_are_reported_not_raised_or_cached(self):
        def boom(pid, key):
            raise TimeoutError("slow")
        r = places.check_place(2, fetch=boom, key="k", place_ids={2: "abc"})
        self.assertFalse(r["ok"])
        self.assertIn("TimeoutError", r["error"])
        self.assertFalse(places.check_place(3, key="k", place_ids={})["ok"])
        self.assertEqual(places._cache, {})

    def test_only_the_small_field_mask(self):
        self.assertNotIn("*", places.FIELD_MASK)
        self.assertEqual(len(places.FIELD_MASK.split(",")), 10)


# ---------- check_candidates ----------
class Checker(unittest.TestCase):
    def test_accepts_both_shapes_and_keeps_b_order(self):
        req = {"mode": "Dine in", "when": "Friday 7 PM"}
        a = run(SCOUT, req, ALL_OPEN)
        b = run(SCOUT["restaurants"], req, ALL_OPEN)
        self.assertEqual(a, b)
        self.assertEqual([r["restaurant_id"] for r in a["restaurants"]],
                         [r["restaurant_id"] for r in SCOUT["restaurants"]])

    def test_b_records_unchanged_plus_checks(self):
        before = copy.deepcopy(SCOUT)
        out = run(SCOUT, {"mode": "Dine in"}, ALL_OPEN)
        self.assertEqual(SCOUT, before)                       # input not mutated
        for kept, orig in zip(out["restaurants"], SCOUT["restaurants"]):
            self.assertEqual({k: v for k, v in kept.items() if k != "checks"}, orig)
            for key in ["verified", "business_status", "hours_text", "open_at_requested_time",
                        "services", "action_link", "inspection", "notes"]:
                self.assertIn(key, kept["checks"])

    def test_closed_status_removed_with_reason(self):
        table = dict(ALL_OPEN)
        table[8] = place(status="CLOSED_PERMANENTLY", periods=daily(11, 21))
        table[2] = place(status="CLOSED_TEMPORARILY", periods=daily(11, 21))
        out = run(SCOUT, {"mode": "Dine in"}, table)
        reasons = {x["restaurant_id"]: x["reason"] for x in out["removed"]}
        self.assertEqual(reasons[8], "Google Places lists it as permanently closed.")
        self.assertEqual(reasons[2], "Google Places lists it as temporarily closed.")

    def test_closed_at_requested_time_removed(self):
        table = dict(ALL_OPEN)
        table[22] = place(periods=daily(11, 17))
        out = run(SCOUT, {"mode": "Dine in", "when": "Friday 7 PM"}, table)
        self.assertEqual(out["removed"], [{"restaurant_id": 22, "name": "The Evergreen",
                                           "reason": "Closed Friday at 7 PM according to Google Places."}])
        self.assertIn("open_at_time", out["checks_run"])

    def test_no_time_skips_the_hours_check_and_says_so(self):
        table = dict(ALL_OPEN)
        table[22] = place(periods=daily(11, 17))
        out = run(SCOUT, {"mode": "Dine in", "when": ""}, table)
        self.assertEqual(out["removed"], [])
        self.assertNotIn("open_at_time", out["checks_run"])
        self.assertTrue(any("not checked" in n for n in out["restaurants"][0]["checks"]["notes"]))

    def test_each_mode_removes_only_on_explicit_false(self):
        cases = [("Dine in", "dine_in", "dine-in"), ("Pickup", "takeout", "pickup"),
                 ("Delivery", "delivery", "delivery")]
        for mode, field, word in cases:
            kw = {"dine_in": True, "takeout": True, "delivery": True}
            kw[field] = False
            table = dict(ALL_OPEN)
            table[7] = place(periods=daily(11, 21), **kw)
            out = run(SCOUT, {"mode": mode}, table)
            self.assertEqual([x["restaurant_id"] for x in out["removed"]], [7], mode)
            self.assertIn(word, out["removed"][0]["reason"])
            self.assertIn(field, out["checks_run"])
            # the other modes don't care about this field
            for other, _, _ in cases:
                if other != mode:
                    self.assertEqual(run(SCOUT, {"mode": other}, table)["removed"], [], (mode, other))

    def test_none_never_removes(self):
        unknown = place(periods=None, dine_in=None, takeout=None, delivery=None)
        unknown["business_status"] = None
        table = {r["restaurant_id"]: unknown for r in SCOUT["restaurants"]}
        for mode in ["Dine in", "Pickup", "Delivery"]:
            out = run(SCOUT, {"mode": mode, "when": "Friday 7 PM"}, table)
            self.assertEqual(out["removed"], [], mode)
            c = out["restaurants"][0]["checks"]
            self.assertIsNone(c["open_at_requested_time"])
            self.assertIn("Hours not listed on Google; confirm by phone.", c["notes"])
            self.assertTrue(any("does not say" in n for n in c["notes"]))

    def test_google_outage_keeps_everyone_unverified(self):
        def raises(rid):
            raise ConnectionError("down")
        for lookup in (lookup_from({}), raises):
            out = check_candidates(SCOUT, {"mode": "Dine in", "when": "Friday 7 PM"}, place_lookup=lookup,
                                   inspection_lookup=lambda rid: None, links={}, last_verified={}, now=NOW)
            self.assertEqual(len(out["restaurants"]), len(SCOUT["restaurants"]))
            self.assertEqual(out["removed"], [])
            self.assertTrue(all(r["checks"]["verified"] is False for r in out["restaurants"]))
            self.assertEqual(out["source_note"], statements.SOURCE_NOTE_UNCHECKED)
            self.assertTrue(all(r["checks"]["action_link"] for r in out["restaurants"]))  # phone still works

    def test_statements_and_timestamp(self):
        out = run(SCOUT, {"mode": "Pickup"}, ALL_OPEN)
        self.assertEqual(out["checked_at"], "2026-11-22T15:00:00-05:00")
        self.assertIn("Google Places, checked at Nov 22, 2026 at 3:00 PM", out["source_note"])
        self.assertIn("out of date", out["source_note"])
        self.assertIn("online ordering is paused", out["source_note"])
        self.assertEqual(out["allergy_note"], statements.ALLERGY_NOTE)

    def test_empty_input(self):
        out = run({"restaurants": [], "reranked": True, "relaxed": None}, {}, {})
        self.assertEqual((out["restaurants"], out["removed"]), ([], []))

    def test_inspection_and_last_verified_attached(self):
        ins = {"date": "2026-09-30", "violations": [], "critical_count": 0, "record_url": "u", "source": "s"}
        out = check_candidates(SCOUT, {"mode": "Dine in"}, place_lookup=lookup_from(ALL_OPEN),
                               inspection_lookup=lambda rid: ins if rid == 8 else None, links={},
                               last_verified={8: "2026-12-01"}, now=NOW)
        first = out["restaurants"][0]["checks"]
        self.assertEqual(first["inspection"], ins)
        self.assertEqual(first["last_verified"], "2026-12-01")
        self.assertIsNone(out["restaurants"][1]["checks"]["inspection"])


# ---------- action links ----------
class Links(unittest.TestCase):
    R = {"website": "https://site.example/", "phone": "(315) 424-1974"}

    def test_dine_in_order(self):
        g_yes = place(periods=daily(11, 21), reservable=True)
        self.assertEqual(action_links.choose_action_link("Dine in", self.R, {"reservation_url": "https://r"}, g_yes),
                         {"type": "reserve", "label": "Reserve on their site", "url": "https://r"})
        self.assertEqual(action_links.choose_action_link("Dine in", self.R, {}, g_yes)["url"], "https://site.example/")
        call = action_links.choose_action_link("Dine in", self.R, {}, place(periods=daily(11, 21)))
        self.assertEqual(call, {"type": "call", "label": "Call to reserve: (315) 424-1974",
                                "url": "tel:+13154241974"})

    def test_pickup_own_order_page_first(self):
        link = action_links.choose_action_link("Pickup", self.R, {"order_url": "https://order",
                                                                  "delivery_url": "https://deliv"}, None)
        self.assertEqual(link, {"type": "order", "label": "Order pickup on their site", "url": "https://order"})
        self.assertEqual(action_links.choose_action_link("Pickup", self.R, {}, None)["type"], "call")

    def test_delivery_order(self):
        delivers = place(periods=daily(11, 21), delivery=True)
        unknown = place(periods=daily(11, 21))
        both = {"order_url": "https://order", "delivery_url": "https://deliv"}
        self.assertEqual(action_links.choose_action_link("Delivery", self.R, both, unknown)["url"], "https://deliv")
        self.assertEqual(action_links.choose_action_link("Delivery", self.R, {"order_url": "https://order"},
                                                         delivers)["url"], "https://order")
        self.assertEqual(action_links.choose_action_link("Delivery", self.R, {"order_url": "https://order"},
                                                         unknown)["type"], "call")

    def test_prefill_from_menu_url(self):
        toast_item = ("https://www.toasttab.com/local/order/three-lives/r-3ffb55b1-0bbf-4028-a9a4-45e803f9e3a5/"
                      "item-star-wars-churros-6_4fb665a9-0706-45f9-abd7-fc8b03ad3f5f")
        self.assertEqual(action_links.prefill_row(5, toast_item)["order_url"],
                         "https://www.toasttab.com/local/order/three-lives/r-3ffb55b1-0bbf-4028-a9a4-45e803f9e3a5")
        self.assertEqual(action_links.prefill_row(1, "https://kittyhoynes.com/")["order_url"], "")
        self.assertEqual(action_links.prefill_row(7, "https://menupages.com/byblos")["order_url"], "")
        for url in ["https://order.online/store/34528579", "https://toast.app/r/x/order",
                    "https://the-fish-friar.square.site/s/order"]:
            self.assertTrue(action_links.is_order_page(url), url)

    def test_modes(self):
        self.assertEqual(action_links.normalize_mode("Dine in"), "dine_in")
        self.assertEqual(action_links.normalize_mode("dine-in"), "dine_in")
        self.assertEqual(action_links.normalize_mode("Pickup"), "pickup")
        self.assertEqual(action_links.normalize_mode("Delivery"), "delivery")
        self.assertIsNone(action_links.normalize_mode(""))


# ---------- wording ----------
def _strings(obj, skip=("dishes", "violations", "hours_text", "name")):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip:
                yield from _strings(v, skip)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, skip)


def _module_strings(path):
    """Every string literal in a module except docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class Wording(unittest.TestCase):
    def test_no_banned_words_in_any_part_c_output(self):
        produced = [statements.ALLERGY_NOTE, statements.SOURCE_NOTE, statements.SOURCE_NOTE_UNCHECKED,
                    statements.INSPECTION_NOTE]
        tables = [ALL_OPEN, {}, {r["restaurant_id"]: place(status="CLOSED_PERMANENTLY") for r in SCOUT["restaurants"]},
                  {r["restaurant_id"]: place(periods=daily(11, 17), dine_in=False, takeout=None) for r in SCOUT["restaurants"]}]
        for table in tables:
            for mode in ["Dine in", "Pickup", "Delivery", ""]:
                for when in ["Friday 7 PM", "", "whenever"]:
                    produced.extend(_strings(run(SCOUT, {"mode": mode, "when": when}, table)))
        for mode in ["Dine in", "Pickup", "Delivery"]:
            for links in [{}, {"reservation_url": "x", "order_url": "y", "delivery_url": "z"}]:
                produced.extend(_strings(action_links.choose_action_link(mode, {"phone": "3155550100"}, links)))
        for path in PART_C_MODULES:
            if (ROOT / path).exists():
                produced.extend(_module_strings(ROOT / path))
        bad = [s for s in produced if BANNED.search(s)]
        self.assertEqual(bad, [])

    def test_the_banned_pattern_works(self):
        for s in ["This dish is safe", "allergen-free", "Guaranteed fresh", "no risk at all"]:
            self.assertTrue(BANNED.search(s), s)


# ---------- inspections ----------
COUNTY_ROW = {"facility": "Kitty Hoynes Irish Pub & Restaurant", "address": "301 WEST Fayette STREET,  Syracuse",
              "date": "2026-09-30T00:00:00.000",
              "violations": "Item  1H-  Critical Violation [RED] Food from unapproved source; Item 11D-   Non food contact surfaces of equipment not clean;",
              "total_critical_violations": "1", "nys_health_operation_id": "294363"}
FACILITIES = [
    {"nys_health_operation_id": "294363", "facility": "Kitty Hoynes Irish Pub & Restaurant", "address": "301 WEST Fayette STREET,  Syracuse"},
    {"nys_health_operation_id": "1032817", "facility": "Eden Hospitality", "address": "118 EAST Genesee STREET,  Syracuse"},
    {"nys_health_operation_id": "1159636", "facility": "Big Fellas", "address": "208 WEST Water STREET,  Syracuse"},
    {"nys_health_operation_id": "1202047", "facility": "Poundtown Burger Bar", "address": "208 WEST Water STREET,  Syracuse"},
    {"nys_health_operation_id": "1160081", "facility": "Mr. Pho", "address": "216 Walton STREET,  Syracuse"},
    {"nys_health_operation_id": "884397", "facility": "Funk 'n Waffles", "address": "307-313 SOUTH Clinton STREET,  Syracuse"},
]


class Inspections(unittest.TestCase):
    def setUp(self):
        inspections.clear_cache()

    def test_summary_has_date_violations_link_and_no_score(self):
        s = inspections.summarize_inspection(COUNTY_ROW)
        self.assertEqual(s["date"], "2026-09-30")
        self.assertEqual(s["violations"], ["Item 1H- Critical Violation [RED] Food from unapproved source",
                                           "Item 11D- Non food contact surfaces of equipment not clean"])
        self.assertEqual(s["critical_count"], 1)
        self.assertIn("nys_health_operation_id=294363", s["record_url"])
        self.assertEqual(s["source"], "Onondaga County Health Department via health.data.ny.gov")
        self.assertFalse(any(k in s for k in ("score", "grade", "rating")))
        clean = dict(COUNTY_ROW, violations="No violations found.", total_critical_violations="0")
        self.assertEqual(inspections.summarize_inspection(clean)["violations"], [])

    def test_semicolon_inside_one_violation_is_not_a_new_violation(self):
        text = ("Item  8C-   Improper use and storage of clean, sanitized equipment and utensils; Item 11A-   "
                "Manual facilities inadequate, technique incorrect; mechanical facilities not operated in "
                "accordance with manufacturer's instructions; Item 11D-   Non food contact surfaces of equipment not clean")
        self.assertEqual(inspections.split_violations(text), [
            "Item 8C- Improper use and storage of clean, sanitized equipment and utensils",
            "Item 11A- Manual facilities inadequate, technique incorrect; mechanical facilities not "
            "operated in accordance with manufacturer's instructions",
            "Item 11D- Non food contact surfaces of equipment not clean"])

    def test_matching(self):
        def m(name, address):
            hit, review = inspections.match_restaurant({"name": name, "address": address}, FACILITIES)
            return (hit or {}).get("nys_health_operation_id"), review
        self.assertEqual(m("Kitty Hoynes", "301 W. Fayette St."), ("294363", ""))
        self.assertEqual(m("Eden", "118 E. Genesee St."), ("1032817", ""))
        self.assertEqual(m("Big Fella’s", "208 W. Water St."), ("1159636", ""))
        self.assertEqual(m("Funk ’n Waffles", "307 S. Clinton St."), ("884397", ""))
        rid, review = m("Dakshin Indian Cuisine", "216 Walton St.")
        self.assertIsNone(rid)
        self.assertIn("Mr. Pho", review)

    def test_get_inspection(self):
        got = inspections.get_inspection(1, fetch=lambda op: COUNTY_ROW, ids={1: "294363"})
        self.assertEqual(got["date"], "2026-09-30")
        self.assertIsNone(inspections.get_inspection(17, fetch=lambda op: COUNTY_ROW, ids={}))

        def down(op):
            raise ConnectionError()
        self.assertIsNone(inspections.get_inspection(2, fetch=down, ids={2: "1"}))


# ---------- place ID review ----------
class Resolve(unittest.TestCase):
    def test_review_reason(self):
        row = {"name": "Dakshin Indian Cuisine", "address": "216 Walton St."}
        ok = {"id": "x", "displayName": {"text": "Dakshin Indian Cuisine"},
              "formattedAddress": "216 Walton St, Syracuse, NY 13202, USA"}
        moved = dict(ok, formattedAddress="218 Walton St, Syracuse, NY 13202, USA")
        other = dict(ok, displayName={"text": "Mr. Pho"})
        self.assertEqual(resolve_place_ids.review_reason(row, ok), "")
        self.assertEqual(resolve_place_ids.review_reason(row, moved), "address differs")
        self.assertEqual(resolve_place_ids.review_reason(row, other), "name differs")
        self.assertEqual(resolve_place_ids.review_reason(row, None), "no result")
        self.assertEqual(resolve_place_ids.resolve_row({**row, "restaurant_id": 17}, [], "2026-11-22")["place_id"], "")


# ---------- the Auditor ----------
MENU = "<html><body><script>var x=1;</script>" + "<p>Pad Thai $12</p>" * 60 + "</body></html>"


class Auditor(unittest.TestCase):
    def test_menu_findings(self):
        url = "https://example.com/menu"
        _, _, h = audit.menu_finding(url, MENU, None, None)
        self.assertIsNotNone(h)
        prev = {"menu_url": url, "sha256": h, "checked_on": "2026-11-01"}
        self.assertIsNone(audit.menu_finding(url, MENU, None, prev)[0])
        self.assertIsNone(audit.menu_finding(url, MENU.replace("var x=1", "var x=2"), None, prev)[0])
        changed = audit.menu_finding(url, MENU.replace("$12", "$13"), None, prev)[0]
        self.assertIn("changed since 2026-11-01", changed)
        self.assertIn("check by hand", audit.menu_finding(url, None, "HTTPError", prev)[0])
        self.assertIn("check by hand", audit.menu_finding("https://www.toasttab.com/x", MENU, None, None)[0])
        self.assertIn("check by hand", audit.menu_finding(url, "<div id=root></div>", None, None)[0])

    def test_status_findings(self):
        self.assertIsNone(audit.status_finding(place(periods=daily(11, 21))))
        self.assertEqual(audit.status_finding(place(status="CLOSED_PERMANENTLY"))[0], "business_status")
        self.assertEqual(audit.status_finding({"ok": False, "error": "no Google place ID stored for this restaurant"})[0],
                         "place_id")
        self.assertIn("Could not check", audit.status_finding({"ok": False, "error": "timeout"})[1])

    def test_run_audit(self):
        df = pd.DataFrame([{"restaurant_id": 5, "name": "Three Lives", "menu_url": "https://www.toasttab.com/x"},
                           {"restaurant_id": 6, "name": "Margaritas", "menu_url": "https://m.example/menu"},
                           {"restaurant_id": 7, "name": "Byblos", "menu_url": "https://b.example/menu"}])
        table = {5: place(), 6: place(status="CLOSED_PERMANENTLY"), 7: place()}
        _, _, old_hash = audit.menu_finding("https://b.example/menu", MENU, None, None)
        hashes = {7: {"restaurant_id": 7, "menu_url": "https://b.example/menu", "sha256": old_hash, "checked_on": "2026-11-01"}}
        reports = [{"restaurant_id": "7", "reported_on": "2026-11-20", "text": "Closed early"},
                   {"restaurant_id": "6", "reported_on": "2026-10-01", "text": "old report"}]
        pages = {"https://m.example/menu": MENU, "https://b.example/menu": MENU.replace("$12", "$14")}
        items, new_hashes = audit.run_audit(df, lambda rid: table[rid], lambda u: pages[u], hashes, reports,
                                            "2026-12-01", since="2026-11-01")
        found = {(i["restaurant_id"], i["item"]) for i in items}
        self.assertEqual(found, {(5, "menu"), (6, "business_status"), (7, "menu"), (7, "user report"),
                                 ("", "restaurant list")})
        self.assertEqual(new_hashes[7]["checked_on"], "2026-12-01")
        self.assertIn(6, new_hashes)            # baseline stored, not flagged
        self.assertNotIn(5, new_hashes)         # Toast: nothing to compare

    def test_log_and_last_verified(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "audit_log.csv"
            audit.append_csv(log, audit.LOG_COLUMNS, {"date": "2026-12-01", "restaurant_id": "6", "item": "business_status",
                                                      "finding": "closed", "action_taken": "removed", "checked_by": "Ashish"})
            audit.append_csv(log, audit.LOG_COLUMNS, {"date": "2026-12-03", "restaurant_id": "6", "item": "menu",
                                                      "finding": "same", "action_taken": "no change", "checked_by": "Ashish"})
            audit.append_csv(log, audit.LOG_COLUMNS, {"date": "2026-12-02", "restaurant_id": "", "item": "restaurant list",
                                                      "finding": "matches guide", "action_taken": "no change", "checked_by": "Ashish"})
            self.assertEqual(len(audit.read_csv(log)), 3)
            self.assertEqual(audit.load_last_verified(log), {6: "2026-12-03"})


if __name__ == "__main__":
    unittest.main()
