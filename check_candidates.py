"""The Checker: check_candidates(candidates, request) -> kept restaurants, removals with reasons.

Chooses the checks the request needs, reads Google Places live (check_place) and the county
inspection record (get_inspection), removes candidates that fail and records why.

Rules
  * Always: business status. CLOSED_PERMANENTLY / CLOSED_TEMPORARILY are removed.
  * If "when" gives a day and time: removed if Google's hours say closed then.
  * Dine in / Pickup / Delivery: removed only if Google explicitly says the service is not offered.
  * Google not saying (None) never removes. A failed Google call keeps the restaurant with
    verified=False, so an outage can't silently empty the results.

Usage (chains B's tool, so it needs B's OpenAI key and chroma_db/):
    python check_candidates.py "Six of us, Friday at 7, Thai or Vietnamese, around $20 each, one vegetarian" \
        --mode "Dine in" --when "Friday 7 PM" --max-price 20
    python check_candidates.py --sample --mode "Dine in" --when "Friday 7 PM"   # B's output from a saved file
"""
import argparse
import json
from datetime import datetime

from action_links import choose_action_link, load_action_links, normalize_mode
from statements import ALLERGY_NOTE, INSPECTION_NOTE, source_note
from timecheck import TZ, describe_time, open_at, parse_when

SERVICE_FOR_MODE = {"dine_in": "dine_in", "pickup": "takeout", "delivery": "delivery"}
SERVICE_WORDS = {"dine_in": "dine-in", "takeout": "pickup (takeout)", "delivery": "delivery"}
CLOSED_STATUS = {"CLOSED_PERMANENTLY": "permanently closed", "CLOSED_TEMPORARILY": "temporarily closed"}
SAMPLE_PATH = "tests/fixtures/scout_output.json"


# ---------- pure ----------
def candidate_list(candidates):
    """Accept find_restaurants() output (dict) or its "restaurants" list."""
    if isinstance(candidates, dict):
        return list(candidates.get("restaurants") or [])
    return list(candidates or [])


def checks_for(request_mode, when_dt):
    run = ["business_status"]
    if when_dt is not None:
        run.append("open_at_time")
    mode = normalize_mode(request_mode)
    if mode:
        run.append(SERVICE_FOR_MODE[mode])
    run.append("inspection")
    return run


def evaluate(place, when_dt, mode, now):
    """One restaurant against the Google facts. Returns (removal reason or None, checks dict)."""
    if not place or not place.get("ok"):
        error = (place or {}).get("error", "unknown error")
        return None, {
            "verified": False, "business_status": None, "hours_text": [],
            "open_at_requested_time": None,
            "services": {"dine_in": None, "takeout": None, "delivery": None, "reservable": None},
            "notes": [f"Not checked with Google Places ({error}). Please confirm hours and "
                      "services with the restaurant by phone."]}

    notes = []
    status = place.get("business_status")
    services = dict(place.get("services") or {})
    checks = {"verified": True, "business_status": status, "hours_text": place.get("hours_text") or [],
              "open_at_requested_time": None, "services": services, "notes": notes}

    if status in CLOSED_STATUS:
        return f"Google Places lists it as {CLOSED_STATUS[status]}.", checks
    if status != "OPERATIONAL":
        notes.append("Google Places does not give a business status; confirm it is open by phone.")

    if when_dt is not None:
        is_open = open_at(place, when_dt, now)
        checks["open_at_requested_time"] = is_open
        if is_open is False:
            return f"Closed {describe_time(when_dt)} according to Google Places.", checks
        if is_open is None:
            notes.append("Hours not listed on Google; confirm by phone.")
    if not checks["hours_text"] and "Hours not listed on Google; confirm by phone." not in notes:
        notes.append("Hours not listed on Google; confirm by phone.")

    service = SERVICE_FOR_MODE.get(mode)
    if service:
        offered = services.get(service)
        if offered is False:
            return f"Google Places says it does not offer {SERVICE_WORDS[service]}.", checks
        if offered is None:
            notes.append(f"Google Places does not say whether it offers {SERVICE_WORDS[service]}; "
                         "confirm with the restaurant.")
    return None, checks


def _safe_call(fn, rid, fallback):
    try:
        return fn(rid)
    except Exception as e:
        return fallback(e)


# ---------- the tool ----------
def check_candidates(candidates, request, place_lookup=None, inspection_lookup=None,
                     links=None, last_verified=None, now=None):
    """Check the Scout's candidates against live data.

    candidates: find_restaurants() output (dict) or its "restaurants" list.
    request:    the Host's request dict (party, when, mode, budget, cuisine, diet).
    The keyword arguments are for tests; the defaults call Google, the county data and our CSVs.
    Returns {"restaurants", "removed", "checks_run", "checked_at", "source_note", "allergy_note",
             "inspection_note", "when_note"}.
    """
    if place_lookup is None:
        from places import check_place as place_lookup
    if inspection_lookup is None:
        from inspections import get_inspection as inspection_lookup
    if links is None:
        links = load_action_links()
    if last_verified is None:
        from audit import load_last_verified
        last_verified = load_last_verified()

    request = request or {}
    now = now or datetime.now(TZ)
    when_dt, when_note = parse_when(request.get("when", ""), now)
    mode = normalize_mode(request.get("mode"))

    kept, removed, any_verified = [], [], False
    for r in candidate_list(candidates):
        rid = int(r["restaurant_id"])
        place = _safe_call(place_lookup, rid,
                           lambda e: {"ok": False, "error": f"check failed ({type(e).__name__})"})
        reason, checks = evaluate(place, when_dt, mode, now)
        any_verified = any_verified or checks["verified"]
        if reason:
            removed.append({"restaurant_id": rid, "name": r.get("name", ""), "reason": reason})
            continue

        if when_dt is None:
            checks["notes"].append(when_note)
        checks["action_link"] = choose_action_link(mode, r, links.get(rid), place)
        if checks["action_link"] is None:
            checks["notes"].append("No reservation, ordering or phone link is on file.")
        checks["inspection"] = _safe_call(inspection_lookup, rid, lambda e: None)
        if checks["inspection"] is None:
            checks["notes"].append("No county inspection record was found for this restaurant.")
        checks["last_verified"] = last_verified.get(rid)
        kept.append({**r, "checks": checks})

    checked_at = now.isoformat(timespec="seconds")
    return {
        "restaurants": kept,
        "removed": removed,
        "checks_run": checks_for(request.get("mode"), when_dt),
        "checked_at": checked_at,
        "when_note": when_note,
        "source_note": source_note(checked_at, any_verified or not (kept or removed)),
        "allergy_note": ALLERGY_NOTE,
        "inspection_note": INSPECTION_NOTE,
    }


# ---------- CLI ----------
def print_result(out):
    print(f"Checks run: {', '.join(out['checks_run'])}")
    print(f"Time: {out['when_note']}\n")
    for r in out["restaurants"]:
        c = r["checks"]
        link = c["action_link"]
        print(f"KEPT  {r['name']}  (menu collected {r.get('menu_collected', '?')})")
        print(f"      verified: {c['verified']}   status: {c['business_status']}   "
              f"open then: {c['open_at_requested_time']}")
        print(f"      services: {c['services']}")
        print(f"      link: {link['label'] + ' -> ' + link['url'] if link else 'none'}")
        if c["inspection"]:
            ins = c["inspection"]
            print(f"      inspection: {ins['date']}, {len(ins['violations'])} violations listed  {ins['record_url']}")
        for d in r.get("dishes", []):
            print(f"        - {d['name']}  {d['price']}" + (f"  [menu says: {d['menu_says']}]" if d.get("menu_says") else ""))
        for n in c["notes"]:
            print(f"      note: {n}")
    for x in out["removed"]:
        print(f"REMOVED  {x['name']}: {x['reason']}")
    print(f"\n{out['source_note']}\n{out['allergy_note']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?", default="")
    parser.add_argument("--mode", default="Dine in")
    parser.add_argument("--when", default="")
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--sample", action="store_true", help=f"use B's output saved in {SAMPLE_PATH}")
    args = parser.parse_args()

    if args.sample:
        with open(SAMPLE_PATH, encoding="utf-8") as f:
            scout = json.load(f)
    else:
        from find_restaurants import find_restaurants
        scout = find_restaurants(args.request, args.max_price)
    request = {"mode": args.mode, "when": args.when, "budget": args.max_price}
    print_result(check_candidates(scout, request))
