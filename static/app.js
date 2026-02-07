let controlsList = [];
let controlMap = {};
let currentState = {};
let controlsRegistry = { schema_version: 2, controls: [] };
let currentTelemetry = {};
let assistantHistory = [];
const MAX_ASSISTANT_HISTORY = 10;

const connectionStatus = document.getElementById("connection-status");
const assistantMessages = document.getElementById("assistant-messages");
const assistantForm = document.getElementById("assistant-form");
const assistantInput = document.getElementById("assistant-input");
const assistantStatus = document.getElementById("assistant-status");
const assistantProvider = document.getElementById("assistant-provider");
const assistantVoiceInput = document.getElementById("assistant-voice-input");
const assistantVoiceOutput = document.getElementById("assistant-voice-output");

let speechRecognition = null;
let isListening = false;

function buildControlMap(controls) {
  const map = {};
  controls.forEach((control) => {
    map[control.path] = control;
  });
  return map;
}

function toFahrenheit(celsius) {
  return (celsius * 9) / 5 + 32;
}

function toMph(kph) {
  return kph / 1.60934;
}

function toMiles(km) {
  return km / 1.60934;
}

function formatControlValue(control, value) {
  if (!control) {
    return value;
  }
  if (control.value_type === "bool") {
    return value ? "On" : "Off";
  }
  if (control.type === "slider") {
    if (control.units) {
      return `${value} ${control.units}`;
    }
    return value;
  }
  return value;
}

function formatStatusValue(path, value) {
  const unitsSystem = currentState.units?.system || "metric";

  if (path === "units.system") {
    return unitsSystem === "imperial" ? "Imperial" : "Metric";
  }
  if (path === "ac.temperature_c") {
    if (unitsSystem === "imperial") {
      return `${Math.round(toFahrenheit(value))} F`;
    }
    return `${value} C`;
  }
  if (path === "tacc.car_speed_kph") {
    if (unitsSystem === "imperial") {
      return `${Math.round(toMph(value))} mph`;
    }
    return `${value} km/h`;
  }

  const control = controlMap[path];
  if (control?.value_type === "bool") {
    return value ? "On" : "Off";
  }
  if (control?.units) {
    return `${value} ${control.units}`;
  }
  return value;
}

function getStateValue(state, path) {
  return path.split(".").reduce((current, key) => {
    if (current === undefined || current === null) {
      return undefined;
    }
    return current[key];
  }, state);
}

function deriveValueFromState(control, state) {
  const direct = getStateValue(state, control.path);
  if (direct !== undefined) {
    return direct;
  }
  if (!control.maps_to) {
    return undefined;
  }
  const source = getStateValue(state, control.maps_to);
  if (source === undefined) {
    return undefined;
  }
  if (control.conversion === "f_to_c") {
    return Math.round(toFahrenheit(source));
  }
  if (control.conversion === "mph_to_kph") {
    return Math.round(toMph(source));
  }
  return source;
}

function buildUpdate(path, value) {
  const parts = path.split(".");
  const payload = {};
  let current = payload;
  for (let i = 0; i < parts.length; i += 1) {
    const key = parts[i];
    if (i === parts.length - 1) {
      current[key] = value;
    } else {
      current[key] = {};
      current = current[key];
    }
  }
  return payload;
}

function coerceForSend(control, value) {
  if (!control) {
    return value;
  }
  if (control.value_type === "int") {
    return parseInt(value, 10);
  }
  if (control.value_type === "float") {
    return parseFloat(value);
  }
  if (control.value_type === "bool") {
    return Boolean(value);
  }
  return value;
}

function isControlVisible(control, state) {
  if (!control.visible_when) {
    return true;
  }
  const rule = control.visible_when;
  const value = getStateValue(state, rule.path);
  if (Object.prototype.hasOwnProperty.call(rule, "equals")) {
    return value === rule.equals;
  }
  if (Array.isArray(rule.in)) {
    return rule.in.includes(value);
  }
  return true;
}

function setConnectionState(isConnected) {
  if (isConnected) {
    connectionStatus.textContent = "Connected";
    connectionStatus.style.color = "var(--accent)";
  } else {
    connectionStatus.textContent = "Offline";
    connectionStatus.style.color = "#f87171";
  }
}

function setAssistantStatus(message) {
  if (assistantStatus) {
    assistantStatus.textContent = message;
  }
}

function renderAssistantMessages() {
  if (!assistantMessages) {
    return;
  }
  assistantMessages.innerHTML = "";
  assistantHistory.forEach((entry) => {
    const bubble = document.createElement("div");
    bubble.className = `assistant-message ${entry.role}`;
    bubble.textContent = entry.content;
    assistantMessages.appendChild(bubble);
  });
  assistantMessages.scrollTop = assistantMessages.scrollHeight;
}

function pushAssistantMessage(role, content) {
  if (!content) {
    return;
  }
  assistantHistory.push({ role, content });
  if (assistantHistory.length > MAX_ASSISTANT_HISTORY) {
    assistantHistory = assistantHistory.slice(-MAX_ASSISTANT_HISTORY);
  }
  renderAssistantMessages();
}

function speakAssistantReply(text) {
  if (!assistantVoiceOutput?.checked) {
    return;
  }
  if (!("speechSynthesis" in window)) {
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1;
  utterance.pitch = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

async function sendAssistantMessage(message) {
  const trimmed = message.trim();
  if (!trimmed) {
    return;
  }
  pushAssistantMessage("user", trimmed);
  if (assistantInput) {
    assistantInput.value = "";
  }
  setAssistantStatus("Thinking...");
  try {
    const response = await fetch("/api/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: trimmed,
        history: assistantHistory,
        provider: assistantProvider?.value || "google",
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Assistant error.");
    }
    pushAssistantMessage("assistant", data.reply || "Ready for the next request.");
    speakAssistantReply(data.reply || "Ready for the next request.");
    if (data.state) {
      currentState = data.state;
      renderAll();
      loadTelemetry();
    }
    setAssistantStatus("");
  } catch (error) {
    pushAssistantMessage(
      "assistant",
      `Sorry, I couldn't reach the assistant. ${error.message}`
    );
    speakAssistantReply(
      `Sorry, I couldn't reach the assistant. ${error.message}`
    );
    setAssistantStatus("Assistant unavailable.");
  }
}

function setupVoiceControls() {
  if (!assistantVoiceInput) {
    return;
  }
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    assistantVoiceInput.disabled = true;
    assistantVoiceInput.textContent = "Voice unavailable";
    return;
  }

  speechRecognition = new SpeechRecognition();
  speechRecognition.lang = "en-US";
  speechRecognition.interimResults = false;
  speechRecognition.maxAlternatives = 1;

  speechRecognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript;
    if (transcript) {
      sendAssistantMessage(transcript);
    }
  };

  speechRecognition.onerror = (event) => {
    setAssistantStatus(`Voice input error: ${event.error || "unknown"}.`);
  };

  speechRecognition.onend = () => {
    isListening = false;
    assistantVoiceInput.classList.remove("listening");
    assistantVoiceInput.textContent = "Start voice";
  };

  assistantVoiceInput.addEventListener("click", () => {
    if (!speechRecognition) {
      return;
    }
    if (isListening) {
      speechRecognition.stop();
      return;
    }
    try {
      speechRecognition.start();
      isListening = true;
      assistantVoiceInput.classList.add("listening");
      assistantVoiceInput.textContent = "Listening...";
      setAssistantStatus("Listening...");
    } catch (error) {
      setAssistantStatus("Voice input failed to start.");
    }
  });
}

function initAssistant() {
  if (!assistantMessages || !assistantForm || !assistantInput) {
    return;
  }
  assistantHistory = [
    {
      role: "assistant",
      content:
        "Hi! I can set the cabin, cruise, wipers, and infotainment. Tell me how you'd like the car configured.",
    },
  ];
  renderAssistantMessages();
  assistantForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendAssistantMessage(assistantInput.value || "");
  });
  setupVoiceControls();
}

async function sendUpdate(control, rawValue) {
  const payload = buildUpdate(control.path, coerceForSend(control, rawValue));
  try {
    const response = await fetch("/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    currentState = data;
    renderAll();
    loadTelemetry();
    setConnectionState(true);
  } catch (error) {
    setConnectionState(false);
  }
}

function createControl(control, state) {
  const wrapper = document.createElement("div");
  wrapper.className = "control";

  const label = document.createElement("label");
  label.textContent = control.label;

  const description = document.createElement("div");
  description.className = "description";
  description.textContent = `${control.description} Path: ${control.path}`;

  const value = deriveValueFromState(control, state);
  let input = null;
  let valueDisplay = null;

  if (control.type === "toggle") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    input.addEventListener("change", () => {
      sendUpdate(control, input.checked);
    });
  } else if (control.type === "slider") {
    input = document.createElement("input");
    input.type = "range";
    input.min = control.min;
    input.max = control.max;
    input.step = control.step || 1;
    input.value = value ?? control.min;

    valueDisplay = document.createElement("div");
    valueDisplay.className = "value";
    valueDisplay.textContent = formatControlValue(control, input.value);

    input.addEventListener("input", () => {
      valueDisplay.textContent = formatControlValue(control, input.value);
    });
    input.addEventListener("change", () => {
      sendUpdate(control, input.value);
    });
  } else if (control.type === "select") {
    input = document.createElement("select");
    control.values.forEach((optionValue) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionValue;
      input.appendChild(option);
    });
    input.value = value ?? control.values[0];
    input.addEventListener("change", () => {
      sendUpdate(control, input.value);
    });
  }

  wrapper.appendChild(label);
  if (input) {
    wrapper.appendChild(input);
  }
  if (valueDisplay) {
    wrapper.appendChild(valueDisplay);
  }
  wrapper.appendChild(description);

  return wrapper;
}

function renderControls(controls, state, moduleName, containerId) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  const filtered = controls.filter((control) => {
    const moduleNameForControl = control.module || "driving";
    return (
      moduleNameForControl === moduleName && isControlVisible(control, state)
    );
  });

  if (filtered.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "panel-note";
    emptyState.textContent =
      "No controls available. Check /api/controls or restart the server.";
    container.appendChild(emptyState);
    return;
  }

  const groups = filtered.reduce((acc, control) => {
    const group = control.group || "Other";
    if (!acc[group]) {
      acc[group] = [];
    }
    acc[group].push(control);
    return acc;
  }, {});

  Object.keys(groups).forEach((groupName) => {
    const groupEl = document.createElement("div");
    groupEl.className = "control-group";

    const groupTitle = document.createElement("h3");
    groupTitle.textContent = groupName;
    groupEl.appendChild(groupTitle);

    groups[groupName].forEach((control) => {
      groupEl.appendChild(createControl(control, state));
    });

    container.appendChild(groupEl);
  });
}

function renderStatus(state) {
  const statusElements = document.querySelectorAll("[data-status-path]");
  statusElements.forEach((el) => {
    const path = el.getAttribute("data-status-path");
    const value = getStateValue(state, path);
    el.textContent = formatStatusValue(path, value);
  });
}

function isIndicatorActive(value) {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value > 0;
  }
  if (typeof value === "string") {
    return value !== "off" && value !== "none" && value !== "";
  }
  return false;
}

function renderIndicators(state) {
  const indicatorElements = document.querySelectorAll("[data-indicator-path]");
  indicatorElements.forEach((el) => {
    const path = el.getAttribute("data-indicator-path");
    const value = getStateValue(state, path);
    const active = isIndicatorActive(value);
    el.classList.toggle("active", active);

    if (el.dataset.indicatorType === "level") {
      const max = parseInt(el.dataset.indicatorMax || "0", 10);
      const level = Math.min(max, parseInt(value || 0, 10));
      const bars = el.querySelectorAll(".indicator-bars span");
      bars.forEach((bar, index) => {
        bar.classList.toggle("active", index < level);
      });
    }
  });
}

function setGaugeValue(id, percent) {
  const ring = document.querySelector(`[data-gauge="${id}"]`);
  if (!ring) {
    return;
  }
  const radius = ring.r.baseVal.value;
  const circumference = 2 * Math.PI * radius;
  ring.style.strokeDasharray = `${circumference}`;
  ring.style.strokeDashoffset = `${circumference * (1 - percent)}`;
}

function renderSpeedGauge(state) {
  const unitsSystem = state.units?.system || "metric";
  const speedKph = getStateValue(state, "tacc.car_speed_kph") || 0;
  const speed = unitsSystem === "imperial" ? Math.round(toMph(speedKph)) : Math.round(speedKph);
  const maxSpeed = unitsSystem === "imperial" ? 120 : 200;
  const percent = Math.min(speed / maxSpeed, 1);

  setGaugeValue("speed", percent);

  const speedValue = document.getElementById("speed-value");
  const speedUnit = document.getElementById("speed-unit");
  if (speedValue) {
    speedValue.textContent = speed;
  }
  if (speedUnit) {
    speedUnit.textContent = unitsSystem === "imperial" ? "mph" : "km/h";
  }
}

function formatTelemetryValue(key, telemetry, unitsSystem) {
  if (!telemetry) {
    return "--";
  }
  if (key === "clock_time") {
    return telemetry.clock_time || "--:--";
  }
  if (key === "clock_date") {
    return telemetry.clock_date || "--";
  }
  if (key === "outside_temp") {
    const tempC = telemetry.outside_temp_c ?? 0;
    if (unitsSystem === "imperial") {
      return `${Math.round(toFahrenheit(tempC))} F`;
    }
    return `${Math.round(tempC)} C`;
  }
  if (key === "engine_temp") {
    const tempC = telemetry.engine_temp_c ?? 0;
    if (unitsSystem === "imperial") {
      return `${Math.round(toFahrenheit(tempC))} F`;
    }
    return `${Math.round(tempC)} C`;
  }
  if (key === "range") {
    const rangeKm = telemetry.range_km ?? 0;
    if (unitsSystem === "imperial") {
      return `${Math.round(toMiles(rangeKm))} mi`;
    }
    return `${Math.round(rangeKm)} km`;
  }
  if (key === "trip") {
    const tripKm = telemetry.trip_km ?? 0;
    if (unitsSystem === "imperial") {
      return `${toMiles(tripKm).toFixed(1)} mi`;
    }
    return `${tripKm.toFixed(1)} km`;
  }
  if (key === "odometer") {
    const odoKm = telemetry.odometer_km ?? 0;
    if (unitsSystem === "imperial") {
      return `${Math.round(toMiles(odoKm))} mi`;
    }
    return `${Math.round(odoKm)} km`;
  }
  if (key === "fuel") {
    const fuel = telemetry.fuel_level_pct ?? 0;
    return `${fuel.toFixed(1)}%`;
  }
  return telemetry[key] ?? "--";
}

function renderTelemetry() {
  const unitsSystem = currentState.units?.system || "metric";
  const elements = document.querySelectorAll("[data-telemetry-key]");
  elements.forEach((el) => {
    const key = el.getAttribute("data-telemetry-key");
    el.textContent = formatTelemetryValue(key, currentTelemetry, unitsSystem);
  });
}

function renderAppGrid(state) {
  const container = document.getElementById("app-grid");
  if (!container) {
    return;
  }
  const activeAppControl = controlsList.find(
    (control) => control.path === "infotainment.active_app"
  );
  const apps = activeAppControl?.values || [];
  container.innerHTML = "";

  apps.forEach((appName) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "app-tile";
    tile.textContent = appName;
    if (appName === state.infotainment?.active_app) {
      tile.classList.add("active");
    }
    tile.addEventListener("click", () => {
      if (activeAppControl) {
        sendUpdate(activeAppControl, appName);
      }
    });
    container.appendChild(tile);
  });
}

function renderNowPlaying(state) {
  const titleEl = document.getElementById("now-title");
  const subtitleEl = document.getElementById("now-subtitle");
  const artEl = document.getElementById("now-art");
  if (!titleEl || !subtitleEl || !artEl) {
    return;
  }
  const activeApp = state.infotainment?.active_app || "Radio";
  const radioBand = state.infotainment?.radio_band || "FM";
  const localGame = state.infotainment?.local_game || "Local Game";

  const mapping = {
    Radio: { title: `${radioBand} 101.5`, subtitle: "Synthwave Drive" },
    Bluetooth: { title: "Driver Phone", subtitle: "Bluetooth Audio" },
    Screencast: { title: "Phone Mirror", subtitle: "Screen sharing live" },
    YouTube: { title: "Highway Review", subtitle: "Recommended for you" },
    Netflix: { title: "Night Drive", subtitle: "Episode 3" },
    Twitch: { title: "Sim Racing Live", subtitle: "Top streamer" },
    "Game Streaming": { title: "Cloud Session", subtitle: "Launching game" },
    "Local Games": { title: localGame, subtitle: "Installed on system" },
    Navigation: { title: "Route Preview", subtitle: "12 min to destination" },
    Podcasts: { title: "Auto Talk", subtitle: "Episode 42" },
    Weather: { title: "Forecast", subtitle: "Light rain expected" },
    "Other Apps": { title: "App Launcher", subtitle: "Browse apps" },
  };

  const selection = mapping[activeApp] || { title: activeApp, subtitle: "Ready" };
  titleEl.textContent = selection.title;
  subtitleEl.textContent = selection.subtitle;

  const artPalette = {
    Radio: "linear-gradient(135deg, #f59e0b, #f97316)",
    Bluetooth: "linear-gradient(135deg, #22d3ee, #0ea5e9)",
    Screencast: "linear-gradient(135deg, #c084fc, #6366f1)",
    YouTube: "linear-gradient(135deg, #ef4444, #f97316)",
    Netflix: "linear-gradient(135deg, #111827, #ef4444)",
    Twitch: "linear-gradient(135deg, #a855f7, #7c3aed)",
    "Game Streaming": "linear-gradient(135deg, #10b981, #22d3ee)",
    "Local Games": "linear-gradient(135deg, #f97316, #84cc16)",
    Navigation: "linear-gradient(135deg, #38bdf8, #22d3ee)",
    Podcasts: "linear-gradient(135deg, #fb7185, #f43f5e)",
    Weather: "linear-gradient(135deg, #60a5fa, #38bdf8)",
    "Other Apps": "linear-gradient(135deg, #94a3b8, #64748b)",
  };

  artEl.style.background = artPalette[activeApp] || artPalette["Other Apps"];
}

function renderRegistry() {
  const registry = document.getElementById("controls-registry");
  registry.textContent = JSON.stringify(controlsRegistry, null, 2);
}

function renderAll() {
  renderControls(controlsList, currentState, "driving", "controls-driving");
  renderControls(
    controlsList,
    currentState,
    "infotainment",
    "controls-entertainment"
  );
  renderStatus(currentState);
  renderIndicators(currentState);
  renderSpeedGauge(currentState);
  renderAppGrid(currentState);
  renderNowPlaying(currentState);
  renderTelemetry();
  renderRegistry();
}

async function loadTelemetry() {
  try {
    const response = await fetch("/api/telemetry");
    currentTelemetry = await response.json();
    renderTelemetry();
  } catch (error) {
    // Ignore telemetry errors for now.
  }
}

async function loadData() {
  try {
    const [controlsResponse, stateResponse] = await Promise.all([
      fetch("/api/controls"),
      fetch("/api/state"),
    ]);

    const controlsData = await controlsResponse.json();
    const stateData = await stateResponse.json();

    controlsRegistry = controlsData;
    controlsList = controlsData.controls || [];
    controlMap = buildControlMap(controlsList);
    currentState = stateData;

    renderAll();
    setConnectionState(true);
    loadTelemetry();
    setInterval(loadTelemetry, 2000);
    initAssistant();
  } catch (error) {
    setConnectionState(false);
  }
}

loadData();
