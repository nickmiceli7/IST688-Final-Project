"""The Scout: find_restaurants(request_text, max_price=None).

Pipeline:  embed the request -> fetch candidate dishes from Chroma -> rerank the top 20
           with an LLM -> group by restaurant -> return up to 5 restaurants.

Usage (test from the terminal):
    python find_restaurants.py "spicy noodles for a group" --max-price 20
"""
import argparse
import json
from functools import lru_cache

from build_index import CHROMA_PATH, COLLECTION, embed, openai_client
from extract_dishes import MODEL
from loader import load_restaurants

CSV_PATH = "Grubbz AI restaurant list for B - 25 restaurantsin.csv"
POOL = 100               # dishes fetched from Chroma before choosing the 20
TOP_K = 20               # dishes that go to the reranker (the plan's "20 retrieved dishes")
CAP_PER_RESTAURANT = 6   # so one restaurant with many similar dishes can't fill all 20 slots
MAX_RESTAURANTS = 5
MIN_RESTAURANTS = 3      # fewer than this triggers the relax-and-retry step
MIN_SCORE = 4            # reranker score (0-10) below this is not a real match
DISHES_PER_RESTAURANT = 3

RERANK_PROMPT = """You rank menu dishes for a restaurant recommendation tool. You receive a \
customer's request and numbered candidate dishes. Score each dish from 0 to 10 for how well it \
fits the WHOLE request: the kind of food, the price, the party, and any dietary needs.

Rules:
- Judge only from the information given. Do not invent details about a dish.
- Never assume a dish is vegetarian, vegan, gluten-free or free of any allergen unless its \
"menu says" field says so. If the request has a dietary need and the menu says nothing about it \
for that dish, the score can be 5 at most.
- A dish with an unknown price can still score well on the other criteria.
- Return one score for every candidate."""

RERANK_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "score": {"type": "number"}},
        "required": ["id", "score"], "additionalProperties": False}}},
    "required": ["scores"], "additionalProperties": False,
}


# ---------- pure helpers ----------
def price_text(meta):
    return f"${meta['price']:.2f}" if meta["price_known"] else "price unknown"


def build_where(max_price):
    """Unknown-price dishes always pass: they are shown as 'price unknown', not hidden."""
    if max_price is None:
        return None
    return {"$or": [{"price_known": False}, {"price": {"$lte": float(max_price)}}]}


def pick_candidates(result, top_k=TOP_K, cap=CAP_PER_RESTAURANT):
    """Walk Chroma's results in similarity order, keeping at most `cap` dishes per restaurant."""
    per_restaurant, picked = {}, []
    for dish_id, doc, meta, dist in zip(result["ids"][0], result["documents"][0],
                                        result["metadatas"][0], result["distances"][0]):
        rid = meta["restaurant_id"]
        if per_restaurant.get(rid, 0) >= cap:
            continue
        per_restaurant[rid] = per_restaurant.get(rid, 0) + 1
        picked.append({"dish_id": dish_id, "meta": meta, "distance": dist})
        if len(picked) == top_k:
            break
    return picked


def candidate_line(i, c):
    m = c["meta"]
    return (f"{i}: {m['name']} | restaurant: {m['restaurant_name']} ({m['cuisine']}) | "
            f"section: {m['section'] or 'n/a'} | price: {price_text(m)} | "
            f"menu says: {m['dietary_text'] or 'nothing'}")


def group_by_restaurant(candidates, scores, info, max_restaurants=MAX_RESTAURANTS):
    """candidates + scores -> up to 5 restaurants, ranked by their best dish."""
    groups = {}
    for c, score in zip(candidates, scores):
        if score < MIN_SCORE:
            continue
        groups.setdefault(c["meta"]["restaurant_id"], []).append((score, c))
    restaurants = []
    for rid, items in groups.items():
        items.sort(key=lambda sc: (-sc[0], sc[1]["distance"]))
        meta0 = items[0][1]["meta"]
        extra = info(rid)
        restaurants.append({
            "restaurant_id": rid, "name": meta0["restaurant_name"], "cuisine": meta0["cuisine"],
            "phone": extra.get("phone", ""), "website": extra.get("website", ""),
            "address": extra.get("address", ""), "menu_collected": meta0["menu_collected"],
            "best_score": items[0][0],
            "dishes": [{"name": c["meta"]["name"], "section": c["meta"]["section"],
                        "price": price_text(c["meta"]), "price_known": c["meta"]["price_known"],
                        "menu_says": c["meta"]["dietary_text"], "score": s,
                        "dish_id": c["dish_id"]}
                       for s, c in items[:DISHES_PER_RESTAURANT]],
            "_min_distance": min(c["distance"] for _, c in items)})
    restaurants.sort(key=lambda r: (-r["best_score"], r["_min_distance"]))
    for r in restaurants:
        del r["_min_distance"]
    return restaurants[:max_restaurants]


# ---------- data access (replaced by fakes in tests) ----------
@lru_cache(maxsize=1)
def get_collection():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH).get_collection(COLLECTION)


@lru_cache(maxsize=1)
def _restaurant_table():
    df = load_restaurants(CSV_PATH)
    return {int(r["restaurant_id"]): r.to_dict() for _, r in df.iterrows()}


def restaurant_info(restaurant_id):
    return _restaurant_table().get(int(restaurant_id), {})


def retrieve(request_text, max_price):
    kwargs = {"where": build_where(max_price)} if max_price is not None else {}
    result = get_collection().query(query_embeddings=embed([request_text]),
                                    n_results=POOL, **kwargs)
    return pick_candidates(result)


def rerank(request_text, candidates):
    """Return (scores in candidate order, reranked_ok). Falls back to embedding order on failure."""
    fallback = [round(10 * max(0.0, 1 - c["distance"]), 1) for c in candidates]
    if not candidates:
        return [], True
    try:
        kwargs = {"reasoning_effort": "low"} if MODEL.startswith(("gpt-5", "o")) else {}
        lines = "\n".join(candidate_line(i, c) for i, c in enumerate(candidates))
        response = openai_client().chat.completions.create(
            model=MODEL, **kwargs,
            messages=[{"role": "system", "content": RERANK_PROMPT},
                      {"role": "user", "content": f"Request: {request_text}\n\nCandidates:\n{lines}"}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "scores", "strict": True,
                                             "schema": RERANK_SCHEMA}})
        by_id = {s["id"]: s["score"] for s in json.loads(response.choices[0].message.content)["scores"]}
        if set(by_id) != set(range(len(candidates))):
            return fallback, False
        return [float(by_id[i]) for i in range(len(candidates))], True
    except Exception:
        return fallback, False


# ---------- the tool ----------
def find_restaurants(request_text, max_price=None):
    """Recommend downtown Syracuse restaurants for a request.

    request_text: the customer's full request (food, party, diet, budget in words).
    max_price: optional per-person price limit in dollars; dishes with no listed price are
               still returned and marked "price unknown".
    Returns {"restaurants": [...up to 5...], "reranked": bool, "relaxed": str or None}.
    """
    candidates = retrieve(request_text, max_price)
    scores, reranked = rerank(request_text, candidates)
    restaurants = group_by_restaurant(candidates, scores, restaurant_info)
    relaxed = None

    if len(restaurants) < MIN_RESTAURANTS and max_price is not None:
        candidates = retrieve(request_text, None)
        scores, reranked = rerank(request_text, candidates)
        restaurants = group_by_restaurant(candidates, scores, restaurant_info)
        relaxed = f"Fewer than {MIN_RESTAURANTS} restaurants matched under ${max_price:g}, so the price limit was removed."
        for r in restaurants:
            for d in r["dishes"]:
                d["over_budget"] = bool(d["price_known"] and float(d["price"].lstrip("$")) > max_price)

    return {"restaurants": restaurants, "reranked": reranked, "relaxed": relaxed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("--max-price", type=float)
    args = parser.parse_args()
    out = find_restaurants(args.request, args.max_price)
    print(f"reranked: {out['reranked']}   relaxed: {out['relaxed']}")
    for r in out["restaurants"]:
        print(f"\n{r['name']} ({r['cuisine']})  score {r['best_score']}  menu collected {r['menu_collected']}")
        for d in r["dishes"]:
            print(f"   {d['score']:>4}  {d['name']}  {d['price']}" + (f"  [{d['menu_says']}]" if d["menu_says"] else ""))
