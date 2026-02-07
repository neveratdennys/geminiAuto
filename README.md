# Car Dashboard Simulator

A lightweight Python + Flask dashboard simulator with explicit controls and a machine-readable registry for future LLM control. Includes a main gauge cluster and a separate infotainment display with app and game controls.

**Run It**
1. `python -m venv .venv`
2. `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`)
3. `pip install -r requirements.txt`
4. `python app.py`
5. Open `http://127.0.0.1:5000`

**LLM-Ready Controls**
- Registry file: `controls.json`
- Registry endpoint: `GET /api/controls`
- State endpoint: `GET /api/state`
- Telemetry endpoint: `GET /api/telemetry` (derived simulation values)
- Update endpoint: `POST /api/state` (JSON patch by control path)
- Units: internal state is metric; `units.system` toggles metric/imperial display and exposes F/mph input controls.

Example update:
```bash
curl -X POST http://127.0.0.1:5000/api/state \
  -H "Content-Type: application/json" \
  -d '{"ac":{"power":true},"tacc":{"car_speed_kph":110}}'
```
