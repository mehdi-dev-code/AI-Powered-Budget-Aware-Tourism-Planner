import shutil
import pandas as pd
import pytest
from backend import database
from backend.database import (initialize_database, search_hotels, clean_hotels, clean_transport,
                              refresh_data, get_connection, get_connecting_options, list_origin_cities,
                              list_trips, delete_trip)
from backend.planner import build_budget_plan, save_plan, summarize_trip, to_pakistan_time


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    """Use a throwaway database and a copy of the CSV files so tests never touch real data."""
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


# ---- database and search
def test_seed_and_hotel_search():
    rows = search_hotels("Hunza")
    assert rows
    assert all(row["city"].lower() == "hunza" for row in rows)
    prices = [row["price_per_night"] for row in rows]
    assert prices == sorted(prices)


def test_price_filter():
    assert all(row["price_per_night"] <= 3500 for row in search_hotels("Skardu", 3500))


# ---- validation
def test_clean_hotels_removes_bad_rows():
    raw = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["A Hotel", "A Hotel", " B Hotel ", None, "C Hotel"],
        "city": ["hunza", "Hunza", "SKARDU ", "Gilgit", "Gilgit"],
        "price_per_night": [3000, 3000, 4000, 2000, -50],
    })
    clean, rejected = clean_hotels(raw)
    assert list(clean["name"]) == ["A Hotel", "B Hotel"]
    assert list(clean["city"]) == ["Hunza", "Skardu"]
    assert len(rejected) == 3


def test_whitespace_only_names_and_cities_are_rejected():
    raw = pd.DataFrame({"id": [1, 2, 3], "name": ["   ", "Good Hotel", "Other"], "city": ["Hunza", " ", "Hunza"],
                        "price_per_night": [1000, 1000, 1000]})
    clean, rejected = clean_hotels(raw)
    assert list(clean["name"]) == ["Other"]
    assert [why for _, why in rejected] == ["missing name", "missing city"]


def test_transport_duration_must_be_positive():
    raw = pd.DataFrame({"id": [1, 2, 3], "mode": ["Bus", "Bus", "Van"], "from_city": ["Islamabad"] * 3,
                        "to_city": ["Hunza", "Skardu", "Gilgit"], "fare_per_person": [5000, 5000, 5000],
                        "duration_hours": [0, -3, 15]})
    clean, rejected = clean_transport(raw)
    assert list(clean["to_city"]) == ["Gilgit"] and len(rejected) == 2


def test_malformed_dates_and_duplicate_ids_are_rejected():
    raw = pd.DataFrame({"id": [1, 1, 2, 3], "name": ["A", "B", "C", "D"], "city": ["Hunza"] * 4,
                        "price_per_night": [1000] * 4,
                        "updated_at": ["2026-10-20", "2026-10-20", "20/13/2026", "2026-10-21"]})
    clean, rejected = clean_hotels(raw)
    assert list(clean["id"]) == [1, 3]
    assert {why.split(" (")[0] for _, why in rejected} == {"duplicate id", "malformed updated_at"}


# ---- planner and budget check
def test_plan_total_matches_items():
    result = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "")
    assert result["total_estimate"] == sum(result["costs"].values())
    assert any(k.startswith("Transport (return") for k in result["costs"])
    assert result["partial"] is False and result["budget_status"] == "within"


def test_no_route_at_all_is_partial_and_not_conclusive():
    result = build_budget_plan("Lahore", "Hunza", 40000, 2, 2, "")
    assert result["partial"] is True
    assert result["budget_status"] == "inconclusive"
    assert result["within_budget"] is None
    assert any("NOT included" in k for k in result["costs"])


def test_no_route_but_already_over_budget_is_conclusive():
    result = build_budget_plan("Lahore", "Hunza", 5000, 2, 2, "")
    assert result["partial"] is True and result["budget_status"] == "over"


def test_connecting_route_used_when_no_direct_route():
    options = get_connecting_options("Hunza", "Skardu")
    assert options and options[0]["via"] == "Gilgit" and options[0]["fare_per_person"] == 1500 + 2500
    result = build_budget_plan("Hunza", "Skardu", 40000, 2, 2, "")
    assert result["partial"] is False
    assert any("connecting route via Gilgit" in k for k in result["costs"])
    assert any("No direct route" in w for w in result["warnings"])
    assert [v for k, v in result["costs"].items() if k.startswith("Transport")] == [4000 * 2 * 2]


def test_direct_route_is_preferred_over_connection():
    result = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "")
    assert not result["transport"].get("connecting")


def test_over_budget_flag():
    assert build_budget_plan("Islamabad", "Hunza", 5000, 2, 2, "")["within_budget"] is False


def test_rooms_scale_with_group_size():
    small = build_budget_plan("Islamabad", "Hunza", 100000, 2, 2, "")
    big = build_budget_plan("Islamabad", "Hunza", 100000, 2, 5, "")
    assert small["rooms"] == 1 and big["rooms"] == 3


def test_preferences_select_matching_hotel_when_budget_allows():
    result = build_budget_plan("Islamabad", "Hunza", 60000, 2, 2, "mountain view")
    assert result["hotel"]["name"] == "Hunza Valley Guest House"
    assert result["budget_status"] == "within" and not result["unmet_preferences"]


def test_budget_beats_preferences():
    plain = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "")
    wanted = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "mountain view")
    assert plain["budget_status"] == "within"
    assert wanted["budget_status"] == "within"                      # the preference must not break the budget
    assert wanted["hotel"]["name"] == plain["hotel"]["name"]
    assert any("would exceed your budget" in w for w in wanted["warnings"])
    assert wanted["unmet_preferences"] == ["mountain view"]


def test_partial_preference_match_is_reported():
    result = build_budget_plan("Islamabad", "Skardu", 60000, 2, 2, "wifi, hot water")
    assert result["hotel"]["name"] == "Skardu Budget Guest House"
    assert result["unmet_preferences"] == ["wifi"]
    assert any("does not match these preferences: wifi" in w for w in result["warnings"])


def test_food_days_are_stated():
    result = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "")
    assert any("Food estimate (3 days)" in k for k in result["costs"])
    assert "nights + 1 days" in result["assumptions"]


def test_cheapest_hotel_shown_when_nothing_fits():
    result = build_budget_plan("Islamabad", "Hunza", 5000, 2, 2, "mountain view")
    assert result["hotel"]["name"] == "Karimabad Budget Stay"
    assert any("No hotel in Hunza fits" in w for w in result["warnings"])


@pytest.mark.parametrize("budget,nights,group", [(0, 2, 2), (1000, 0, 2), (1000, 2, 0)])
def test_invalid_numbers_raise(budget, nights, group):
    with pytest.raises(ValueError):
        build_budget_plan("Islamabad", "Hunza", budget, nights, group)


def test_same_origin_and_destination_raises():
    with pytest.raises(ValueError):
        build_budget_plan("Hunza", "Hunza", 10000, 2, 2)


def test_unsupported_destination_lists_supported_cities():
    result = build_budget_plan("Lahore", "Murree", 30000, 2, 2)
    assert "error" in result and "Hunza" in result["error"]


# ---- saving is optional
def test_plans_are_saved_only_on_request():
    result = build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, "")
    build_budget_plan("Islamabad", "Skardu", 40000, 2, 2, "")
    assert list_trips() == []                                      # building a plan saves nothing
    trip_id = save_plan(result)
    trips = list_trips()
    assert len(trips) == 1 and trips[0]["id"] == trip_id
    summary = summarize_trip(trips[0])
    assert summary["Estimated total (PKR)"] == result["total_estimate"] and summary["Hotel"] == result["hotel"]["name"]
    delete_trip(trip_id)
    assert list_trips() == []


def test_saved_time_is_shown_in_pakistan_time():
    assert to_pakistan_time("2026-10-22 07:28:00") == "2026-10-22 12:28"
    assert to_pakistan_time("2026-10-22 20:30:00") == "2026-10-23 01:30"      # rolls over to the next day
    assert to_pakistan_time(None) == "" and to_pakistan_time("not a date") == "not a date"
    save_plan(build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, ""))
    summary = summarize_trip(list_trips()[0])
    assert "Saved at (PKT)" in summary and len(summary["Saved at (PKT)"]) == 16


def test_origin_cities_come_from_the_data():
    origins = list_origin_cities()
    assert "Islamabad" in origins and "Hunza" in origins


# ---- data refresh
def test_refresh_applies_csv_edits(temp_env):
    csv = temp_env / "hotels_sample.csv"
    df = pd.read_csv(csv)
    df.loc[df["id"] == 1, "price_per_night"] = 9999                  # price change
    df.loc[len(df)] = [7, "New Hunza Inn", "Hunza", "Aliabad", 2800, "Wi-Fi", "0300-0000000", "Open", "2026-10-22"]
    df.to_csv(csv, index=False)
    assert all(h["price_per_night"] != 9999 for h in search_hotels("Hunza"))   # not applied automatically
    report = refresh_data()
    assert report["hotels"]["updated"] == 1 and report["hotels"]["added"] == 1
    assert any(h["price_per_night"] == 9999 for h in search_hotels("Hunza"))
    assert any(h["name"] == "New Hunza Inn" for h in search_hotels("Hunza"))


def test_refresh_prune_removes_deleted_rows_only_when_asked(temp_env):
    csv = temp_env / "hotels_sample.csv"
    df = pd.read_csv(csv)
    df[df["id"] != 6].to_csv(csv, index=False)
    refresh_data()
    assert any(h["id"] == 6 for h in search_hotels("Gilgit"))
    assert refresh_data(prune=True)["hotels"]["removed"] == 1
    assert not any(h["id"] == 6 for h in search_hotels("Gilgit"))


def test_refresh_rejects_bad_rows_and_keeps_saved_trips(temp_env):
    save_plan(build_budget_plan("Islamabad", "Hunza", 40000, 2, 2, ""))
    csv = temp_env / "transport_sample.csv"
    df = pd.read_csv(csv)
    df.loc[df["id"] == 1, "duration_hours"] = -5
    df.to_csv(csv, index=False)
    report = refresh_data()
    assert len(report["transport"]["rejected"]) == 1
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1
    conn.close()


def test_csv_missing_required_column_is_reported_not_crashing(temp_env):
    csv = temp_env / "hotels_sample.csv"
    pd.read_csv(csv).drop(columns=["city"]).to_csv(csv, index=False)
    report = refresh_data(prune=True)
    assert report["hotels"]["rejected"] == [(0, "missing required column: city")]
    assert report["hotels"]["removed"] == 0 and len(search_hotels("Hunza")) == 2   # database left untouched
    assert report["transport"]["rejected"] == []                                 # the other file still refreshes


def test_missing_optional_columns_are_allowed(temp_env):
    csv = temp_env / "transport_sample.csv"
    pd.read_csv(csv).drop(columns=["duration_hours", "notes", "updated_at"]).to_csv(csv, index=False)
    assert refresh_data()["transport"]["rejected"] == []


def test_missing_or_empty_csv_file_is_reported(temp_env):
    (temp_env / "hotels_sample.csv").unlink()
    (temp_env / "transport_sample.csv").write_text("")
    report = refresh_data(prune=True)
    assert "file not found" in report["hotels"]["rejected"][0][1]
    assert "cannot read" in report["transport"]["rejected"][0][1]
