"""Grubbz AI - Part D (Host) blueprint.

Runs with: streamlit run app.py
Uses SAMPLE results and a local JSON file for profiles.
Later: replace find_restaurants / check_candidates with B's and C's real tools,
and replace load_profile / save_profile / delete_profile with Supabase.
"""
import json
import os
import hashlib
import streamlit as st

PROFILE_FILE = "profiles.json"

# ---------------------------------------------------------------
# Placeholder tools (replace with real ones from B and C)
# ---------------------------------------------------------------
def find_restaurants(request: dict) -> list:
    """TODO (Dominick, Part B): Scout. Returns up to 5 restaurants."""
    return [
        {"name": "Byblos Mediterranean Cafe", "dish": "Chicken shawarma plate",
         "price": 16, "hours": "11am-9pm", "data_date": "Nov 22",
         "link": "https://menupages.com/byblos-mediterranean-cafe/223-n-clinton-st-syracuse"},
        {"name": "Sakana-Ya Sushi Bar", "dish": "Veggie roll combo",
         "price": 18, "hours": "12pm-10pm", "data_date": "Nov 22",
         "link": "https://menupages.com/sakana-ya-sushi-bar/215-walton-st-syracuse"},
        {"name": "Inka's", "dish": "Lomo saltado",
         "price": 20, "hours": "11am-9pm", "data_date": "Nov 22",
         "link": "https://inkassyracuse.com/food-menu"},
    ]


def check_candidates(candidates: list, request: dict) -> list:
    """TODO (Ashish, Part C): Checker. Removes closed places."""
    return candidates


# ---------------------------------------------------------------
# Profile storage (local file now; Supabase later)
# ---------------------------------------------------------------
def _hash(passcode: str) -> str:
    return hashlib.sha256(passcode.encode()).hexdigest()


def _read_all() -> dict:
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            return json.load(f)
    return {}


def _write_all(data: dict) -> None:
    with open(PROFILE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def sign_in(name: str, passcode: str):
    """Returns (ok, message). Creates the user if new."""
    data = _read_all()
    key = name.strip().lower()
    if key in data:
        if data[key]["passcode"] != _hash(passcode):
            return False, "Wrong passcode."
        return True, "Welcome back."
    data[key] = {"passcode": _hash(passcode), "profile": {}}
    _write_all(data)
    return True, "New account created."


def load_profile(name: str) -> dict:
    return _read_all().get(name.strip().lower(), {}).get("profile", {})


def save_profile(name: str, profile: dict) -> None:
    data = _read_all()
    data[name.strip().lower()]["profile"] = profile
    _write_all(data)


def delete_profile(name: str) -> None:
    data = _read_all()
    data.pop(name.strip().lower(), None)
    _write_all(data)


# ---------------------------------------------------------------
# Short-term memory: recent turns + running summary of needs
# ---------------------------------------------------------------
def update_summary(summary: dict, new_info: dict) -> dict:
    merged = dict(summary)
    merged.update({k: v for k, v in new_info.items() if v not in (None, "", [])})
    return merged


def build_reply(request: dict) -> str:
    results = check_candidates(find_restaurants(request), request)[:3]
    if not results:
        return "I couldn't find a match. Try a different cuisine or budget."
    lines = ["Here are my top picks:"]
    for r in results:
        lines.append(
            f"- **{r['name']}**: {r['dish']} (${r['price']}), open {r['hours']}. "
            f"Menu collected {r['data_date']}. [Order or reserve]({r['link']})"
        )
    lines.append("\nPlease confirm allergies with the restaurant. "
                 "No dish is guaranteed safe.")
    return "\n".join(lines)


# ---------------------------------------------------------------
# App
# ---------------------------------------------------------------
st.set_page_config(page_title="Grubbz AI", page_icon="🍽️")
st.title("🍽️ Grubbz AI")
st.caption("Downtown Syracuse restaurant picks for your request.")

ss = st.session_state
ss.setdefault("user", None)
ss.setdefault("messages", [])          # short-term buffer
ss.setdefault("summary", {})           # running summary of needs

# ---- Sidebar: sign-in, consent, profile ----
with st.sidebar:
    st.header("Account")
    if ss.user is None:
        name = st.text_input("Name")
        passcode = st.text_input("Passcode", type="password")
        consent = st.checkbox("I agree to save my preferences (you can view "
                              "and delete them anytime).")
        if st.button("Sign in / Create"):
            if not name or not passcode:
                st.warning("Enter a name and passcode.")
            elif not consent:
                st.warning("Please tick the consent box to save a profile.")
            else:
                ok, msg = sign_in(name, passcode)
                if ok:
                    ss.user = name
                    ss.summary = load_profile(name)
                    st.rerun()
                else:
                    st.error(msg)
        st.info("Or just use the app without signing in; nothing is saved.")
    else:
        st.success(f"Signed in as {ss.user}")
        st.subheader("My saved profile")
        st.json(load_profile(ss.user) or {"info": "Nothing saved yet."})
        if st.button("Delete my profile"):
            delete_profile(ss.user)
            ss.user, ss.summary, ss.messages = None, {}, []
            st.rerun()
        if st.button("Sign out"):
            ss.user, ss.summary, ss.messages = None, {}, []
            st.rerun()

# ---- Intake form ----
with st.form("intake"):
    st.subheader("Tell me what you need")
    c1, c2 = st.columns(2)
    party = c1.number_input("Party size", 1, 30, int(ss.summary.get("party", 2)))
    when = c2.text_input("Day and time", ss.summary.get("when", ""),
                         placeholder="Friday 7 PM")
    mode = c1.selectbox("Mode", ["Dine in", "Pickup", "Delivery"])
    budget = c2.number_input("Budget per person ($)", 0, 200,
                             int(ss.summary.get("budget", 20)))
    cuisine = st.text_input("Cuisine", ss.summary.get("cuisine", ""),
                            placeholder="Thai or Vietnamese")
    diet = st.text_input("Diet or limits", ss.summary.get("diet", ""),
                         placeholder="1 vegetarian")
    go = st.form_submit_button("Find restaurants")

if go:
    info = {"party": party, "when": when, "mode": mode, "budget": budget,
            "cuisine": cuisine, "diet": diet}
    ss.summary = update_summary(ss.summary, info)
    text = (f"Party of {party}, {when}, {mode}, about ${budget} each, "
            f"{cuisine}, {diet}")
    ss.messages.append({"role": "user", "content": text})
    ss.messages.append({"role": "assistant", "content": build_reply(ss.summary)})
    if ss.user:
        save_profile(ss.user, ss.summary)

# ---- Chat (follow-ups reuse the stored request) ----
for m in ss.messages[-10:]:            # keep a short buffer on screen
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if prompt := st.chat_input("Refine it: cheaper, vegetarian, pickup..."):
    ss.messages.append({"role": "user", "content": prompt})
    lower = prompt.lower()
    if "cheaper" in lower:
        ss.summary["budget"] = max(5, int(ss.summary.get("budget", 20)) - 5)
    if "pickup" in lower:
        ss.summary["mode"] = "Pickup"
    # TODO: replace the keyword rules above with an LLM (OpenAI/LangChain)
    ss.messages.append({"role": "assistant", "content": build_reply(ss.summary)})
    if ss.user:
        save_profile(ss.user, ss.summary)
    st.rerun()

with st.expander("What the Host remembers right now"):
    st.json(ss.summary)