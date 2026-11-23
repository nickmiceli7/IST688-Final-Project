# Ethan Vroom: Part A (Restaurants and Audit)

**Status as of Nov 22, 2026:** two things of mine are in use. The team is building from the v3.2 plan I drafted, and Parts B and C run on my restaurant list. Both were made with an AI assistant (Claude) at my direction. I have not verified any restaurant myself yet, and the audit has not been run. Data: `Grubbz AI restaurant list for B - 25 restaurantsin.csv`.

## Completed
- The plan (Nov 15): drafted “v3.2 Approach and Milestones” with the assistant, from my own notes, over five drafts. `AGENTS.md` names it as the approved plan
- Ideas from my notes that are now in the repo:
  - The Downtown Committee of Syracuse dining guide as the source of the list: the project’s scope, and the Auditor’s check of the list against the guide
  - Dine in, pickup or delivery, with the restaurant’s own ordering page first for pickup: `action_links.py`
  - Everything depends on hours, read from a Google API: `places.py` and `timecheck.py`
  - A restaurant can pause online ordering without the app seeing it: the source statement in `statements.py` says so
  - Health inspection data: `inspections.py`
  - Tonight or a later date: the time reading in `timecheck.py`
  - Saved preferences: the profiles in `app.py`
  - A person finds and verifies the restaurants, and the team audits monthly with a pipeline to help: Part A, `audit.py` and the “last verified” date
- The restaurant list: had the assistant compile the 25 from the Downtown Committee guide and the restaurants’ pages, with name, address, phone, website, menu page link and menu format
- Made the swaps: added The Sweet Praxis and Water Street Bagel Co., moved Big Fella’s into the first 10, and dropped appeThaizing and Mr. Pho
- Sent the list to B, who collected the first 10 menus from its links
- The list’s `menu_format` column sorts the 15 menus still to collect: 1 text, 4 that need a browser, 5 PDFs, 1 set of images, 3 not opened and 1 not confirmed
- Two flags from the list’s notes are now known issues in `AGENTS.md`: the Toast pages that list every day as closed (5 and 6), and Big Fella’s needing a browser. The Evergreen (22), marked in the list as a ready spare, took its place

## Not done yet (planned by Dec 6)
- Verify each restaurant myself, on its own site or by phone. Nothing has been checked by a person: `open` reads “to confirm” and `date_checked` is empty for all 25
- The audit run and its log: `python audit.py`, then `--record` for each check. Those records give each result its “last verified” date
- Check Dakshin (17), and the pickup links for Three Lives (5) and Margaritas (6), as C asked
- Menu text for the other 15, as B asked

## Changes from the Nov 15 plan
- The plan has A collecting 10 menus for B. B collected them from the list’s menu links instead
- The list was compiled by an AI assistant from web pages, so its notes and item counts are approximate. B’s `dishes/` files are the better record for the first 10
- The Evergreen (22) replaced Big Fella’s (10) in the first 10
- Three of the first 10 menus come from third-party pages: Byblos and Sakana-Ya from MenuPages (Grubhub), Funk ’n Waffles from Allmenus. The team has not decided whether those count

## Interface for Parts B and C
The list is read through `loader.load_restaurants`. What each column feeds:
- `menu_format`: which menus B’s scraper fetches
- `menu_url`: C’s prefilled ordering links, and the page the Auditor checks for changes
- `name` and `address`: the match to each county inspection record and Google place
- `phone` and `website`: the results and the action links
- `notes`: its item counts are B’s “A says” check on each extraction
- `open` and `date_checked`: not read by the code yet. They fill in as I verify
- Names key the permanent IDs in `restaurant_ids.csv`, so a renamed restaurant gets a new ID

## Needs from teammates
- Dominick (B): how you want the menu text for the other 15. The plan says one text file per menu, and `scrape_menus.py` writes `menus/<id>.txt`, a folder that is not in the repo
- Ashish (C): say when the Google key is set, since the audit’s status checks need it. And please put my name in the `AGENTS.md` owner table, where Part A reads “Group member 1”
- Everyone: decide whether Grubhub and Allmenus pages count as menu sources, and whether to keep The Sweet Praxis (24)
