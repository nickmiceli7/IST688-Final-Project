# Grubbz AI: handoff for coding agents

Read this before changing anything. It covers what the project is, who owns what, what's done, the rules the project is graded on, and known traps. It reflects **Nov 22, 2026**. Check `git log` and `gh pr list` for anything newer, and **update the "Status" section when you finish work**.

## The project
IST 688 final project (5-person team). A Streamlit app that recommends **downtown Syracuse restaurants** for a request ("Six of us, Friday at 7, Thai, ~$20 each, one vegetarian") and gives **one action link** each: reserve, order pickup, or delivery. Scope: **25 restaurants** from the Downtown Committee of Syracuse dining guide.

Request flow: **Host** (Part D, `app.py`) → **Scout** (Part B, `find_restaurants`) → **Checker** (Part C, `check_candidates`) → Host shows up to 3 picks. A monthly **Auditor** (Part C, `audit.py`) lists items for a person to verify.

Dates: milestone update **Nov 22, 2026**; completion **Dec 6, 2026**. The approved plan is "v3.2 Approach and Milestones" (Nov 15). Follow it; don't add features it doesn't ask for without asking the user.

## Who owns what
Each part's owner decides changes to its files. To change another part's file, use a branch and PR, describe it there, and leave the owner's `contributions/<name>.md` to them.

| Part | Owner | Files |
|---|---|---|
| A: restaurants and audit run | Group member 1 | `Grubbz AI restaurant list for B - 25 restaurantsin.csv` |
| B: Scout (menu search) | Dominick | `find_restaurants.py`, `build_index.py`, `extract_dishes.py`, `scrape_menus.py`, `cuisines.py`, `cuisines.json`, `loader.py`, `restaurant_ids.csv`, `dishes/`, `chroma_db/` |
| C: Checker and Auditor | Ashish | `check_candidates.py`, `places.py`, `resolve_place_ids.py`, `timecheck.py`, `matching.py`, `action_links.py`, `action_links.csv`, `statements.py`, `inspections.py`, `inspection_ids.csv`, `audit.py`, `checker_tool.py`, `tests/` |
| D: Host (conversation, memory) | Abhishek | `app.py` |
| E: deploy, test, evaluate | Group member 5 | deployment, `requirements.txt`, test requests, rubric |

Status notes: `contributions/abhishek.md`, `contributions/dominick.md`, `contributions/ashish.md`. Each has the format Status / Completed / Not done yet / Changes from the plan / Interface / Needs from teammates.

## Rules the project is graded on (ethics rubric): don't break these
- **Google Places terms: store only the place ID.** Hours, status, services and phone are fetched live and cached in memory. Never write them to disk, the repo or a database.
- **Every recommendation names Google Places as the source and states both limits**: hours can be out of date, and they don't show paused online ordering. Use `statements.SOURCE_NOTE` / `check_candidates(...)["source_note"]` verbatim.
- **Inspections:** show the date, the violations and a link to the official record. **Never compute or show a score, grade or rating.**
- **Allergies:** report only what the menu prints, and tell the user to confirm with the restaurant. **Never describe a dish as "safe", "allergen-free", "guaranteed" or "no risk"**, not even in the negative ("no dish is guaranteed safe"). Use `allergy_note` verbatim. `tests/test_checker.py` scans Part C's strings for these words.
- **One action link per recommendation.** For pickup, the restaurant's own ordering page comes first.
- **User profiles** are stored only with consent, and the user can view and delete them.
- **Not used:** Yelp (paid API), Uber Eats (partner-only API).
- **Removing a restaurant always records a plain-English reason** a user could read. Unknown data (Google says nothing) never removes a restaurant. If Google fails, keep the restaurant with `verified: False`.

## Working conventions
- **Key everything on `restaurant_id`** (`restaurant_ids.csv`, permanent numbers). The first 10 with dishes are IDs **1–9 and 22**.
- **Read A's CSV only through `loader.load_restaurants(csv_path)`.** The file is cp1252, not UTF-8. Note: `load_restaurants` rewrites `restaurant_ids.csv` on every call, with the same content unless names change.
- Keep network calls in small functions that tests can fake, and keep the logic pure (B and C both follow this).
- Python 3.11+ (`tomllib`). Run everything from the repo root, since paths are relative.
- Tests: `python -m unittest discover -s tests`. 43 tests, no network, no keys needed. Run them before every commit that touches Part C.
- Commit in small steps. Don't push or merge unless the user asks.

## Keys and secrets
- Keys: `OPENAI_API_KEY` (Scout), `GOOGLE_PLACES_API_KEY` (Checker), later Supabase. Read them from environment variables or `.streamlit/secrets.toml`. On Streamlit Cloud, use the app's Secrets settings.
- `.streamlit/secrets.toml`, `.env` and `.env.*` are gitignored. **Never commit a key** and never print one. Before committing, check `git status` for stray secrets.

## Status (Nov 22, 2026)
**Part B (Scout):** working on real data for the 10. It has 820 dishes in Chroma and a reranker. `find_restaurants(request_text, max_price=None)` returns `{"restaurants": [...up to 5...], "reranked": bool, "relaxed": str|None}`. To do by Dec 6: menus for the other 15 (several are PDFs, images or script-loaded pages), and check the reranker on dietary requests.

**Part C (Checker and Auditor):** merged to `main` (PRs #1, #2).
- `check_candidates(candidates, request)` accepts B's dict or list plus the Host's request dict (`party, when, mode, budget, cuisine, diet`). It returns `restaurants` (B's records unchanged plus a `checks` dict), `removed` (with reasons), `checks_run`, `checked_at`, `requested_time`, `when_note`, `source_note`, `allergy_note`, `inspection_note`.
- **Google Places is plug-and-play and not yet run against Google: no key is set yet.** Until a key is set, every restaurant is kept with `verified: False` and a phone link, and nothing is removed. Once `GOOGLE_PLACES_API_KEY` is set, it works with no code change: it matches each place live by A's name and address and uses the match only when both clearly agree. Reviewed IDs in `place_ids.csv` (from `python resolve_place_ids.py`) take priority. `python places.py --first10` is the milestone demo table.
- Inspections are live from the Onondaga County dataset `tbxv-5tbd` on health.data.ny.gov. 24 of 25 are matched in `inspection_ids.csv`.
- `action_links.csv`: 10 ordering pages are prefilled from A's menu URLs and **not yet verified**. Reservation and delivery links are blank, for a person to fill in.
- The Auditor is ready: `python audit.py`, then `python audit.py --record <id> "<finding>" "<action>" --by <name>`. The plan assigns the audit run to Part A; it needs the Google key for the business-status checks.

**Part D (Host):** `app.py` has the intake form, chat refinement, short-term memory and local-file profiles. **PR #3 (`wire-host-tools`) connects the real Scout and Checker and is open.** Until it merges, `main`'s `app.py` still returns sample results and has a `build_reply` that expects keys B doesn't return (`dish`, `price`, `hours`, `link`). To do by Dec 6: Supabase profiles, an LLM chat in place of keyword rules, a follow-up on the last choice, and a report link. Reports go in `reports.csv` (`restaurant_id, reported_on, text`), which the Auditor reads.

**Part E:** to do: deployment with a password, 10 test requests, rubric scores, appearance counts per restaurant. `requirements.txt` still lacks `chromadb` (pin the local version) and `openai`. Dominick notes Streamlit Cloud may need `pysqlite3-binary` for Chroma.

## Known issues and caveats
- **Dakshin (ID 17) may have closed.** It has no county inspection record, and a different restaurant, Mr. Pho, was inspected at its address (216 Walton) on Nov 3. Needs a person to check.
- **Three Lives (5) and Margaritas (6)** show "closed every day" on their Toast pages, which most likely means online ordering is paused, not that the restaurant is closed. Don't remove a restaurant based on a Toast page. Use Google status and hours, and verify their pickup links.
- Big Fella's (10) was replaced by The Evergreen (22) in the first 10 because its menu needs a browser.

## Traps that have already bitten someone
- **Opening Chroma rewrites `chroma_db/chroma.sqlite3`**, even for a search. After running the app or `find_restaurants` locally, run `git restore chroma_db/chroma.sqlite3` before committing, unless you meant to rebuild the index.
- **`menus/` is not in the repo.** `build_index.py` reads `menus/<id>.txt` for collection dates, so rebuilding the index needs those files from Part B.
- **B's `get_api_key()` raises `SystemExit` when the OpenAI key is missing.** `except Exception` doesn't catch it; use `except (Exception, SystemExit)` where the app must keep running.
- **Streamlit Markdown:** `*` and `_` in menu text become emphasis, and `$...$` becomes math. Escape menu text before display (see `_md` in PR #3).
- **On Windows PowerShell,** a `git commit -m` message containing double quotes gets split into file paths. Write the message to a file and use `git commit -F <file>`.
- The county violation text sometimes has a `;` inside a single violation. Split only on `; Item` (see `inspections.split_violations`).
