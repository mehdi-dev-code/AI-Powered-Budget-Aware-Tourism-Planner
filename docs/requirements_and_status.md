# Weeks 1–4 Summary

## Week 1 — Project Idea
**Project:** AI-Powered Budget-Aware Tourism Planner
**Problem:** Travelers must piece together hotel, transport and cost information from several sources, and may miss affordable local hotels that are not listed online.
**Users:** Tourists, students, families and travel agencies. Participating local hotels are supporting stakeholders.

## Week 2 — Proposal & Scope
**Objectives**
1. Capture traveler budget, dates, group size and preferences.
2. Maintain a local hotel dataset (rates, location, facilities, contact, availability).
3. Compare accommodation and transport options.
4. Generate itineraries with itemized estimated costs.

**Scope:** Hunza, Skardu and Gilgit. No online booking or payments. Estimates are not guaranteed prices.

## Week 3 — Requirements, Data & Design
- FR1: accept origin, destination, budget, group size and preferences. Trip length is entered as nights; travel dates are a planned later feature (seasonal prices, weather, availability).
- FR2: retrieve hotel records from the database.
- FR3: compare transport options when data is available, including one-stop connections built from existing routes.
- FR4: build a trip estimate, choosing a hotel that fits the budget first and the best preference match second.
- FR5: show itemized costs and budget status. Plans are saved only when the user chooses to.
- FR6: update hotel data (CSV refresh in Week 4; a hotel-owner workflow is planned for a later iteration).

**Design change:** the Week 3 plan listed FastAPI and MongoDB. The Week 4 prototype uses Python data-access functions and SQLite (no server or API keys needed). The tables carry the same fields, so an API layer or another database can be added later.

## Week 4 — Data & Backend Foundation
**Done:** sample CSV data, data validation and cleaning, controlled CSV-to-database refresh, SQLite schema, hotel search, transport lookup, trip storage on request, input validation, itemized estimate with partial-estimate handling when transport is missing, Streamlit demo, 32 automated tests, Git-ready project structure.
**Not done:** verified real hotel data, live APIs, LLM/agents (Week 5), travel dates, FastAPI layer, Admin login, online booking and payments.
**Git:** create the repository, make the first commits and push to GitHub (the folder already has a `.gitignore`).
