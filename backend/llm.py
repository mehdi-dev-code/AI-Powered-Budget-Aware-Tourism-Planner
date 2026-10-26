"""Thin wrapper around the Gemini API. This is the only file that knows about Google's SDK.

The agent talks to any object with a `step(system, messages, tools)` method, so another provider (or a
scripted fake in the tests) can replace GeminiLLM without touching the agent.

Neutral message format used by the agent:
    {"role": "user",  "text": "..."}
    {"role": "model", "text": "...", "calls": [{"name": ..., "args": {...}}], "raw": <provider object or None>}
    {"role": "tool",  "name": "...", "result": {...}}
"""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gemini-3.8-flash"        # on Google's free tier; override with GEMINI_MODEL in .env
RETRY_DELAYS = (2, 5, 10)                 # seconds to wait before each retry when Google is busy


class LLMError(Exception):
    """A problem the user can act on (missing key, rate limit, network)."""


def load_env(path=None):
    """Read KEY=VALUE lines from .env into os.environ (existing variables win). No extra package needed."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key_available(env=None):
    load_env()
    env = os.environ if env is None else env
    return bool(env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"))


def _to_contents(messages, types):
    """Convert neutral messages to Gemini Content objects. `types` is google.genai.types (injectable for tests)."""
    contents, pending = [], []

    def flush():
        if pending:
            contents.append(types.Content(role="tool", parts=list(pending)))
            pending.clear()

    for m in messages:
        if m["role"] == "tool":                                  # consecutive tool results share one turn
            pending.append(types.Part.from_function_response(name=m["name"], response={"result": m["result"]}))
            continue
        flush()
        if m["role"] == "user":
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["text"])]))
        elif m.get("raw") is not None:                           # reuse Gemini's own turn (keeps thought signatures)
            contents.append(m["raw"])
        else:
            parts = [types.Part.from_text(text=m["text"])] if m.get("text") else []
            parts += [types.Part.from_function_call(name=c["name"], args=c["args"]) for c in m.get("calls", [])]
            contents.append(types.Content(role="model", parts=parts))
    flush()
    return contents


class GeminiLLM:
    def __init__(self, api_key=None, model=None, env=None):
        load_env()
        env = os.environ if env is None else env
        self.api_key = api_key or env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
        self.model = model or env.get("GEMINI_MODEL") or DEFAULT_MODEL
        if not self.api_key:
            raise LLMError("No Gemini API key found. Create one at https://aistudio.google.com/apikey, then put "
                           "GEMINI_API_KEY=your-key in a file named .env in the project folder and restart the app.")
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise LLMError("The Gemini library is not installed. Run: pip install -r requirements.txt")
        self._types = types
        self.client = genai.Client(api_key=self.api_key)

    def _generate(self, contents, config, sleep=time.sleep):
        """Call Gemini, retrying a few times when Google reports it is busy (503/500/overloaded)."""
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as exc:
                text = str(exc)
                busy = any(x in text for x in ("503", "UNAVAILABLE", "500", "INTERNAL", "overloaded", "high demand"))
                if busy and attempt < len(RETRY_DELAYS):
                    sleep(RETRY_DELAYS[attempt])
                    continue
                if busy:
                    raise LLMError("Gemini is overloaded right now (Google-side). Wait a minute and send the message "
                                   "again, or set GEMINI_MODEL in .env to another free model.") from exc
                if "429" in text or "RESOURCE_EXHAUSTED" in text:
                    raise LLMError("Gemini rate limit reached (free tier). Wait a minute and try again.") from exc
                if "404" in text or "NOT_FOUND" in text:
                    raise LLMError(f"Gemini does not know the model '{self.model}'. Set GEMINI_MODEL in .env to a model "
                                   "listed in AI Studio.") from exc
                if "API key" in text or "401" in text or "403" in text or "PERMISSION_DENIED" in text:
                    raise LLMError("Gemini rejected the API key. Check GEMINI_API_KEY in .env.") from exc
                raise LLMError(f"Gemini request failed: {text[:300]}") from exc

    def step(self, system, messages, tools):
        """One model turn. Returns {"text", "calls", "raw"}; function calls are returned, not executed."""
        t = self._types
        declarations = [t.FunctionDeclaration(name=s["name"], description=s["description"],
                                              parameters_json_schema=s["parameters"]) for s in tools]
        config = t.GenerateContentConfig(
            system_instruction=system, tools=[t.Tool(function_declarations=declarations)], temperature=0.2,
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True))   # our loop runs the tools
        response = self._generate(_to_contents(messages, t), config)
        candidate = response.candidates[0] if getattr(response, "candidates", None) else None
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        calls = [{"name": p.function_call.name, "args": dict(p.function_call.args or {})}
                 for p in parts if getattr(p, "function_call", None)]
        return {"text": text.strip(), "calls": calls, "raw": content}
