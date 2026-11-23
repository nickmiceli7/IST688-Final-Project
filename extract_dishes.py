"""Turn each scraped menu (menus/<id>.txt) into structured dishes (dishes/<id>.json).

Usage:
    python extract_dishes.py                # every restaurant that has a menus/<id>.txt
    python extract_dishes.py --only 1,2     # just these restaurant_ids
    python extract_dishes.py --force        # redo ones that already have a dishes/<id>.json

API key: set OPENAI_API_KEY in your environment, or keep it in .streamlit/secrets.toml
(any key whose name contains "openai"). The key is never printed.

Safety checks (the LLM reads the menu, but code verifies it):
  * a dish whose name does not appear in the source text is REJECTED (possible invention)
  * a price that does not appear in the source text becomes "unknown"
  * dietary_text that does not appear in the source text is blanked
"""
import argparse
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

from loader import load_restaurants

CSV_PATH = "Grubbz AI restaurant list for B - 25 restaurantsin.csv"
MENU_DIR = Path("menus")
OUT_DIR = Path("dishes")
MODEL = "gpt-5-mini"
CHUNK_CHARS = 12000      # long menus are sent in pieces so the model doesn't drop dishes

SYSTEM_PROMPT = """You extract food dishes from raw text copied from a restaurant's menu web page. \
The text also contains unrelated material (navigation, hours, events, reviews, footers).

Return every food dish on the menu. Rules:
- Copy names, descriptions, section headings and dietary markers EXACTLY as written. \
Do not paraphrase, correct, translate, or invent anything.
- Skip drinks of every kind (cocktails, beer, wine, coffee, soda, juice) and anything that is \
not a dish. Skip add-on lines such as "add chicken +$4" unless they are a dish on their own.
- price: the number only, with no currency symbol (for example 14.5). If a dish lists several \
sizes or prices, use the lowest. If no price is printed for that dish, use null. Never guess a price.
- section: the menu heading the dish appears under, as written, or "" if there is none.
- dietary_text: only markers or words the menu itself prints for that dish (such as "(V)", \
"vegetarian", "gluten free"). Never infer them from the ingredients. Use "" if none.
- description: the printed description, or "" if none.
- If the text contains no dishes, return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {"dishes": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "section": {"type": "string"},
            "price": {"type": ["number", "null"]},
            "dietary_text": {"type": "string"},
        },
        "required": ["name", "description", "section", "price", "dietary_text"],
        "additionalProperties": False}}},
    "required": ["dishes"],
    "additionalProperties": False,
}

_client = None
usage_totals = {"prompt": 0, "completion": 0}


# ---------- API ----------
def get_api_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    secrets = Path(".streamlit/secrets.toml")
    if secrets.exists():
        data = tomllib.loads(secrets.read_text(encoding="utf-8"))
        for key, value in data.items():
            if "openai" in key.lower() and isinstance(value, str):
                return value
    raise SystemExit("No OpenAI key found. Set OPENAI_API_KEY or add it to .streamlit/secrets.toml")


def call_llm(chunk):
    """Send one chunk of menu text to the model. Returns a list of dish dicts."""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=get_api_key())
    kwargs = {}
    if MODEL.startswith(("gpt-5", "o")):
        kwargs["reasoning_effort"] = "low"
    response = _client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": chunk}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "dishes", "strict": True, "schema": SCHEMA}},
        **kwargs)
    usage_totals["prompt"] += response.usage.prompt_tokens
    usage_totals["completion"] += response.usage.completion_tokens
    return json.loads(response.choices[0].message.content)["dishes"]


# ---------- helpers ----------
def chunk_text(text, max_chars=CHUNK_CHARS):
    """Split on line boundaries into pieces of at most max_chars."""
    chunks, current, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) + 1 > max_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def norm(s):
    """Lowercase, straighten quotes, collapse whitespace, so string checks don't trip on formatting."""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def price_in_text(price, source_norm):
    forms = {f"{price:.2f}", f"{price:g}"}
    return any(f in source_norm for f in forms)


def build_records(raw_dishes, source_text, restaurant_id):
    """Verify each dish against the source and build final records. Returns (kept, rejected)."""
    source_norm = norm(source_text)
    kept, rejected, seen = [], [], set()
    for d in raw_dishes:
        name = d["name"].strip()
        if not name or norm(name) not in source_norm:
            rejected.append(d)                      # not found in the menu text: don't trust it
            continue
        key = (norm(name), d["price"])
        if key in seen:                             # same dish returned twice (chunk overlap etc.)
            continue
        seen.add(key)

        price = d["price"]
        if price is not None and not price_in_text(price, source_norm):
            price = None                            # price not printed anywhere: treat as unknown
        diet = d["dietary_text"].strip()
        if diet and norm(diet) not in source_norm:
            diet = ""

        kept.append({
            "dish_id": f"{restaurant_id}-{len(kept) + 1}",
            "restaurant_id": int(restaurant_id),
            "name": name,
            "description": d["description"].strip(),
            "section": d["section"].strip(),
            "price": float(price) if price is not None else 0.0,
            "price_known": price is not None,
            "dietary_text": diet,
        })
    return kept, rejected


def expected_count(notes):
    m = re.search(r"(\d+)\s+(?:priced\s+)?(?:food\s+)?(?:items|entries)", notes)
    return int(m.group(1)) if m else None


def extract_restaurant(text, restaurant_id):
    raw = []
    for chunk in chunk_text(text):
        raw.extend(call_llm(chunk))
    return build_records(raw, text, restaurant_id)


# ---------- main ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    restaurants = load_restaurants(CSV_PATH)
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        restaurants = restaurants[restaurants["restaurant_id"].isin(wanted)]

    OUT_DIR.mkdir(exist_ok=True)
    print(f"{'id':>3}  {'restaurant':<28} {'kept':>5} {'A says':>7} {'rejected':>9} {'no price':>9}")

    for _, row in restaurants.iterrows():
        rid, name = row["restaurant_id"], row["name"]
        menu_file, out_file = MENU_DIR / f"{rid}.txt", OUT_DIR / f"{rid}.json"
        if not menu_file.exists() or (out_file.exists() and not args.force):
            continue

        kept, rejected = extract_restaurant(menu_file.read_text(encoding="utf-8"), rid)
        out_file.write_text(json.dumps({
            "restaurant_id": int(rid), "restaurant_name": name, "model": MODEL,
            "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "dishes": kept, "rejected": rejected}, indent=2, ensure_ascii=False),
            encoding="utf-8")

        no_price = sum(1 for d in kept if not d["price_known"])
        exp = expected_count(row["notes"])
        print(f"{rid:>3}  {name:<28} {len(kept):>5} {exp if exp else '-':>7} "
              f"{len(rejected):>9} {no_price:>9}")

    print(f"\nTokens used: {usage_totals['prompt']:,} in, {usage_totals['completion']:,} out")
    print("Review: any restaurant whose 'kept' is far below 'A says', and every 'rejected' list "
          "(open the .json and read the rejected dishes).")


if __name__ == "__main__":
    main()
