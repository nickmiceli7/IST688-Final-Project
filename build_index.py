"""Build the Scout's search index: classify cuisines, embed every dish, store in Chroma.

Usage:
    python build_index.py                 # classify cuisines (cached), then build the index
    python build_index.py --rebuild       # delete and rebuild the Chroma collection
    python build_index.py --test "pad thai noodles"   # run one test search after building

Reads:   dishes/<id>.json (from extract_dishes.py), menus/<id>.txt (for the collection date)
Writes:  cuisines.json (one label per restaurant, with evidence, for you to review)
         chroma_db/ (the vector database, persisted on disk)
"""
import argparse
import json
from datetime import date
from pathlib import Path

from cuisines import SYSTEM_PROMPT, parse_classification
from extract_dishes import MODEL, get_api_key

DISH_DIR = Path("dishes")
MENU_DIR = Path("menus")
CUISINE_FILE = Path("cuisines.json")
CHROMA_PATH = "chroma_db"
COLLECTION = "grubbz_dishes"
EMBED_MODEL = "text-embedding-3-small"
EMBED_BATCH = 100

_client = None


def openai_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=get_api_key())
    return _client


# ---------- pure functions (no network) ----------
def dish_document(d, cuisine):
    """The text that gets embedded. Meaning goes here; exact-match fields go in metadata."""
    parts = [f"{d['name']}.", d["description"], f"Cuisine: {cuisine}.",
             f"Menu section: {d['section']}." if d["section"] else "",
             f"Menu says: {d['dietary_text']}." if d["dietary_text"] else ""]
    return " ".join(p for p in parts if p)


def dish_metadata(d, restaurant_name, cuisine, collected):
    """Chroma metadata may only hold str / int / float / bool: never None."""
    return {"restaurant_id": int(d["restaurant_id"]), "restaurant_name": restaurant_name,
            "cuisine": cuisine, "name": d["name"], "section": d["section"],
            "price": float(d["price"]), "price_known": bool(d["price_known"]),
            "dietary_text": d["dietary_text"], "menu_collected": collected}


def classifier_message(restaurant_name, dishes, max_dishes=60):
    step = max(1, len(dishes) // max_dishes)          # evenly spaced sample of the menu
    lines = [f"- {d['name']}" + (f" [{d['section']}]" if d["section"] else "")
             for d in dishes[::step][:max_dishes]]
    return f"Restaurant: {restaurant_name}\n\nDishes:\n" + "\n".join(lines)


def collection_date(restaurant_id):
    """When the menu text was collected = when menus/<id>.txt was last written."""
    path = MENU_DIR / f"{restaurant_id}.txt"
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


# ---------- network steps ----------
def call_classifier(message):
    kwargs = {"reasoning_effort": "low"} if MODEL.startswith(("gpt-5", "o")) else {}
    response = openai_client().chat.completions.create(
        model=MODEL, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": message}], **kwargs)
    return response.choices[0].message.content


def classify_all(restaurants):
    """restaurants: list of (id, name, dishes). Returns {id: {cuisine, evidence}}; cached on disk."""
    cache = json.loads(CUISINE_FILE.read_text()) if CUISINE_FILE.exists() else {}
    for rid, name, dishes in restaurants:
        if str(rid) in cache or not dishes:
            continue
        cuisine, evidence = parse_classification(call_classifier(classifier_message(name, dishes)))
        cache[str(rid)] = {"name": name, "cuisine": cuisine, "evidence": evidence}
        CUISINE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    return cache


def embed(texts):
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        response = openai_client().embeddings.create(model=EMBED_MODEL, input=texts[i:i + EMBED_BATCH])
        vectors.extend(item.embedding for item in response.data)
    return vectors


def build_collection(records, rebuild=False):
    """records: list of (dish_id, document, metadata)."""
    import chromadb
    db = chromadb.PersistentClient(path=CHROMA_PATH)
    if rebuild:
        try:
            db.delete_collection(COLLECTION)
        except Exception:
            pass
    collection = db.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    if collection.count() == len(records):
        print(f"Collection already has {collection.count()} dishes; nothing to do (use --rebuild).")
        return collection
    if collection.count():
        print("Collection size differs from the dishes on disk; rebuilding it.")
        db.delete_collection(COLLECTION)
        collection = db.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    ids, docs, metas = zip(*records)
    collection.add(ids=list(ids), documents=list(docs), metadatas=list(metas),
                   embeddings=embed(list(docs)))
    print(f"Stored {collection.count()} dishes.")
    return collection


def test_search(collection, query, n=5):
    result = collection.query(query_embeddings=embed([query]), n_results=n)
    print(f"\nTop {n} for: {query!r}")
    for meta, dist in zip(result["metadatas"][0], result["distances"][0]):
        price = f"${meta['price']:.2f}" if meta["price_known"] else "price unknown"
        print(f"  {dist:.3f}  {meta['name']}  |  {meta['restaurant_name']} ({meta['cuisine']})  {price}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--test")
    args = parser.parse_args()

    loaded = []
    for path in sorted(DISH_DIR.glob("*.json"), key=lambda p: int(p.stem)):
        data = json.loads(path.read_text(encoding="utf-8"))
        loaded.append((data["restaurant_id"], data["restaurant_name"], data["dishes"]))
    print(f"{len(loaded)} restaurants, {sum(len(d) for _, _, d in loaded)} dishes loaded.")

    cuisines = classify_all(loaded)
    print("\nCuisine labels (read these!):")
    for rid, name, dishes in loaded:
        c = cuisines.get(str(rid))
        if c:
            print(f"  {rid:>3}  {name:<28} {c['cuisine']:<32} {c['evidence']}")

    records = []
    for rid, name, dishes in loaded:
        cuisine = cuisines.get(str(rid), {}).get("cuisine", "Other")
        collected = collection_date(rid)
        for d in dishes:
            records.append((d["dish_id"], dish_document(d, cuisine),
                            dish_metadata(d, name, cuisine, collected)))

    collection = build_collection(records, rebuild=args.rebuild)
    if args.test:
        test_search(collection, args.test)


if __name__ == "__main__":
    main()
