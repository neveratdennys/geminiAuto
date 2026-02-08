from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from datetime import datetime
import math
import os
import time
import json
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request
import requests

ROOT = Path(__file__).parent
CONTROLS_PATH = ROOT / "controls.json"
STATE_PATH = ROOT / "state.json"

DEFAULT_STATE: Dict[str, Any] = {
    "units": {"system": "metric"},
    "ac": {"power": False, "temperature_c": 22},
    "seat_heating": {"driver_level": 0, "passenger_level": 0},
    "seat_cooling": {"driver_level": 0, "passenger_level": 0},
    "tacc": {"enabled": False, "car_speed_kph": 88, "follow_distance": 2},
    "wipers": {"mode": "auto", "frequency_level": 1},
    "infotainment": {
        "power": True,
        "volume": 18,
        "active_app": "Radio",
        "radio_band": "FM",
        "bluetooth_connected": False,
        "screencast_active": False,
        "local_game": "Elden Ring",
    },
}


def get_api_key() -> Optional[str]:
    return os.environ.get("DASHBOARD_API_KEY")


def authorize_request() -> bool:
    api_key = get_api_key()
    if not api_key:
        return True
    header_key = request.headers.get("X-API-Key")
    if header_key and header_key == api_key:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1] == api_key
    return False


def unauthorized_response():
    return jsonify({"error": "Unauthorized"}), 401


app = Flask(__name__)
ASSISTANT_MAX_HISTORY = 10
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
LLM_RATE_LIMIT_RPM = int(os.environ.get("LLM_RATE_LIMIT_RPM", "5"))
LLM_RATE_LIMIT_WINDOW = 60.0
LLM_REQUEST_LOG: Dict[str, List[float]] = {}
DEFAULT_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)
ASSISTANT_PROMPT_CONFIG = {
    "role": "You are an in-car assistant for a simulated vehicle.",
    "goals": [
        "Help passengers reach their destination safely.",
        "Keep passengers comfortable by adjusting available controls.",
        "Offer clear, concise guidance with a calm tone.",
    ],
    "safety_rules": [
        "Be safety-first: discourage unsafe driving behavior.",
        "Prefer safer settings when making tradeoffs.",
    ],
    "interaction_rules": [
        "Only use the controls listed below.",
        "If a request is outside the list, explain what is available.",
        "When a request is ambiguous, apply the change that is most likely intended. Please make your best judgement in situations such as turning off seat heating when seat cooling is being turned on",
        "Respond with JSON only using the specified schema.",
    ],
    "output_schema": '{ "reply": "...", "updates": { ... } }',
    "output_notes": [
        "The updates object must use control paths (dot-delimited) as keys.",
        "If no updates are needed, return an empty updates object.",
    ],
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_controls() -> Dict[str, Any]:
    data = read_json(CONTROLS_PATH, {"controls": []})
    if not isinstance(data, dict) or "controls" not in data:
        return {"controls": []}
    return data


def refresh_controls() -> None:
    global CONTROLS, CONTROL_MAP
    CONTROLS = load_controls()
    CONTROL_MAP = build_control_map(CONTROLS)


def merge_state(default: Any, current: Any) -> Any:
    if isinstance(default, dict):
        if not isinstance(current, dict):
            return deepcopy(default)
        merged: Dict[str, Any] = {}
        for key, default_value in default.items():
            if key in current:
                merged[key] = merge_state(default_value, current[key])
            else:
                merged[key] = deepcopy(default_value)
        for key, value in current.items():
            if key not in merged:
                merged[key] = value
        return merged
    if current is None:
        return deepcopy(default)
    return current


def load_state() -> Dict[str, Any]:
    data = read_json(STATE_PATH, DEFAULT_STATE)
    if not isinstance(data, dict):
        data = {}
    merged = merge_state(DEFAULT_STATE, data)
    if merged != data:
        write_json(STATE_PATH, merged)
    return merged


def build_control_map(controls: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for control in controls.get("controls", []):
        path = control.get("path")
        if isinstance(path, str):
            mapping[path] = control
    return mapping


def get_google_api_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY")


def get_client_id() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(client_id: str) -> Tuple[bool, int]:
    if LLM_RATE_LIMIT_RPM <= 0:
        return True, 0
    now = time.time()
    window_start = now - LLM_RATE_LIMIT_WINDOW
    timestamps = [ts for ts in LLM_REQUEST_LOG.get(client_id, []) if ts >= window_start]
    if len(timestamps) >= LLM_RATE_LIMIT_RPM:
        retry_after = int(math.ceil(LLM_RATE_LIMIT_WINDOW - (now - min(timestamps))))
        LLM_REQUEST_LOG[client_id] = timestamps
        return False, max(retry_after, 1)
    timestamps.append(now)
    LLM_REQUEST_LOG[client_id] = timestamps
    return True, 0


def summarize_controls(controls: Dict[str, Any]) -> List[Dict[str, Any]]:
    summarized: List[Dict[str, Any]] = []
    for control in controls.get("controls", []):
        summarized.append(
            {
                "id": control.get("id"),
                "label": control.get("label"),
                "group": control.get("group"),
                "module": control.get("module"),
                "path": control.get("path"),
                "type": control.get("type"),
                "value_type": control.get("value_type"),
                "values": control.get("values"),
                "min": control.get("min"),
                "max": control.get("max"),
                "step": control.get("step"),
                "units": control.get("units"),
                "maps_to": control.get("maps_to"),
                "conversion": control.get("conversion"),
                "visible_when": control.get("visible_when"),
                "description": control.get("description"),
            }
        )
    return summarized


def build_assistant_system_prompt(
    controls: Dict[str, Any], state: Dict[str, Any]
) -> str:
    control_summary = summarize_controls(controls)
    config = ASSISTANT_PROMPT_CONFIG
    sections: List[str] = []
    sections.append(config.get("role", "You are an assistant."))
    goals = config.get("goals", [])
    if goals:
        sections.append("Goals:\n" + "\n".join(f"- {goal}" for goal in goals))
    safety = config.get("safety_rules", [])
    if safety:
        sections.append("Safety:\n" + "\n".join(f"- {rule}" for rule in safety))
    rules = config.get("interaction_rules", [])
    if rules:
        sections.append("Rules:\n" + "\n".join(f"- {rule}" for rule in rules))
    output_schema = config.get("output_schema")
    if output_schema:
        sections.append(f"Output schema:\n{output_schema}")
    output_notes = config.get("output_notes", [])
    if output_notes:
        sections.append(
            "Output notes:\n" + "\n".join(f"- {note}" for note in output_notes)
        )
    sections.append(f"Controls:\n{json.dumps(control_summary, indent=2)}")
    sections.append(f"Current state:\n{json.dumps(state, indent=2)}")
    return "\n\n".join(sections)


def normalize_history(history: Any) -> List[Dict[str, str]]:
    if not isinstance(history, list):
        return []
    normalized: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        normalized.append({"role": role, "content": content.strip()})
    return normalized[-ASSISTANT_MAX_HISTORY :]


def build_google_contents(
    history: List[Dict[str, str]], message: str
) -> List[Dict[str, Any]]:
    contents: List[Dict[str, Any]] = []
    for item in history:
        role = "model" if item["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": item["content"]}]})
    if message:
        if not history or history[-1]["role"] != "user" or history[-1]["content"] != message:
            contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def build_azure_messages(
    history: List[Dict[str, str]], message: str, system_prompt: str
) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        if item["role"] == "assistant":
            messages.append({"role": "assistant", "content": item["content"]})
        else:
            messages.append({"role": "user", "content": item["content"]})
    if message:
        if not history or history[-1]["role"] != "user" or history[-1]["content"] != message:
            messages.append({"role": "user", "content": message})
    return messages


def call_google_gemini(
    message: str,
    history: List[Dict[str, str]],
    system_prompt: str,
    api_key_override: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    api_key = api_key_override or get_google_api_key()
    if not api_key:
        return (
            None,
            "Missing API key. Set GEMINI_API_KEY in your environment.",
        )

    endpoint = os.environ.get("GEMINI_API_ENDPOINT", DEFAULT_GEMINI_ENDPOINT)
    url = f"{endpoint}/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": build_google_contents(history, message),
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        return None, f"Gemini request failed: {exc}"
    if response.status_code >= 400:
        return None, f"Gemini error ({response.status_code}): {response.text}"

    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"], None
    except (KeyError, IndexError, TypeError):
        return None, "Gemini response was missing text content."


def call_azure_openai(
    message: str,
    history: List[Dict[str, str]],
    system_prompt: str,
    api_key_override: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    api_key = api_key_override or os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    if not api_key:
        return None, "Missing AZURE_OPENAI_API_KEY environment variable."
    if not endpoint or not deployment:
        return (
            None,
            "Missing Azure settings. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT.",
        )

    try:
        from openai import AzureOpenAI
    except ImportError as exc:
        return None, f"openai is not installed: {exc}"

    client = AzureOpenAI(
        api_version=api_version,
        azure_endpoint=endpoint,
        api_key=api_key,
    )
    messages = build_azure_messages(history, message, system_prompt)
    try:
        response = client.chat.completions.create(
            messages=messages,
            max_completion_tokens=1024,
            model=deployment,
        )
    except Exception as exc:  # pragma: no cover - network bound
        return None, f"Azure request failed: {exc}"
    try:
        return response.choices[0].message.content, None
    except (AttributeError, IndexError):
        return None, "Azure response was missing text content."


def parse_model_json(text: str) -> Any:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                return None
    return None


def normalize_value(control: Dict[str, Any], value: Any) -> Any:
    value_type = control.get("value_type", "str")
    control_type = control.get("type", "input")

    if value_type == "bool":
        if isinstance(value, bool):
            coerced = value
        elif isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "on", "yes"}:
                coerced = True
            elif lowered in {"false", "0", "off", "no"}:
                coerced = False
            else:
                return None
        elif isinstance(value, (int, float)):
            coerced = bool(value)
        else:
            return None
    elif value_type == "int":
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return None
    elif value_type == "float":
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return None
    else:
        if isinstance(value, (dict, list)):
            return None
        coerced = str(value)

    if control_type == "select":
        values = control.get("values", [])
        if coerced not in values:
            return None

    if control_type == "slider":
        min_value = control.get("min")
        max_value = control.get("max")
        step = control.get("step") or 1
        if isinstance(coerced, (int, float)):
            if min_value is not None:
                coerced = max(min_value, coerced)
            if max_value is not None:
                coerced = min(max_value, coerced)
            if min_value is None:
                min_value = 0
            steps = round((coerced - min_value) / step)
            coerced = min_value + steps * step
            if value_type == "int":
                coerced = int(round(coerced))

    return coerced


def apply_conversion(control: Dict[str, Any], value: Any) -> Any:
    conversion = control.get("conversion")
    if conversion == "f_to_c":
        return (value - 32) * 5 / 9
    if conversion == "mph_to_kph":
        return value * 1.60934
    return value


def flatten_update(payload: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            updates.update(flatten_update(value, path))
        else:
            updates[path] = value
    return updates


def set_in_state(state: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: Dict[str, Any] = state
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def delete_in_state(state: Dict[str, Any], path: str) -> bool:
    parts = path.split(".")
    current: Dict[str, Any] = state
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            return False
        current = current[part]
    if parts[-1] in current:
        del current[parts[-1]]
        return True
    return False


def prune_mapped_state_entries(
    state: Dict[str, Any], controls: Dict[str, Any]
) -> bool:
    changed = False
    for control in controls.get("controls", []):
        path = control.get("path")
        maps_to = control.get("maps_to")
        if path and maps_to:
            changed = delete_in_state(state, path) or changed
    return changed


def apply_update(state: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    updates = flatten_update(update)
    for path, value in updates.items():
        control = CONTROL_MAP.get(path)
        if not control:
            continue
        normalized = normalize_value(control, value)
        if normalized is None:
            continue
        converted = apply_conversion(control, normalized)
        if control.get("value_type") == "int" and isinstance(converted, float):
            converted = int(round(converted))
        target_path = control.get("maps_to", path)
        set_in_state(state, target_path, converted)
    prune_mapped_state_entries(state, CONTROLS)
    write_json(STATE_PATH, state)
    return state


CONTROLS = load_controls()
CONTROL_MAP = build_control_map(CONTROLS)


def build_default_telemetry_state() -> Dict[str, float]:
    return {
        "last_ts": time.time(),
        "trip_km": 12.4,
        "odometer_km": 18420.7,
        "fuel_level_pct": 72.0,
    }


def reset_telemetry_state() -> None:
    TELEMETRY_STATE.clear()
    TELEMETRY_STATE.update(build_default_telemetry_state())


STATE = load_state()
if prune_mapped_state_entries(STATE, CONTROLS):
    write_json(STATE_PATH, STATE)
TELEMETRY_STATE = build_default_telemetry_state()


def compute_telemetry(state: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    last_ts = TELEMETRY_STATE.get("last_ts", now)
    dt = max(0.0, now - last_ts)

    speed_kph = float(state.get("tacc", {}).get("car_speed_kph", 0))
    distance_km = speed_kph * dt / 3600.0

    TELEMETRY_STATE["trip_km"] = TELEMETRY_STATE.get("trip_km", 0.0) + distance_km
    TELEMETRY_STATE["odometer_km"] = (
        TELEMETRY_STATE.get("odometer_km", 0.0) + distance_km
    )

    fuel_level = TELEMETRY_STATE.get("fuel_level_pct", 70.0)
    fuel_level = max(0.0, fuel_level - distance_km * 0.25)
    TELEMETRY_STATE["fuel_level_pct"] = fuel_level

    outside_temp_c = 18 + 6 * math.sin(now / 900.0)
    engine_temp_c = 70 + min(speed_kph, 120) * 0.25
    range_km = fuel_level / 100.0 * 520

    TELEMETRY_STATE["last_ts"] = now

    return {
        "timestamp": now,
        "clock_time": datetime.now().strftime("%H:%M"),
        "clock_date": datetime.now().strftime("%a %b %d"),
        "outside_temp_c": round(outside_temp_c, 1),
        "engine_temp_c": round(engine_temp_c, 1),
        "range_km": round(range_km, 0),
        "fuel_level_pct": round(fuel_level, 1),
        "trip_km": round(TELEMETRY_STATE["trip_km"], 1),
        "odometer_km": round(TELEMETRY_STATE["odometer_km"], 1),
    }


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/controls")
def get_controls():
    refresh_controls()
    return jsonify(CONTROLS)


@app.get("/api/state")
def get_state():
    return jsonify(STATE)


@app.get("/api/telemetry")
def get_telemetry():
    return jsonify(compute_telemetry(STATE))


@app.post("/api/state")
def update_state():
    refresh_controls()
    if not authorize_request():
        return unauthorized_response()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    updated = apply_update(STATE, payload)
    return jsonify(updated)


@app.post("/api/reset")
def reset_state():
    global STATE
    if not authorize_request():
        return unauthorized_response()
    STATE = deepcopy(DEFAULT_STATE)
    write_json(STATE_PATH, STATE)
    reset_telemetry_state()
    return jsonify(STATE)


@app.post("/api/assistant")
def assistant():
    refresh_controls()
    if not authorize_request():
        return unauthorized_response()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400
    message = message.strip()

    history = normalize_history(payload.get("history"))

    provider = str(payload.get("provider") or "google").strip().lower()
    api_key_override = payload.get("api_key")
    if not isinstance(api_key_override, str) or not api_key_override.strip():
        api_key_override = None
    else:
        api_key_override = api_key_override.strip()

    client_id = get_client_id()
    allowed, retry_after = check_rate_limit(client_id)
    if not allowed:
        response = jsonify(
            {
                "error": "Rate limit exceeded. Try again soon.",
                "retry_after": retry_after,
            }
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    system_prompt = build_assistant_system_prompt(CONTROLS, STATE)
    if provider == "google":
        response_text, error = call_google_gemini(
            message, history, system_prompt, api_key_override
        )
    elif provider == "azure":
        response_text, error = call_azure_openai(
            message, history, system_prompt, api_key_override
        )
    else:
        return jsonify({"error": f"Unknown provider: {provider}"}), 400

    if error:
        return jsonify({"error": error}), 502

    parsed = parse_model_json(response_text or "")
    if not isinstance(parsed, dict):
        return jsonify({"error": "Model response was not valid JSON."}), 502

    reply = parsed.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = "I can help with driving, comfort, or infotainment settings. What would you like to adjust?"

    updates = parsed.get("updates")
    if not isinstance(updates, dict):
        updates = {}

    if updates:
        updated_state = apply_update(STATE, updates)
    else:
        updated_state = STATE

    return jsonify(
        {
            "reply": reply.strip(),
            "updates": updates,
            "state": updated_state,
            "provider": provider,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
