Dominick Miceli: Part B (Scout: Menu Search)

Status as of Nov 22, 2026: working end to end on real data for 10 restaurants. Code: find_restaurants.py, build_index.py, extract_dishes.py, scrape_menus.py, loader.py, cuisines.py (all pushed to GitHub).

Completed

Restaurant loader with permanent numeric IDs (restaurant_ids.csv), so IDs don’t shift when the audit removes a restaurant
Menu collection for 10 restaurants: 7 scraped automatically, 3 pasted by hand because Toast blocks automated access
Dish extraction: an LLM reads each menu and returns dishes with name, description, section, price, and any dietary wording the menu itself prints. Drinks are skipped. Code checks every dish against the source text, so a dish or price not found in the menu is rejected or marked “unknown”
820 dishes embedded and stored in Chroma, each with dish_id and restaurant_id, cuisine, price, price known or unknown, and the menu’s collection date
One cuisine label per restaurant, assigned by an LLM from a fixed list
find_restaurants: embeds the request, retrieves candidate dishes, reranks the top 20 with an LLM, groups by restaurant, and returns up to 5. A price limit is supported, and dishes with no listed price are returned and marked “price unknown”

Not done yet (planned by Dec 6)

Menus for the other 15 restaurants. Several are PDFs, images, or pages that need a browser, so they need a different collection method
Check how the reranker orders results for dietary requests. In the first test, an unmarked dish ranked above one the menu marks vegetarian

Changes from the Nov 15 plan

Cuisine is not a hard filter. Cuisine is stored per restaurant, but the best Thai dishes in the data are at Sakana-Ya, which is labeled Japanese. Search finds dishes by meaning, and the cuisine stays in the record
Unknown prices are shown as “price unknown” and still pass a price limit
The reranker is an LLM scoring call, not a separate reranking model
No more than 6 dishes per restaurant in the 20 sent to the reranker, so one restaurant can’t fill every slot
Built with Chroma and the OpenAI API directly, not LangChain. Part D can wrap find_restaurants as a LangChain tool
The Evergreen replaces Big Fella’s in the first 10, because Big Fella’s menu needs a browser

Interface for Part D

find_restaurants(request_text, max_price=None) returns {"restaurants": [...], "reranked": bool, "relaxed": str or None}
Each restaurant has: restaurant_id, name, cuisine, phone, website, address, menu_collected, best_score, and up to 3 dishes
Each dish has: name, section, price (a dollar amount or “price unknown”), price_known, menu_says (dietary wording from the menu, often empty), and score
Hours and live checks come from Part C, not Part B. If relaxed is set, the Host should tell the user the price limit was removed

Needs from teammates

Part A: send menu text for the remaining 15 if available
Part C: use the IDs in restaurant_ids.csv in check_candidates
Part D: confirm the return format above works for the Host
Part E: for deployment, pin the same chromadb version as local, add pysqlite3-binary, store the key as OPENAI_API_KEY in secrets, and note chroma_db/ is in the repo