# AI-Powered Budget-Aware Tourism Planner — Week 4: Data & Backend Foundation

A budget-aware trip planner for northern Pakistan (Hunza, Skardu, Gilgit). This Week 4 version is the data and backend foundation that the Week 5 AI agents will build on.

## What is implemented
- Sample hotel and transport datasets (`data/*.csv`), with prices in PKR.
- Data validation and cleaning before loading: whitespace-only or empty names and cities are rejected, prices must be 0 or more, travel durations must be positive, dates must be YYYY-MM-DD, required columns must exist, and duplicate rows or ids are removed. Rejected rows and file-level problems (missing column, missing file) are reported, and the database is left unchanged for a broken file.
- Controlled data refresh: edits to the CSV files are applied to the database with `python refresh_data.py` (add `--prune` to also remove deleted rows) or the Admin panel in the app. Saved trips are never changed.
- SQLite database with three tables: `hotels`, `transport`, `trips`.
- Data-access functions: hotel search, transport lookup (direct and one-stop connections), saving trips.
- Rule-based planner: validates input, picks a hotel that fits the budget (then the best preference match, then the lowest price) and warns when a preference had to be dropped, and builds an itemized cost estimate with a budget check. When transport data is missing it shows a partial estimate, and the budget check is not treated as conclusive.
- Streamlit interface: trip planner, hotel database, transport data and saved trips tabs. The result stays on screen when inputs change. A plan is saved only when you press "Save this plan". Saved times are shown in Pakistan time (PKT, UTC+5); the database stores UTC.
- Automated tests (`pytest`).

## Not implemented yet
- AI agents or an LLM (planned for Week 5).
- Live hotel or transport APIs, and verified partner data.
- Travel dates. Trip length is entered as a number of nights; dates for seasonal prices, weather and hotel availability are planned for a later version.
- Login for the Admin refresh panel (it is open in this prototype).
- A FastAPI layer (the Week 3 design mentioned one; the prototype calls the Python functions directly).
- Online booking and payments (out of scope).

## Data note
Hotel names, contacts, rates, availability and transport fares are illustrative sample data. They are not verified offers and must be replaced with real, confirmed data.

## Cost assumptions
2 people per room; food PKR 1,200 per person per day for nights + 1 days; activities PKR 800 per person; transport is a return trip per person. If a route has no direct record, the planner combines two existing legs through a connecting city (for example Hunza to Skardu via Gilgit, fares added, waiting time not modeled) and says so in a warning. If no route exists at all, the app shows "Partial estimate — transportation cost unavailable". A trip that is already over budget stays flagged as over budget; otherwise the check is marked inconclusive until transport cost is supplied.

## Run locally
1. Install Python 3.10 or newer.
2. `pip install -r requirements.txt`
3. `streamlit run app.py`
4. Run the tests with `pytest` (or `python -m pytest`).
5. After editing the CSV files, run `python refresh_data.py` to update the database.

The database file `tourism_planner.db` is created automatically from the CSV files on first run.

## Project structure
```text
tourism_planner_project/
├── app.py                 Streamlit interface (with Admin refresh panel)
├── refresh_data.py        apply CSV edits to the database
├── backend/
│   ├── database.py        schema, validation, refresh, data-access functions
│   └── planner.py         validation and cost estimate
├── data/                  hotels_sample.csv, transport_sample.csv
├── docs/                  requirements_and_status.md
├── tests/                 test_planner.py
├── pytest.ini
├── requirements.txt
└── .gitignore
```

## Data model
- `hotels`: id, name, city, area, price_per_night, facilities, contact, availability, updated_at
- `transport`: id, mode, from_city, to_city, fare_per_person, duration_hours, notes, updated_at
- `trips`: id, origin, destination, budget, nights, group_size, preferences, cost_breakdown, created_at
