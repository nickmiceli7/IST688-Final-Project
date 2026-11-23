# Ashish Chaudhary: Part C (Live Checks: Checker and Auditor)

**Status as of Nov 22, 2026:** all Part C code is written and passes 40 offline tests (fakes for Google, the county data and web pages). Inspections and action links work on real data. The Google Places calls are built but not yet run against Google, because the API key isn't set up yet. Code is on branch `part-c-checker`: `check_candidates.py`, `places.py`, `resolve_place_ids.py`, `timecheck.py`, `action_links.py`, `statements.py`, `inspections.py`, `audit.py`, `checker_tool.py`, `matching.py`, `tests/test_checker.py`.

## Completed
- `check_place(restaurant_id)`: reads Google Places (New) Place Details live with a small field mask (status, hours, dine-in, takeout, delivery, reservable, phone, website, Maps link). Results are cached in memory for 30 minutes. Nothing from Google is written to disk
- `resolve_place_ids.py`: finds each restaurant's place ID once with Text Search limited to downtown, and marks rows whose name or address doesn't clearly match A's list for review
- `check_candidates(candidates, request)`: chooses the checks the request needs, removes failing candidates and gives a plain-English reason for each one (e.g. "Closed Friday at 7 PM according to Google Places.")
  - Always checks business status. Checks hours only when "when" gives a day and time. Checks dine-in, takeout or delivery based on the mode
  - When Google doesn't say (None), the restaurant is never removed; it is kept with a note. When the Google call fails, the restaurant is kept with `verified: False`, so an outage can't empty the results
  - Reads "Friday 7 PM", "Fri 7pm", "tonight 8", "tomorrow at noon", "now", "Saturday 19:00" and blank. Handles hours that run past midnight. Uses Google's holiday-aware hours for the next 6 days (Thanksgiving week matters)
- Action links: one per restaurant from `action_links.csv` (our own file). Reservation page, website (if Google says reservable) or phone for dine-in; the restaurant's own ordering page first for pickup; delivery link for delivery. 10 ordering pages prefilled from A's menu URLs
- Allergy, source and inspection statements in `statements.py`, shown verbatim. A test scans every Part C string and fails if any describes a dish as risk-free
- `get_inspection(restaurant_id)`: date of the last county inspection, the violations as the county wrote them, the critical count the county labels, and a link to the record. No score or grade. 24 of 25 restaurants matched in `inspection_ids.csv`
- The Auditor (`audit.py`): flags missing place IDs, Google statuses other than OPERATIONAL, changed menu pages, user reports, and one item to compare the list with the current Downtown guide. Writes `audit/audit_list_<date>.csv`; verifications go to an append-only `audit/audit_log.csv`
- LangChain `StructuredTool` wrapper (`checker_tool.py`) so the Host can call the Checker as a tool

## Not done yet (planned by Dec 6)
- Run with a Google key: resolve place IDs, review them, and run `python places.py --first10` (the milestone demo)
- Fill and verify `action_links.csv` (reservation and delivery links are blank; the 10 prefilled ordering pages are not yet verified)
- Review Dakshin (17): no county inspection record, and **Mr. Pho** was inspected at its address (216 Walton) on Nov 3. It may have closed
- The audit run and its log (listed under Part A in the plan; the Auditor is ready for it)

## Changes from the Nov 15 plan
- Inspections come from the **Onondaga County Restaurant Inspections** dataset (`tbxv-5tbd` on health.data.ny.gov), because the county does its own inspections. The same 1,924 Onondaga records also appear in the statewide set (`cnih-y5dw`)
- The allergy statement ends "Grubbz cannot confirm what is in any dish." instead of the draft wording, so it passes our own banned-word test
- `check_candidates` returns a dict (kept, removed with reasons, checks run, statements), not a list
- Each kept restaurant includes `last_verified` (latest date in the audit log), for the "last verification date" the plan says each recommendation shows
- A time without AM or PM ("Friday at 7") is read as PM, and the note says so
- `place_ids.csv` keeps Google's matched name and address only until a person reviews the match; `python resolve_place_ids.py --strip` then removes them, so the repo stores only place IDs and our own columns

## Interface for Part D
`check_candidates(candidates, request)` takes B's whole `find_restaurants` output (or its `restaurants` list) and the Host's `ss.summary`. It returns:
- `restaurants`: B's records unchanged, in B's order, each with a `checks` dict: `verified`, `business_status`, `hours_text`, `open_at_requested_time`, `services`, `action_link` (`type`, `label`, `url`), `inspection` (`date`, `violations`, `critical_count`, `record_url`, `source`) or None, `last_verified`, `notes`
- `removed`: `restaurant_id`, `name`, `reason`
- `checks_run`, `checked_at`, `when_note`, `source_note`, `allergy_note`, `inspection_note`

Wiring it into `app.py`:
```python
from find_restaurants import find_restaurants
from check_candidates import check_candidates

def build_reply(request: dict) -> str:
    text = (f"Party of {request.get('party')}, {request.get('when')}, {request.get('mode')}, "
            f"about ${request.get('budget')} each, {request.get('cuisine')}, {request.get('diet')}")
    found = find_restaurants(text, max_price=request.get("budget") or None)
    checked = check_candidates(found, request)
    picks = checked["restaurants"][:3]
    if not picks:
        return "I couldn't find a match. Try a different cuisine, time or budget."
    lines = ["Here are my top picks:"]
    for r in picks:
        c = r["checks"]
        dishes = "; ".join(f"{d['name']} ({d['price']}"
                           + (f", menu says {d['menu_says']}" if d["menu_says"] else "") + ")"
                           for d in r["dishes"])
        link = f"[{c['action_link']['label']}]({c['action_link']['url']})" if c["action_link"] else ""
        hours = "; ".join(c["hours_text"]) or "hours not listed"
        lines.append(f"- **{r['name']}**: {dishes}. Hours: {hours}. "
                     f"Menu collected {r['menu_collected']}. {link}")
        for note in c["notes"]:
            lines.append(f"  - {note}")
    if found.get("relaxed"):
        lines.append(found["relaxed"])
    lines += ["", checked["source_note"], checked["allergy_note"]]
    return "\n".join(lines)
```

## Needs from teammates
- **Abhishek (D):** the current `build_reply` uses keys `dish`, `price`, `hours`, `link` that B's real output doesn't have (see the snippet above). Please also replace the line "No dish is guaranteed safe." with `allergy_note`: the ethics rules say never to call a dish safe, even in the negative. For the report link, reports go in `reports.csv` (`restaurant_id, reported_on, text`) and the Auditor picks them up
- **Part A:** check Dakshin (above). Three Lives and Margaritas show "closed every day" on Toast, which is likely paused online ordering; please verify their pickup links in `action_links.csv`. The audit run: `python audit.py`, then log each check with `python audit.py --record <id> "<finding>" "<action>" --by <name>`
- **Part E:** add `GOOGLE_PLACES_API_KEY` to the Streamlit secrets. `requirements.txt` still lacks B's packages (chromadb, openai). Note: `.gitignore` was missing a newline (`.streamlit/secrets.toml.DS_Store`), so `secrets.toml` was **not** ignored; fixed on this branch. Anyone who committed a key before this fix should rotate it
- **Everyone:** Google key setup: in Google Cloud, enable **Places API (New)**, create a key restricted to that API, and set it as `GOOGLE_PLACES_API_KEY`. The dine-in, takeout, delivery and reservable fields put Place Details in a higher billing tier with a smaller free monthly allowance, so the 30-minute cache matters
