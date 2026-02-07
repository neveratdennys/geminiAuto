from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from datetime import datetime
import math
import time
import json
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

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

app = Flask(__name__)


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
    write_json(STATE_PATH, state)
    return state


CONTROLS = load_controls()
CONTROL_MAP = build_control_map(CONTROLS)
STATE = load_state()
TELEMETRY_STATE = {
    "last_ts": time.time(),
    "trip_km": 12.4,
    "odometer_km": 18420.7,
    "fuel_level_pct": 72.0,
}


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
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    updated = apply_update(STATE, payload)
    return jsonify(updated)


if __name__ == "__main__":
    app.run(debug=True)
