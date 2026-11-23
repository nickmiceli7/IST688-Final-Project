"""Optional LangChain wrapper so the Host agent can call the Checker as a tool.

    from checker_tool import make_checker_tool
    tools = [make_checker_tool(), ...]

The tool takes restaurant IDs (from the Scout) plus mode and time, so the LLM only passes
small arguments. Needs langchain-core, which Part D adds when it wires up LangChain.
"""
from functools import lru_cache

from check_candidates import check_candidates

CSV_PATH = "Grubbz AI restaurant list for B - 25 restaurantsin.csv"


@lru_cache(maxsize=1)
def _restaurants():
    from loader import load_restaurants
    df = load_restaurants(CSV_PATH)
    return {int(r["restaurant_id"]): {"restaurant_id": int(r["restaurant_id"]), "name": r["name"],
                                      "phone": r["phone"], "website": r["website"], "address": r["address"]}
            for _, r in df.iterrows()}


def check_restaurants(restaurant_ids, mode="Dine in", when=""):
    """Run the Checker on restaurant IDs and return a compact result for an LLM."""
    table = _restaurants()
    candidates = [table[int(i)] for i in restaurant_ids if int(i) in table]
    out = check_candidates(candidates, {"mode": mode, "when": when})
    return {
        "kept": [{"restaurant_id": r["restaurant_id"], "name": r["name"],
                  "verified": r["checks"]["verified"],
                  "open_at_requested_time": r["checks"]["open_at_requested_time"],
                  "hours_text": r["checks"]["hours_text"],
                  "action_link": r["checks"]["action_link"],
                  "last_inspection_date": (r["checks"]["inspection"] or {}).get("date"),
                  "notes": r["checks"]["notes"]} for r in out["restaurants"]],
        "removed": out["removed"],
        "when_note": out["when_note"],
        "source_note": out["source_note"],
        "allergy_note": out["allergy_note"],
    }


def make_checker_tool():
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class CheckerArgs(BaseModel):
        restaurant_ids: list[int] = Field(description="restaurant_id values returned by find_restaurants")
        mode: str = Field(default="Dine in", description='"Dine in", "Pickup" or "Delivery"')
        when: str = Field(default="", description='Day and time as the user said it, e.g. "Friday 7 PM"; blank if none')

    return StructuredTool.from_function(
        func=check_restaurants, name="check_candidates", args_schema=CheckerArgs,
        description=("Check candidate restaurants live: Google Places business status, hours at the "
                     "requested time and the requested service, plus the last county inspection. "
                     "Returns the restaurants to keep (with one action link each), the ones removed "
                     "with a reason, and statements to show the user verbatim."))
