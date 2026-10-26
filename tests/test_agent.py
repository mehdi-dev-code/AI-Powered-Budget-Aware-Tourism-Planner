"""Tests for the AI agent. A scripted fake model replaces Gemini, so no API key or network is needed."""
import shutil
from types import SimpleNamespace
import pytest
from backend import database
from backend.database import initialize_database
from backend.planner import build_budget_plan
from backend.tools import call_tool, plan_trip, find_hotels, find_transport, TOOL_SPECS
from backend.agent import run_agent, verify_reply
from backend.llm import GeminiLLM, LLMError, _to_contents


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


class FakeLLM:
    """Returns scripted turns: {"text": ..., "calls": [...]}. Records what it was sent."""
    def __init__(self, script):
        self.script, self.seen = list(script), []

    def step(self, system, messages, tools):
        self.seen.append(list(messages))
        out = self.script.pop(0) if self.script else {"text": "", "calls": [{"name": "list_destinations", "args": {}}]}
        return {"text": out.get("text", ""), "calls": out.get("calls", []), "raw": None}


PLAN_ARGS = {"origin": "Islamabad", "destination": "Hunza", "budget": 60000, "nights": 2, "group_size": 2}


# ---- tools
def test_unknown_tool_returns_error():
    assert "error" in call_tool("book_hotel", {})


def test_bad_arguments_return_error_not_exception():
    assert "error" in call_tool("plan_trip", {"origin": "Islamabad"})


def test_float_arguments_from_the_model_are_coerced():
    result = plan_trip("Islamabad", "Hunza", 60000.0, 2.0, 2.0)
    assert result["nights"] == 2 and result["group_size"] == 2
    assert "2 nights" in " ".join(result["costs"])


def test_invalid_numbers_become_an_error():
    assert "error" in plan_trip("Islamabad", "Hunza", "lots", 2, 2)
    assert "error" in plan_trip("Islamabad", "Islamabad", 50000, 2, 2)


def test_find_hotels_and_unknown_city():
    assert find_hotels("Hunza")["hotels"]
    assert find_hotels("Atlantis")["hotels"] == []


def test_find_transport_marks_missing_routes():
    assert find_transport("Islamabad", "Hunza")["options"]
    assert find_transport("Karachi", "Hunza")["options"] == []


def test_every_tool_spec_has_a_function():
    from backend.tools import TOOL_FUNCS
    assert {s["name"] for s in TOOL_SPECS} == set(TOOL_FUNCS)


# ---- agent loop
def test_agent_calls_tool_and_returns_the_planners_plan():
    expected = build_budget_plan("Islamabad", "Hunza", 60000, 2, 2)
    reply = f"The estimate is PKR {expected['total_estimate']:,}. Please confirm prices with the hotel."
    llm = FakeLLM([{"calls": [{"name": "plan_trip", "args": PLAN_ARGS}]}, {"text": reply}])
    out = run_agent("2 nights in Hunza from Islamabad for 2, budget 60000", llm=llm)
    assert out["plan"]["total_estimate"] == expected["total_estimate"]
    assert out["steps"][0]["tool"] == "plan_trip"
    assert out["warnings"] == []


def test_agent_without_tool_calls_just_replies():
    llm = FakeLLM([{"text": "How many nights, and what is your budget?"}])
    out = run_agent("Plan a trip to Hunza", llm=llm)
    assert out["plan"] is None and out["steps"] == [] and "budget" in out["reply"]


def test_tool_error_gives_no_plan():
    bad = dict(PLAN_ARGS, destination="Atlantis")
    llm = FakeLLM([{"calls": [{"name": "plan_trip", "args": bad}]}, {"text": "I have no data for Atlantis."}])
    out = run_agent("trip to Atlantis", llm=llm)
    assert out["plan"] is None


def test_step_limit_stops_a_looping_model():
    llm = FakeLLM([])                       # always asks for a tool, never answers
    out = run_agent("hello", llm=llm, max_steps=3)
    assert len(llm.seen) == 3 and out["reply"]


def test_history_carries_over_between_messages():
    first = run_agent("hi", llm=FakeLLM([{"text": "Hello! Where would you like to go?"}]))
    llm = FakeLLM([{"text": "OK."}])
    run_agent("Hunza", history=first["history"], llm=llm)
    assert [m["role"] for m in llm.seen[0]] == ["user", "model", "user"]


# ---- answer checks
def test_invented_amount_is_flagged():
    llm = FakeLLM([{"calls": [{"name": "plan_trip", "args": PLAN_ARGS}]}, {"text": "It will cost PKR 12,345 in total."}])
    out = run_agent("plan it", llm=llm)
    assert any("12,345" in w for w in out["warnings"])


def test_amount_from_the_user_is_not_flagged():
    steps = []
    assert verify_reply("Your budget of PKR 60,000 is noted.", steps, ["budget 60000"]) == []


def test_invented_hotel_is_flagged_and_real_one_is_not():
    assert verify_reply("Stay at Serena Grand Hotel.", [])
    assert verify_reply("Stay at Hunza Valley Guest House.", []) == []


def test_booking_claim_is_flagged():
    assert verify_reply("Great, I have booked your room.", [])
    assert verify_reply("You can ask the hotel to confirm a booking.", []) == []


# ---- Gemini connector (no network)
def test_missing_key_gives_a_clear_message():
    try:
        GeminiLLM(env={})
        assert False, "should have raised"
    except LLMError as exc:
        assert "GEMINI_API_KEY" in str(exc)


def test_messages_convert_to_gemini_contents():
    fake = SimpleNamespace(
        Content=lambda role, parts: (role, parts),
        Part=SimpleNamespace(from_text=lambda text: ("text", text),
                             from_function_call=lambda name, args: ("call", name, args),
                             from_function_response=lambda name, response: ("resp", name, response)))
    raw = ("model", ["RAW"])
    msgs = [{"role": "user", "text": "hi"},
            {"role": "model", "text": "", "calls": [{"name": "a", "args": {}}, {"name": "b", "args": {}}], "raw": raw},
            {"role": "tool", "name": "a", "result": {"x": 1}},
            {"role": "tool", "name": "b", "result": {"y": 2}},
            {"role": "model", "text": "done", "calls": [], "raw": None}]
    contents = _to_contents(msgs, fake)
    assert contents[0] == ("user", [("text", "hi")])
    assert contents[1] is raw                                   # Gemini's own turn is reused untouched
    assert contents[2][0] == "tool" and len(contents[2][1]) == 2  # both results in one turn
    assert contents[3] == ("model", [("text", "done")])


class _FlakyClient:
    def __init__(self, failures, error="503 UNAVAILABLE. high demand"):
        self.calls, self.failures, self.error = 0, failures, error
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.error)
        return "OK"


def _bare_llm(client):
    llm = object.__new__(GeminiLLM)
    llm.client, llm.model = client, "m"
    return llm


def test_busy_gemini_is_retried_then_succeeds():
    client = _FlakyClient(failures=2)
    assert _bare_llm(client)._generate([], None, sleep=lambda s: None) == "OK" and client.calls == 3


def test_busy_gemini_gives_a_friendly_error_after_retries():
    client = _FlakyClient(failures=99)
    try:
        _bare_llm(client)._generate([], None, sleep=lambda s: None)
        assert False, "should have raised"
    except LLMError as exc:
        assert "overloaded" in str(exc) and client.calls == 4


def test_other_errors_are_not_retried():
    client = _FlakyClient(failures=99, error="403 PERMISSION_DENIED")
    try:
        _bare_llm(client)._generate([], None, sleep=lambda s: None)
        assert False, "should have raised"
    except LLMError as exc:
        assert "API key" in str(exc) and client.calls == 1
