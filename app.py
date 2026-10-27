import streamlit as st
import pandas as pd
from backend.database import (initialize_database, search_hotels, list_cities, list_origin_cities, refresh_data,
                              describe_rejection, list_trips, delete_trip, effective_confidence, CONFIDENCE_LEVELS,
                              route_kind, DATA_DIR)
from backend.planner import build_budget_plan, save_plan, summarize_trip
from backend.agent import run_agent
from backend.owners import (admin_password_configured, check_admin_password, create_hotel, create_owner, delete_owner,
                            get_owner_hotel, list_owners, update_own_hotel, verify_owner)
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
    conf = result.get("data_confidence", {})
    overall = conf.get("overall", "Unknown")
    detail = f"hotel: {conf.get('hotel', 'Unknown')}" + (f", transport: {conf['transport']}" if conf.get("transport") else "")
    message = f"**Data confidence: {overall}** ({detail}). Food and activity costs are fixed assumptions."
    (st.success if overall == "Verified" else st.warning if overall == "Needs reconfirmation" else st.info)(message)
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
    origins = [c for c in list_origin_cities() if c != destination]
    route_notes = {None: " (no fare data yet)", "direct": "", "connecting": " (via a connection)"}
    origin = st.selectbox("Starting city", origins, format_func=lambda c: c + route_notes[route_kind(c, destination)])
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

tab1, tab_ai, tab2, tab3, tab4, tab_owner = st.tabs(["Trip planner", "Trip assistant (AI)", "Hotel database", "Transport data",
                                                      "Saved trips", "For hotel owners"])
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
        df["confidence"] = [effective_confidence(c, v) for c, v in zip(df["confidence"], df["last_verified"])]
        st.dataframe(df[["name", "city", "area", "price_per_night", "facilities", "contact", "availability", "confidence",
                         "last_verified", "source", "updated_at"]], hide_index=True, width="stretch")
        st.caption("Confidence: " + " > ".join(CONFIDENCE_LEVELS) + f". A Verified record older than 60 days is shown as "
                   "Needs reconfirmation.")
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

MAX_LOGIN_FAILURES = 5          # per browser session; restart the app to reset


def failed_too_often(key):
    return st.session_state.get(key, 0) >= MAX_LOGIN_FAILURES


with tab_owner:
    st.subheader("Hotel owner area")
    st.caption("Log in to update your own hotel's price, facilities and availability. A saved update is marked Verified "
               "with today's date and appears in plans and in the assistant straight away. Logins are created by the project admin.")
    owner = st.session_state.get("owner")
    if st.session_state.get("owner_message"):
        st.success(st.session_state.pop("owner_message"))
    if not owner:
        with st.form("owner_login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            if failed_too_often("owner_failures"):
                st.error("Too many wrong attempts in this session. Restart the app to try again.")
            else:
                found = verify_owner(username, password)
                if found:
                    st.session_state["owner"] = found
                    st.session_state["owner_failures"] = 0
                    st.rerun()
                st.session_state["owner_failures"] = st.session_state.get("owner_failures", 0) + 1
                st.error("Wrong username or password.")
    else:
        hotel = get_owner_hotel(owner["id"])
        st.write(f"Logged in as **{owner['username']}**: {hotel['name']}, {hotel['city']}")
        st.caption(f"Current record: confidence {effective_confidence(hotel['confidence'], hotel['last_verified'])}, "
                   f"last verified {hotel['last_verified'] or 'never'}.")
        with st.form("owner_edit"):
            price = st.number_input("Price per room per night (PKR)", min_value=0, max_value=500000,
                                    value=int(hotel["price_per_night"]), step=100)
            facilities = st.text_input("Facilities (separate with ;)", value=hotel["facilities"] or "")
            availability = st.text_input("Availability", value=hotel["availability"] or "")
            contact = st.text_input("Contact", value=hotel["contact"] or "")
            area = st.text_input("Area", value=hotel["area"] or "")
            saved = st.form_submit_button("Save and confirm these details", type="primary")
        if saved:
            try:
                update_own_hotel(owner["id"], price, facilities, availability, contact, area)
                st.session_state["owner_message"] = "Saved. Your hotel is now marked Verified as of today."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if st.button("Log out"):
            st.session_state.pop("owner", None)
            st.rerun()

with st.expander("Admin (password required)"):
    if not admin_password_configured():
        st.info("Admin tools are locked. Add ADMIN_PASSWORD=your-password to the .env file and restart the app.")
    elif not st.session_state.get("is_admin"):
        admin_pw = st.text_input("Admin password", type="password", key="admin_pw")
        if st.button("Unlock admin tools"):
            if failed_too_often("admin_failures"):
                st.error("Too many wrong attempts in this session. Restart the app to try again.")
            elif check_admin_password(admin_pw):
                st.session_state["is_admin"] = True
                st.session_state["admin_failures"] = 0
                st.rerun()
            else:
                st.session_state["admin_failures"] = st.session_state.get("admin_failures", 0) + 1
                st.error("Wrong password.")
    else:
        if st.button("Lock admin tools"):
            st.session_state["is_admin"] = False
            st.rerun()
        st.markdown("##### Refresh data from CSV files")
        st.write("After editing `data/hotels_sample.csv` or `data/transport_sample.csv`, apply the changes to the database here. "
                 "Saved trips and hotels maintained in the app (owner or admin) are never changed.")
        prune = st.checkbox("Also remove records that are no longer in the CSV files")
        if st.button("Refresh data now"):
            report = refresh_data(prune=prune)
            for table, r in report.items():
                st.write(f"**{table}**: {r['added']} added, {r['updated']} updated, {r['unchanged']} unchanged, "
                         f"{r['removed']} removed, {len(r['rejected'])} rejected, {r.get('protected', 0)} kept (app-managed)")
                for line, why in r["rejected"]:
                    st.warning(f"{table} — {describe_rejection(line, why)}")

        st.markdown("##### Add a hotel")
        with st.form("add_hotel", clear_on_submit=True):
            h_name = st.text_input("Hotel name")
            h_city = st.selectbox("City", list_cities())
            h_area = st.text_input("Area")
            h_price = st.number_input("Price per room per night (PKR)", min_value=0, max_value=500000, value=4000, step=100)
            h_fac = st.text_input("Facilities (separate with ;)")
            h_contact = st.text_input("Contact")
            h_avail = st.text_input("Availability")
            if st.form_submit_button("Add hotel"):
                try:
                    new_id = create_hotel(h_name, h_city, h_area, h_price, h_fac, h_contact, h_avail)
                    st.success(f"Hotel added with id {new_id}. It stays 'Unknown' until its owner confirms the details.")
                except ValueError as exc:
                    st.error(str(exc))

        st.markdown("##### Owner logins")
        owners = list_owners()
        if owners:
            st.dataframe(pd.DataFrame(owners)[["id", "username", "hotel", "city", "created_at"]], hide_index=True, width="stretch")
        taken = {o["hotel_id"] for o in owners}
        free = [h for city_name in list_cities() for h in search_hotels(city_name) if h["id"] not in taken]
        if free:
            with st.form("add_owner", clear_on_submit=True):
                labels = {h["id"]: f"{h['name']} ({h['city']})" for h in free}
                o_hotel = st.selectbox("Hotel", list(labels), format_func=labels.get)
                o_user = st.text_input("Username")
                o_pass = st.text_input("Password (at least 8 characters)", type="password")
                if st.form_submit_button("Create owner login"):
                    try:
                        create_owner(o_user, o_pass, o_hotel)
                        st.success(f"Login '{o_user}' created for {labels[o_hotel]}. Give the password to the owner privately.")
                    except ValueError as exc:
                        st.error(str(exc))
        else:
            st.caption("Every hotel already has an owner login.")
        if owners:
            names = {o["id"]: f"{o['username']} ({o['hotel']})" for o in owners}
            to_remove = st.selectbox("Remove a login", list(names), format_func=names.get)
            if st.button("Remove selected login"):
                delete_owner(to_remove)
                st.rerun()

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
    - Confidence labels on every hotel and fare, a hotel-owner login to update your own hotel, and a password-protected admin area
    - Gemini trip assistant: tool calling over the same database and planner, with checks on prices, hotel names and booking claims

    **Not implemented yet**
    - Live hotel/transport APIs or verified partner availability
    - Multiple specialised agents, RAG or embeddings, live research
    - Travel dates, online booking and payments
    - Production-grade authentication (logins are basic, with no password reset), deployment, or real customer data
    """)
