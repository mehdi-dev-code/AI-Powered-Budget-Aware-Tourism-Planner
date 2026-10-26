"""Tests for the evaluation script itself, using a scripted fake model (no key, no network)."""
import shutil
import pytest
from backend import database
from backend.database import initialize_database
import evaluate


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


class Fake:
    def __init__(self, script):
        self.script = list(script)

    def step(self, system, messages, tools):
        out = self.script.pop(0)
        return {"text": out.get("text", ""), "calls": out.get("calls", []), "raw": None}


def test_case_passes_and_fails_on_its_check():
    case = evaluate.CASES[2]                                   # missing budget
    good = evaluate.run_case(case, Fake([{"text": "What is your total budget and starting city?"}]))
    bad = evaluate.run_case(case, Fake([{"text": "Sure, here you go."}]))
    assert good["passed"] == "PASS" and bad["passed"] == "FAIL"


def test_full_trip_case_uses_the_planner():
    args = {"origin": "Islamabad", "destination": "Hunza", "budget": 60000, "nights": 2, "group_size": 2}
    row = evaluate.run_case(evaluate.CASES[0], Fake([{"calls": [{"name": "plan_trip", "args": args}]},
                                                     {"text": "Within budget. Please confirm prices with the hotel."}]))
    assert row["passed"] == "PASS" and row["plan_total_pkr"] and row["tools_called"] == "plan_trip"


def test_results_are_written(tmp_path):
    rows = [evaluate.run_case(evaluate.CASES[2], Fake([{"text": "Your budget?"}]))]
    assert evaluate.write_results(rows, tmp_path / "out") == 1
    assert "1/1" in (tmp_path / "out" / "results.md").read_text(encoding="utf-8")


def test_llm_error_is_reported_not_raised():
    from backend.llm import LLMError

    class Broken:
        def step(self, *a):
            raise LLMError("busy")
    rows = evaluate.run_all(Broken(), cases=evaluate.CASES[:1], delay=0, sleep=lambda s: None)
    assert rows[0]["passed"] == "FAIL" and "busy" in rows[0]["note"]
