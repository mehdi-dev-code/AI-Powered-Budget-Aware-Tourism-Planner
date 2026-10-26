import streamlit as st
import pandas as pd
from backend.database import (initialize_database, search_hotels, list_cities, list_origin_cities, refresh_data,
                              describe_rejection, list_trips, delete_trip, DATA_DIR)
from backend.planner import build_budget_plan, save_plan, summarize_trip
from backend.agent import run_agent
from backend.llm import GeminiLLM, LLMError, api_key_available

st.set_page_config(page_title="Budget Tourism Planner", page_icon="🧭", layout="wide")
initialize_database()
st.title("🧭 AI-Powered Budget-Aware Tourism Planner")
st.caption("Prototype • Rule-based planner plus a Gemini-powered trip assistant that can only use the local database")
st.warning("Demo only: hotel records, contacts, fares and availability are illustrative sample data—not verified live offers.")


def show_plan(result, key="plan"):
    """Render a plan stored in session state, so it survives widget changes."""
    st.caption(f"Plan shown: {result['origin']} → {result['destination']}, {result['nights']} night(s), "
               f"{result['group_size']} traveler(s), budget PKR {result['budget']:,.0f}"
               + (f", preferences: {result['preferences']}" if result["preferences"] else "")
               + ". Press the build button again after changing the inputs.")
    partial = result["partial"]
    if partial:
        st.warning("Partial estimate — transportation cost unavailable")
    c1, c2, c3 = st.columns(3)
    c1.metric("Estimated total (partial)" if partial else "Estimated total", f"PKR {result['total_estimate']:,}")
    c2.metric("Your budget", f"PKR {result['budget']:,.0f}")
    c3.metric("Remaining before transport" if partial else "Remaining / over budget", f"PKR {result['remaining']:,}")
    if result["budget_status"] == "within":
        st.success("This sample estimate is within budget.")
    elif result["budget_status"] == "over":
        if partial:
            st.error("Already over budget without transportation. Adding transport will only increase the total.")
        else:
            st.error("This sample estimate exceeds the budget. Try a higher budget, fewer nights, or fewer travelers.")
    else:
        st.info("Budget check is not conclusive: transportation cost is missing. Add it before deciding if the trip fits the budget.")
    st.markdown("#### Selected accommodation")
    st.write(f"**{result['hotel']['name']}** — {result['hotel']['city']}, {result['hotel']['area']}")
    st.write(f"PKR {result['hotel']['price_per_night']:,.0f} per night • {result['hotel']['facilities']} • {result['rooms']} room(s)")
    for warning in result["warnings"]:
        if not warning.startswith("No transport data"):          # already shown as the partial-estimate banner
            st.warning(warning)
    if result["transport"]:
        st.markdown("#### Lowest-cost transport option")
        st.write(f"{result['transport']['mode']}: PKR {result['transport']['fare_per_person']:,.0f} per person each way (estimate)")
    st.markdown("#### Itemized cost estimate")
    costs = pd.DataFrame([{"Cost item": k, "Estimated PKR": v} for k, v in result["costs"].items()])
    st.dataframe(costs, hide_index=True, width="stretch")
    st.caption("Assumptions: " + result["assumptions"])
    st.info(result["notice"])
    if result.get("saved_trip_id"):
        st.success(f"Plan saved as trip #{result['saved_trip_id']}. See the Saved trips tab.")
    elif st.button("Save this plan", key=f"save_{key}"):
        result["saved_trip_id"] = save_plan(result)
        st.success(f"Plan saved as trip #{result['saved_trip_id']}. See the Saved trips tab.")


with st.sidebar:
    st.header("Plan a trip")
    destination = st.selectbox("Destination", list_cities())
    origin = st.selectbox("Starting city", [c for c in list_origin_cities() if c != destination])
    budget = st.number_input("Total budget (PKR)", min_value=1000, max_value=1000000, value=30000, step=1000)
    nights = st.number_input("Nights", min_value=1, max_value=30, value=2, step=1)
    group_size = st.number_input("Travelers", min_value=1, max_value=20, value=2, step=1)
    preferences = st.text_input("Preferences", placeholder="e.g. family rooms, Wi-Fi, hot water")
    create = st.button("Build sample budget plan", type="primary", width="stretch")

if create:
    try:
        st.session_state["plan"] = build_budget_plan(origin, destination, budget, nights, group_size, preferences)
    except Exception as exc:
        st.session_state["plan"] = {"error": f"Could not build plan: {exc}"}

tab1, tab_ai, tab2, tab3, tab4 = st.tabs(["Trip planner", "Trip assistant (AI)", "Hotel database", "Transport data", "Saved trips"])
with tab1:
    st.subheader("Budget-aware planning baseline")
    st.write("Enter trip details in the sidebar, then generate a transparent itemized estimate from the local sample database.")
    st.caption("Trip length is entered as a number of nights. Travel dates (for seasonal prices, weather and hotel availability) are planned for a later version.")
    plan = st.session_state.get("plan")
    if plan is None:
        st.info("Try Islamabad → Hunza, 2 nights, 2 travelers, budget PKR 40,000.")
    elif "error" in plan:
        st.error(plan["error"])
    else:
        show_plan(plan)
    st.markdown("#### Current supported sample destinations")
    st.write(" • ".join(list_cities()))

with tab_ai:
    st.subheader("Trip assistant (Gemini)")
    st.caption("Describe your trip in your own words. The assistant looks up hotels and fares in the local database and uses the "
               "planner for every number. It cannot book anything.")
    if not api_key_available():
        st.info("Add your Gemini key to a `.env` file (GEMINI_API_KEY=...) and restart the app to enable the assistant.")
    st.session_state.setdefault("chat", [])
    st.session_state.setdefault("agent_history", [])
    for m in st.session_state["chat"]:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            for w in m.get("warnings", []):
                st.warning(w)
    prompt = st.chat_input("e.g. 2 nights in Hunza from Islamabad for 2 people, budget 60,000 PKR")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state["chat"].append({"role": "user", "text": prompt})
        with st.chat_message("assistant"):
            try:
                with st.spinner("Looking up the database..."):
                    out = run_agent(prompt, st.session_state["agent_history"], llm=GeminiLLM())
                st.session_state["agent_history"] = out["history"]
                st.markdown(out["reply"])
                for w in out["warnings"]:
                    st.warning(w)
                st.session_state["chat"].append({"role": "assistant", "text": out["reply"], "warnings": out["warnings"]})
                if out["plan"]:
                    st.session_state["agent_plan"] = out["plan"]
                    st.session_state["agent_steps"] = out["steps"]
            except LLMError as exc:
                st.error(str(exc))
    if st.session_state.get("agent_plan"):
        st.markdown("#### Latest plan from the assistant")
        show_plan(st.session_state["agent_plan"], key="assistant")
        with st.expander("What the assistant looked up"):
            for s in st.session_state.get("agent_steps", []):
                st.write(f"`{s['tool']}`", s["args"])
    if st.session_state["chat"] and st.button("Clear conversation"):
        for k in ("chat", "agent_history", "agent_plan", "agent_steps"):
            st.session_state.pop(k, None)
        st.rerun()

with tab2:
    st.subheader("Local hotel database")
    city = st.selectbox("Filter hotels by city", list_cities(), key="hotel_city")
    max_price = st.number_input("Maximum nightly rate (PKR)", min_value=1000, max_value=100000, value=10000, step=500)
    rows = search_hotels(city, max_price)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df[["name", "city", "area", "price_per_night", "facilities", "contact", "availability", "updated_at"]], hide_index=True, width="stretch")
    else:
        st.info("No hotels match this filter.")
    st.caption("Contacts and availability are placeholders for demonstration and must be replaced with participating hotel data.")

with tab3:
    st.subheader("Transport dataset")
    st.caption("Illustrative fares; no live transport API is connected.")
    st.dataframe(pd.read_csv(DATA_DIR / "transport_sample.csv"), hide_index=True, width="stretch")

with tab4:
    st.subheader("Saved trips")
    st.caption("Times are shown in Pakistan time (PKT, UTC+5).")
    trips = list_trips()
    if not trips:
        st.info("No saved trips yet. Build a plan and press “Save this plan”.")
    else:
        st.dataframe(pd.DataFrame([summarize_trip(t) for t in trips]), hide_index=True, width="stretch")
        to_delete = st.selectbox("Delete a saved trip", [t["id"] for t in trips], format_func=lambda i: f"Trip #{i}")
        if st.button("Delete selected trip"):
            delete_trip(to_delete)
            st.rerun()

with st.expander("Admin: refresh data from CSV files (prototype, no login)"):
    st.write("After editing `data/hotels_sample.csv` or `data/transport_sample.csv`, apply the changes to the database here. Saved trips are never changed.")
    prune = st.checkbox("Also remove records that are no longer in the CSV files")
    if st.button("Refresh data now"):
        report = refresh_data(prune=prune)
        for table, r in report.items():
            st.write(f"**{table}**: {r['added']} added, {r['updated']} updated, {r['unchanged']} unchanged, {r['removed']} removed, {len(r['rejected'])} rejected")
            for line, why in r["rejected"]:
                st.warning(f"{table} — {describe_rejection(line, why)}")

with st.expander("Implementation status"):
    st.markdown("""
    **Included in this prototype**
    - CSV seed datasets for sample hotels and transport
    - SQLite tables: `hotels`, `transport`, `trips`
    - Data validation and cleaning (empty fields, negative or zero values, malformed dates, missing columns, duplicates) before loading
    - Controlled data refresh from the CSV files (Admin panel or `python refresh_data.py`)
    - Data-access functions for filtering hotels and transport
    - Budget-first hotel choice, partial-estimate handling and an itemized cost estimate
    - Plans are saved only when you choose to, and listed in the Saved trips tab
    - Basic Streamlit interface to demonstrate data/backend access
    - Gemini trip assistant: tool calling over the same database and planner, with checks on prices, hotel names and booking claims

    **Not implemented yet**
    - Live hotel/transport APIs or verified partner availability
    - Multiple specialised agents, RAG or embeddings, live research
    - Travel dates, online booking and payments
    - Production authentication (the Admin panel has no login), deployment, or real customer data
    """)
