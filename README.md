# Car Dashboard Simulator

A lightweight Python + Flask dashboard simulator with explicit controls and a machine-readable registry for future LLM control. Includes a main gauge cluster and a separate infotainment display with app and game controls.

**Run It**
1. `python -m venv .venv`
2. `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`)
3. `pip install -r requirements.txt`
4. `export GEMINI_API_KEY="YOUR_KEY"` (Windows PowerShell: `$env:GEMINI_API_KEY="YOUR_KEY"`)
5. `export GEMINI_MODEL="gemini-3-flash-preview"` (optional)
6. `python app.py`
7. Open `http://127.0.0.1:5000`

**LLM-Ready Controls**
- Registry file: `controls.json`
- Registry endpoint: `GET /api/controls`
- State endpoint: `GET /api/state`
- Telemetry endpoint: `GET /api/telemetry` (derived simulation values)
- Update endpoint: `POST /api/state` (JSON patch by control path)
- Reset endpoint: `POST /api/reset` (restore default state and telemetry)
- Units: internal state is metric; `units.system` toggles metric/imperial display and exposes F/mph input controls.

Example update (include `X-API-Key` if enabled):
```bash
curl -X POST http://127.0.0.1:5000/api/state \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"ac":{"power":true},"tacc":{"car_speed_kph":110}}'
```

**Assistant (Gemini or Azure OpenAI)**
- Gemini: set `GEMINI_API_KEY`. Optional `GEMINI_MODEL` and `GEMINI_API_ENDPOINT`.
- Azure: set `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, optional `AZURE_OPENAI_API_VERSION`.
- Endpoint: `POST /api/assistant` with `"provider": "google"` or `"provider": "azure"`.
- Voice: the UI uses the browser Web Speech API for voice input/output and may require HTTPS and mic permissions.
- Rate limit: 5 requests per minute by default (`LLM_RATE_LIMIT_RPM` to override).
- UI tokens: the assistant panel lets users store an API key locally and send it with assistant requests to override server env keys.

**Ambient UI**
- Ambient background responds to speed, weather, and temperature; AC airflow has a subtle wind overlay.

**API Access Control (Optional)**
- Set `DASHBOARD_API_KEY` to require an API key for `POST /api/state`, `POST /api/reset`, and `POST /api/assistant`.
- Send the key via `X-API-Key: YOUR_KEY` or `Authorization: Bearer YOUR_KEY`.

Example request:
```bash
curl -X POST http://127.0.0.1:5000/api/assistant \
  -H "Content-Type: application/json" \
  -d '{"message":"Make it 21 C, turn on driver seat heating, and switch to Navigation.","provider":"google"}'
```
