let controlsList = [];
let controlMap = {};
let currentState = {};
let controlsRegistry = { schema_version: 2, controls: [] };

const connectionStatus = document.getElementById("connection-status");

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
  renderRegistry();
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
  } catch (error) {
    setConnectionState(false);
  }
}

loadData();
