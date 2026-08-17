// frontend/js/live.js
// DeepStream PPE Monitor — Live Feed Page
//
// Backend contract:
//   GET  /api/cameras
//   POST /api/stream/{slot}/start   { device: "/dev/videoX" }
//   POST /api/stream/{slot}/stop
//   GET  /api/stream/{slot}/status
//   WS   /ws/stream/{slot}
//
// Current backend supports:
//   - Slot 0
//   - Slot 1
//   - USB / V4L2 camera sources

const LivePage = (() => {
  const MAX_ALERTS = 200;

  let root = null;
  let mounted = false;
  let visible = false;
  let cameraCount = 1;
  let cameras = [];
  let alerts = [];
  let alertsOpen = true;
  let fullscreenSlot = null;

  const slots = {
    0: createSlotState(0),
    1: createSlotState(1),
  };

  function createSlotState(slot) {
    return {
      slot,
      device: "",
      running: false,
      connecting: false,
      websocket: null,
      reconnectTimer: null,
      objectUrl: null,
      persons: 0,
      violations: 0,
      fps: 0,
      error: "",
    };
  }

  // ─────────────────────────────────────────────────────────────
  // Utilities
  // ─────────────────────────────────────────────────────────────

  function apiBase() {
    return window.location.origin;
  }

  function wsBase() {
    const protocol =
      window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatTime(timestamp) {
    if (!timestamp) return "--:--:--";

    let ms = Number(timestamp);

    // Backend timestamps are expected to be seconds.
    if (ms < 1000000000000) {
      ms *= 1000;
    }

    const date = new Date(ms);

    if (Number.isNaN(date.getTime())) {
      return "--:--:--";
    }

    return date.toLocaleTimeString();
  }

  function getViolationType(type) {
    if (typeof vtype === "function") {
      return vtype(type);
    }

    return {
      label: String(type || "Violation").replaceAll("_", " "),
      color: "var(--danger)",
      bg: "var(--danger-bg)",
      border: "var(--danger-border)",
      icon: "⚠️",
    };
  }

  function activeSlots() {
    return Array.from(
      { length: cameraCount },
      (_, index) => slots[index]
    );
  }

  function totalStats() {
    return activeSlots().reduce(
      (total, slot) => {
        total.persons += Number(slot.persons || 0);
        total.violations += Number(slot.violations || 0);
        total.fps += Number(slot.fps || 0);
        return total;
      },
      {
        persons: 0,
        violations: 0,
        fps: 0,
      }
    );
  }

  function averageFps() {
    const running = activeSlots().filter(slot => slot.running);

    if (!running.length) {
      return 0;
    }

    const total = running.reduce(
      (sum, slot) => sum + Number(slot.fps || 0),
      0
    );

    return Math.round((total / running.length) * 10) / 10;
  }

  function setSlotStats(slotId, data) {
    const slot = slots[slotId];

    slot.persons = Number(data.persons ?? 0);
    slot.violations = Number(data.violations ?? 0);
    slot.fps = Number(data.fps ?? 0);
  }

  function resetSlotStats(slotId) {
    const slot = slots[slotId];

    slot.persons = 0;
    slot.violations = 0;
    slot.fps = 0;
  }

  // ─────────────────────────────────────────────────────────────
  // Camera discovery
  // ─────────────────────────────────────────────────────────────

  async function loadCameras() {
    try {
      const response = await fetch(`${apiBase()}/api/cameras`);

      if (!response.ok) {
        throw new Error(`Camera discovery failed: ${response.status}`);
      }

      const data = await response.json();
      cameras = Array.isArray(data.cameras) ? data.cameras : [];

      // Assign different default cameras to each slot.
      if (!slots[0].device && cameras[0]) {
        slots[0].device = cameras[0];
      }

      if (!slots[1].device) {
        slots[1].device = cameras[1] || cameras[0] || "";
      }
    } catch (error) {
      console.error("[LivePage] Camera discovery error:", error);
      cameras = [];
    }
  }

  async function restoreStreamStatus() {
    await Promise.all(
      [0, 1].map(async slotId => {
        try {
          const response = await fetch(
            `${apiBase()}/api/stream/${slotId}/status`
          );

          if (!response.ok) return;

          const data = await response.json();

          slots[slotId].running = Boolean(data.running);

          if (data.device) {
            slots[slotId].device = data.device;
          }

          if (slots[slotId].running) {
            connectWebSocket(slotId);
          }
        } catch (error) {
          console.warn(
            `[LivePage] Could not restore slot ${slotId}:`,
            error
          );
        }
      })
    );
  }

  // ─────────────────────────────────────────────────────────────
  // Backend stream control
  // ─────────────────────────────────────────────────────────────

  async function startStream(slotId) {
    const slot = slots[slotId];

    if (slot.connecting || slot.running) {
      return;
    }

    if (!slot.device) {
      slot.error = "Select a camera device first.";
      render();
      return;
    }

    // Prevent the same USB device being used by both pipelines.
    const otherSlotId = slotId === 0 ? 1 : 0;
    const other = slots[otherSlotId];

    if (
      cameraCount > 1 &&
      other.running &&
      other.device === slot.device
    ) {
      slot.error =
        `${slot.device} is already being used by CAM ${otherSlotId + 1}.`;
      render();
      return;
    }

    slot.connecting = true;
    slot.error = "";
    render();

    try {
      const response = await fetch(
        `${apiBase()}/api/stream/${slotId}/start`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            device: slot.device,
          }),
        }
      );

      let data = {};

      try {
        data = await response.json();
      } catch (_) {}

      if (!response.ok) {
        throw new Error(
          data.detail ||
          `Unable to start camera (${response.status})`
        );
      }

      slot.running = true;
      slot.connecting = false;

      connectWebSocket(slotId);
      render();
    } catch (error) {
      console.error(
        `[LivePage] Start slot ${slotId} failed:`,
        error
      );

      slot.running = false;
      slot.connecting = false;
      slot.error = error.message || "Unable to start camera.";

      closeWebSocket(slotId);
      resetSlotStats(slotId);
      render();
    }
  }

  async function stopStream(slotId) {
    const slot = slots[slotId];

    slot.connecting = true;
    slot.error = "";

    closeWebSocket(slotId);
    render();

    try {
      const response = await fetch(
        `${apiBase()}/api/stream/${slotId}/stop`,
        {
          method: "POST",
        }
      );

      if (!response.ok) {
        let message = `Unable to stop camera (${response.status})`;

        try {
          const data = await response.json();
          message = data.detail || message;
        } catch (_) {}

        throw new Error(message);
      }
    } catch (error) {
      console.error(
        `[LivePage] Stop slot ${slotId} failed:`,
        error
      );

      slot.error = error.message || "Unable to stop camera.";
    }

    slot.running = false;
    slot.connecting = false;

    resetSlotStats(slotId);
    clearSlotImage(slotId);

    if (fullscreenSlot === slotId) {
      closeFullscreen();
    }

    render();
  }

  // ─────────────────────────────────────────────────────────────
  // WebSocket streaming
  // ─────────────────────────────────────────────────────────────

  function connectWebSocket(slotId) {
    const slot = slots[slotId];

    closeWebSocket(slotId, false);

    const websocket = new WebSocket(
      `${wsBase()}/ws/stream/${slotId}`
    );

    websocket.binaryType = "blob";
    slot.websocket = websocket;

    websocket.onopen = () => {
      console.log(
        `[LivePage] WebSocket connected: slot ${slotId}`
      );

      slot.error = "";
      updateSlotConnectionUi(slotId);
    };

    websocket.onmessage = event => {
      if (event.data instanceof Blob) {
        updateFrame(slotId, event.data);
        return;
      }

      try {
        const data = JSON.parse(event.data);

        if (data.type === "stats") {
          setSlotStats(slotId, data);

          if (Array.isArray(data.alerts) && data.alerts.length) {
            addAlerts(slotId, data.alerts);
          }

          updateStatsUi();
          updateSlotStatsUi(slotId);
        }

        if (data.type === "error") {
          slot.error =
            data.message || "Streaming WebSocket error.";

          updateSlotConnectionUi(slotId);
        }
      } catch (error) {
        console.warn(
          `[LivePage] Invalid WebSocket message for slot ${slotId}:`,
          error
        );
      }
    };

    websocket.onerror = () => {
      console.warn(
        `[LivePage] WebSocket error: slot ${slotId}`
      );
    };

    websocket.onclose = () => {
      if (slot.websocket === websocket) {
        slot.websocket = null;
      }

      updateSlotConnectionUi(slotId);

      // Reconnect only if backend stream is still expected to run.
      if (slot.running) {
        clearTimeout(slot.reconnectTimer);

        slot.reconnectTimer = setTimeout(() => {
          if (slot.running) {
            connectWebSocket(slotId);
          }
        }, 1500);
      }
    };
  }

  function closeWebSocket(slotId, clearTimer = true) {
    const slot = slots[slotId];

    if (clearTimer) {
      clearTimeout(slot.reconnectTimer);
      slot.reconnectTimer = null;
    }

    if (slot.websocket) {
      const websocket = slot.websocket;
      slot.websocket = null;

      websocket.onclose = null;
      websocket.onerror = null;
      websocket.onmessage = null;

      try {
        websocket.close();
      } catch (_) {}
    }
  }

  function updateFrame(slotId, blob) {
    const slot = slots[slotId];

    if (slot.objectUrl) {
      URL.revokeObjectURL(slot.objectUrl);
    }

    slot.objectUrl = URL.createObjectURL(blob);

    const image = document.getElementById(
      `live-frame-${slotId}`
    );

    if (image) {
      image.src = slot.objectUrl;
      image.style.display = "block";
    }

    const placeholder = document.getElementById(
      `camera-placeholder-${slotId}`
    );

    if (placeholder) {
      placeholder.style.display = "none";
    }

    const fullscreenImage = document.getElementById(
      "fullscreen-live-image"
    );

    if (
      fullscreenImage &&
      fullscreenSlot === slotId
    ) {
      fullscreenImage.src = slot.objectUrl;
    }
  }

  function clearSlotImage(slotId) {
    const slot = slots[slotId];

    if (slot.objectUrl) {
      URL.revokeObjectURL(slot.objectUrl);
      slot.objectUrl = null;
    }

    const image = document.getElementById(
      `live-frame-${slotId}`
    );

    if (image) {
      image.removeAttribute("src");
      image.style.display = "none";
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Alerts
  // ─────────────────────────────────────────────────────────────

  function addAlerts(slotId, newAlerts) {
    const tagged = newAlerts.map(alert => ({
      ...alert,
      camera: alert.camera ?? slotId,
      _key:
        `${slotId}-${alert.timestamp}-${alert.person_id}-` +
        `${alert.violation_type}-${Math.random()}`,
    }));

    alerts = [...tagged, ...alerts].slice(0, MAX_ALERTS);

    updateAlertsUi();
    updateStatsUi();
  }

  function clearAlerts() {
    alerts = [];
    updateAlertsUi();
    updateStatsUi();
  }

  // ─────────────────────────────────────────────────────────────
  // Rendering
  // ─────────────────────────────────────────────────────────────

  function render() {
    if (!root) return;

    const totals = totalStats();

    root.innerHTML = `
      <div class="live-page">

        ${renderControlBar(totals)}

        <div class="live-main-layout">

          <div
            id="live-camera-grid"
            class="live-camera-grid live-grid-${cameraCount}"
          >
            ${activeSlots()
              .map(slot => renderCameraCard(slot))
              .join("")}
          </div>

          ${
            alertsOpen
              ? renderAlertsPanel()
              : ""
          }

        </div>

      </div>

      <div id="live-fullscreen-root"></div>
    `;

    bindEvents();

    // Restore already received frames after a re-render.
    activeSlots().forEach(slot => {
      if (!slot.objectUrl) return;

      const image = document.getElementById(
        `live-frame-${slot.slot}`
      );

      const placeholder = document.getElementById(
        `camera-placeholder-${slot.slot}`
      );

      if (image) {
        image.src = slot.objectUrl;
        image.style.display = "block";
      }

      if (placeholder) {
        placeholder.style.display = "none";
      }
    });

    if (fullscreenSlot !== null) {
      renderFullscreen(fullscreenSlot);
    }
  }

  function renderControlBar(totals) {
    return `
      <div class="live-control-bar">

        <div class="live-stream-count-control">
          <span class="live-control-label">STREAMS</span>

          <div class="live-stream-count-buttons">
            ${[1, 2]
              .map(
                count => `
                  <button
                    class="stream-count-btn ${
                      cameraCount === count ? "active" : ""
                    }"
                    data-camera-count="${count}"
                  >
                    ${count}
                  </button>
                `
              )
              .join("")}
          </div>
        </div>

        <div class="live-divider"></div>

        <div class="live-summary-cards">

          ${renderSummaryCard(
            "👤",
            "Total Persons",
            totals.persons,
            "navy"
          )}

          ${renderSummaryCard(
            "⚠️",
            "Total Violations",
            totals.violations,
            "danger"
          )}

          ${renderSummaryCard(
            "⚡",
            "Avg FPS",
            averageFps(),
            "success"
          )}

        </div>

        <div class="live-control-spacer"></div>

        ${
          alerts.length
            ? `
              <div class="live-alert-counter">
                <span>🚨</span>
                <strong>${alerts.length} ALERTS</strong>
              </div>
            `
            : ""
        }

        <button
          id="toggle-alerts-btn"
          class="live-secondary-btn ${
            alertsOpen ? "active" : ""
          }"
        >
          ${
            alertsOpen
              ? "◀ Hide Alerts"
              : "▶ Show Alerts"
          }
        </button>

      </div>
    `;
  }

  function renderSummaryCard(
    icon,
    label,
    value,
    tone
  ) {
    return `
      <div class="live-summary-card">
        <span class="live-summary-icon">${icon}</span>

        <div>
          <div
            class="live-summary-value tone-${tone}"
          >
            ${value}
          </div>

          <div class="live-summary-label">
            ${label}
          </div>
        </div>
      </div>
    `;
  }

  function renderCameraCard(slot) {
    const wsConnected =
      slot.websocket &&
      slot.websocket.readyState === WebSocket.OPEN;

    const statusText = slot.connecting
      ? "STARTING"
      : slot.running
      ? wsConnected
        ? "LIVE"
        : "CONNECTING"
      : "OFFLINE";

    const deviceOptions = cameras.length
      ? cameras
          .map(
            device => `
              <option
                value="${escapeHtml(device)}"
                ${
                  slot.device === device
                    ? "selected"
                    : ""
                }
              >
                ${escapeHtml(device)}
              </option>
            `
          )
          .join("")
      : `
          <option value="">
            No capture devices detected
          </option>
        `;

    return `
      <section
        class="camera-card ${
          slot.running ? "running" : ""
        }"
        id="camera-card-${slot.slot}"
      >

        <div class="camera-card-header">

          <div class="camera-title-group">
            <span
              class="camera-status-dot ${
                slot.running ? "online" : ""
              }"
            ></span>

            <span class="camera-title">
              CAM ${slot.slot + 1}
            </span>

            <span
              class="camera-state"
              id="camera-state-${slot.slot}"
            >
              ${statusText}
            </span>
          </div>

          <div class="camera-source-control">

            <span class="source-type-badge">
              📷 USB
            </span>

            <select
              id="camera-select-${slot.slot}"
              class="camera-device-select"
              ${
                slot.running || slot.connecting
                  ? "disabled"
                  : ""
              }
            >
              ${deviceOptions}
            </select>

          </div>

          <div class="camera-actions">

            <button
              class="camera-start-stop-btn ${
                slot.running
                  ? "stop"
                  : "start"
              }"
              data-slot="${slot.slot}"
              data-action="${
                slot.running
                  ? "stop"
                  : "start"
              }"
              ${
                slot.connecting ? "disabled" : ""
              }
            >
              ${
                slot.connecting
                  ? "… WAIT"
                  : slot.running
                  ? "■ STOP"
                  : "▶ START"
              }
            </button>

            <button
              class="camera-expand-btn"
              data-fullscreen-slot="${slot.slot}"
              title="Expand camera"
            >
              ⛶
            </button>

          </div>

        </div>

        <div class="camera-video-area">

          <img
            id="live-frame-${slot.slot}"
            class="camera-frame"
            alt="Camera ${slot.slot + 1} DeepStream feed"
          />

          <div
            id="camera-placeholder-${slot.slot}"
            class="camera-placeholder"
          >
            <div class="camera-placeholder-icon">
              📷
            </div>

            <div class="camera-placeholder-title">
              CAM ${slot.slot + 1}
            </div>

            <div class="camera-placeholder-text">
              ${
                slot.connecting
                  ? "Initializing DeepStream pipeline..."
                  : slot.running
                  ? "Waiting for annotated frames..."
                  : "Select a camera and start the stream"
              }
            </div>
          </div>

          ${
            slot.running
              ? `
                <div class="camera-overlay-live">
                  ● LIVE
                </div>

                <div
                  class="camera-overlay-fps"
                  id="camera-overlay-fps-${slot.slot}"
                >
                  ${slot.fps} FPS
                </div>

                <div
                  class="camera-overlay-persons"
                  id="camera-overlay-persons-${slot.slot}"
                >
                  👤 ${slot.persons}
                </div>

                <div
                  class="camera-overlay-violations ${
                    slot.violations > 0
                      ? ""
                      : "hidden"
                  }"
                  id="camera-overlay-violations-${slot.slot}"
                >
                  ⚠ ${slot.violations}
                  VIOLATION${
                    slot.violations === 1 ? "" : "S"
                  }
                </div>

                <span class="scan-corner top-left"></span>
                <span class="scan-corner top-right"></span>
                <span class="scan-corner bottom-left"></span>
                <span class="scan-corner bottom-right"></span>
              `
              : ""
          }

        </div>

        <div class="camera-stats-bar">

          ${renderCameraStat(
            "Persons",
            slot.persons,
            "navy",
            `camera-stat-persons-${slot.slot}`
          )}

          ${renderCameraStat(
            "Violations",
            slot.violations,
            "danger",
            `camera-stat-violations-${slot.slot}`
          )}

          ${renderCameraStat(
            "FPS",
            slot.fps,
            "success",
            `camera-stat-fps-${slot.slot}`
          )}

        </div>

        ${
          slot.error
            ? `
              <div
                class="camera-error"
                id="camera-error-${slot.slot}"
              >
                ⚠ ${escapeHtml(slot.error)}
              </div>
            `
            : ""
        }

      </section>
    `;
  }

  function renderCameraStat(
    label,
    value,
    tone,
    id
  ) {
    return `
      <div class="camera-stat">
        <div
          id="${id}"
          class="camera-stat-value tone-${tone}"
        >
          ${value}
        </div>

        <div class="camera-stat-label">
          ${label}
        </div>
      </div>
    `;
  }

  function renderAlertsPanel() {
    return `
      <aside class="live-alerts-panel">

        <div class="live-alerts-header">

          <div class="live-alerts-title">
            <span
              class="live-alerts-title-icon ${
                alerts.length ? "danger" : "safe"
              }"
            >
              ${alerts.length ? "⚠" : "✓"}
            </span>

            <span>All Camera Alerts</span>
          </div>

          <span
            id="live-alert-count"
            class="live-alert-count ${
              alerts.length ? "danger" : ""
            }"
          >
            ${alerts.length}
          </span>

        </div>

        <div
          id="live-alert-list"
          class="live-alert-list"
        >
          ${renderAlertItems()}
        </div>

        ${
          alerts.length
            ? `
              <div class="live-alerts-footer">
                <button
                  id="clear-live-alerts"
                  class="clear-alerts-btn"
                >
                  Clear displayed alerts
                </button>
              </div>
            `
            : ""
        }

      </aside>
    `;
  }

  function renderAlertItems() {
    if (!alerts.length) {
      return `
        <div class="alerts-empty-state">
          <div class="alerts-empty-icon">✓</div>
          <div>No violations detected</div>
        </div>
      `;
    }

    return alerts
      .map(alert => {
        const violation = getViolationType(
          alert.violation_type
        );

        return `
          <div
            class="live-alert-item"
            style="
              background:${violation.bg};
              border-color:${violation.border};
            "
          >

            <div class="live-alert-main">

              <div
                class="live-alert-icon"
                style="
                  border-color:${violation.border};
                "
              >
                ${violation.icon}
              </div>

              <div>

                <div
                  class="live-alert-person"
                  style="color:${violation.color};"
                >
                  CAM ${
                    Number(alert.camera ?? 0) + 1
                  } · ID:${escapeHtml(
                    alert.person_id ?? "?"
                  )}
                </div>

                <div class="live-alert-type">
                  ${escapeHtml(violation.label)}
                </div>

              </div>

            </div>

            <div class="live-alert-time">
              ${formatTime(alert.timestamp)}
            </div>

          </div>
        `;
      })
      .join("");
  }

  // ─────────────────────────────────────────────────────────────
  // Incremental UI updates
  // ─────────────────────────────────────────────────────────────

  function updateStatsUi() {
    if (!visible) return;

    const totals = totalStats();

    const summaryValues =
      root?.querySelectorAll(".live-summary-value");

    if (
      summaryValues &&
      summaryValues.length >= 3
    ) {
      summaryValues[0].textContent = totals.persons;
      summaryValues[1].textContent =
        totals.violations;
      summaryValues[2].textContent =
        averageFps();
    }
  }

  function updateSlotStatsUi(slotId) {
    if (!visible) return;

    const slot = slots[slotId];

    setText(
      `camera-stat-persons-${slotId}`,
      slot.persons
    );

    setText(
      `camera-stat-violations-${slotId}`,
      slot.violations
    );

    setText(
      `camera-stat-fps-${slotId}`,
      slot.fps
    );

    setText(
      `camera-overlay-fps-${slotId}`,
      `${slot.fps} FPS`
    );

    setText(
      `camera-overlay-persons-${slotId}`,
      `👤 ${slot.persons}`
    );

    const violationOverlay =
      document.getElementById(
        `camera-overlay-violations-${slotId}`
      );

    if (violationOverlay) {
      violationOverlay.textContent =
        `⚠ ${slot.violations} ` +
        `VIOLATION${
          slot.violations === 1 ? "" : "S"
        }`;

      violationOverlay.classList.toggle(
        "hidden",
        slot.violations <= 0
      );
    }

    updateFullscreenStats();
  }

  function updateSlotConnectionUi(slotId) {
    if (!visible) return;

    const slot = slots[slotId];
    const state =
      document.getElementById(
        `camera-state-${slotId}`
      );

    if (!state) return;

    const connected =
      slot.websocket &&
      slot.websocket.readyState === WebSocket.OPEN;

    state.textContent = !slot.running
      ? "OFFLINE"
      : connected
      ? "LIVE"
      : "CONNECTING";
  }

  function updateAlertsUi() {
    if (!visible || !alertsOpen) return;

    const list =
      document.getElementById("live-alert-list");

    const count =
      document.getElementById("live-alert-count");

    if (list) {
      list.innerHTML = renderAlertItems();
    }

    if (count) {
      count.textContent = alerts.length;
      count.classList.toggle(
        "danger",
        alerts.length > 0
      );
    }

    updateFullscreenAlerts();
  }

  function setText(id, value) {
    const element = document.getElementById(id);

    if (element) {
      element.textContent = value;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Fullscreen
  // ─────────────────────────────────────────────────────────────

  function openFullscreen(slotId) {
    fullscreenSlot = slotId;
    renderFullscreen(slotId);
  }

  function closeFullscreen() {
    fullscreenSlot = null;

    const fullscreenRoot =
      document.getElementById(
        "live-fullscreen-root"
      );

    if (fullscreenRoot) {
      fullscreenRoot.innerHTML = "";
    }
  }

  function renderFullscreen(slotId) {
    const fullscreenRoot =
      document.getElementById(
        "live-fullscreen-root"
      );

    if (!fullscreenRoot) return;

    const slot = slots[slotId];

    fullscreenRoot.innerHTML = `
      <div class="live-fullscreen-overlay">

        <div class="fullscreen-topbar">

          <div class="fullscreen-camera-title">

            <span
              class="camera-status-dot ${
                slot.running ? "online" : ""
              }"
            ></span>

            CAM ${slotId + 1} — EXPANDED VIEW

          </div>

          <div class="fullscreen-stat-group">

            <div class="fullscreen-stat">
              <strong
                id="fullscreen-persons"
                class="tone-info"
              >
                ${slot.persons}
              </strong>
              <span>Persons</span>
            </div>

            <div class="fullscreen-stat">
              <strong
                id="fullscreen-violations"
                class="tone-danger"
              >
                ${slot.violations}
              </strong>
              <span>Violations</span>
            </div>

            <div class="fullscreen-stat">
              <strong
                id="fullscreen-fps"
                class="tone-success"
              >
                ${slot.fps}
              </strong>
              <span>FPS</span>
            </div>

          </div>

          <div class="fullscreen-spacer"></div>

          <span class="fullscreen-hint">
            ESC to close
          </span>

          <button
            id="close-fullscreen-btn"
            class="fullscreen-close-btn"
          >
            ✕ CLOSE
          </button>

        </div>

        <div class="fullscreen-content">

          <div class="fullscreen-video">

            ${
              slot.objectUrl
                ? `
                  <img
                    id="fullscreen-live-image"
                    src="${slot.objectUrl}"
                    alt="Expanded DeepStream camera"
                  />
                `
                : `
                  <div class="fullscreen-empty">
                    <div>📷</div>
                    <span>
                      ${
                        slot.running
                          ? "Waiting for camera frames..."
                          : "Camera is not running"
                      }
                    </span>
                  </div>
                `
            }

            ${
              slot.running
                ? `
                  <div class="fullscreen-live-badge">
                    ● LIVE
                  </div>

                  <div
                    id="fullscreen-fps-overlay"
                    class="fullscreen-fps-badge"
                  >
                    ${slot.fps} FPS
                  </div>
                `
                : ""
            }

          </div>

          <aside class="fullscreen-alert-panel">

            <div class="fullscreen-alert-header">
              🚨 Alerts — CAM ${slotId + 1}
            </div>

            <div
              id="fullscreen-alert-list"
              class="fullscreen-alert-list"
            >
              ${renderFullscreenAlertItems(slotId)}
            </div>

          </aside>

        </div>

      </div>
    `;

    document.getElementById(
      "close-fullscreen-btn"
    ).onclick = closeFullscreen;
  }

  function renderFullscreenAlertItems(slotId) {
    const slotAlerts = alerts.filter(
      alert =>
        Number(alert.camera ?? 0) === slotId
    );

    if (!slotAlerts.length) {
      return `
        <div class="fullscreen-alert-empty">
          No violations detected
        </div>
      `;
    }

    return slotAlerts
      .map(alert => {
        const violation = getViolationType(
          alert.violation_type
        );

        return `
          <div class="fullscreen-alert-item">

            <div>
              <strong
                style="color:${violation.color};"
              >
                ${violation.icon}
                Person ${escapeHtml(
                  alert.person_id ?? "?"
                )}
              </strong>

              <span>
                ${escapeHtml(violation.label)}
              </span>
            </div>

            <time>
              ${formatTime(alert.timestamp)}
            </time>

          </div>
        `;
      })
      .join("");
  }

  function updateFullscreenStats() {
    if (fullscreenSlot === null) return;

    const slot = slots[fullscreenSlot];

    setText(
      "fullscreen-persons",
      slot.persons
    );

    setText(
      "fullscreen-violations",
      slot.violations
    );

    setText(
      "fullscreen-fps",
      slot.fps
    );

    setText(
      "fullscreen-fps-overlay",
      `${slot.fps} FPS`
    );
  }

  function updateFullscreenAlerts() {
    if (fullscreenSlot === null) return;

    const list =
      document.getElementById(
        "fullscreen-alert-list"
      );

    if (list) {
      list.innerHTML =
        renderFullscreenAlertItems(
          fullscreenSlot
        );
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Events
  // ─────────────────────────────────────────────────────────────

  function bindEvents() {
    root
      .querySelectorAll("[data-camera-count]")
      .forEach(button => {
        button.onclick = () => {
          const nextCount = Number(
            button.dataset.cameraCount
          );

          if (nextCount === cameraCount) {
            return;
          }

          // If switching from 2 → 1, stop only the UI display.
          // The backend stream is intentionally not force-stopped.
          // User can return to 2-stream mode without restarting.
          cameraCount = nextCount;
          render();
        };
      });

    root
      .querySelectorAll(".camera-device-select")
      .forEach(select => {
        select.onchange = event => {
          const slotId = Number(
            event.target.id.split("-").pop()
          );

          slots[slotId].device =
            event.target.value;
          slots[slotId].error = "";
        };
      });

    root
      .querySelectorAll(
        ".camera-start-stop-btn"
      )
      .forEach(button => {
        button.onclick = () => {
          const slotId = Number(
            button.dataset.slot
          );

          if (
            button.dataset.action === "start"
          ) {
            startStream(slotId);
          } else {
            stopStream(slotId);
          }
        };
      });

    root
      .querySelectorAll(
        "[data-fullscreen-slot]"
      )
      .forEach(button => {
        button.onclick = () => {
          openFullscreen(
            Number(
              button.dataset.fullscreenSlot
            )
          );
        };
      });

    const toggleAlerts =
      document.getElementById(
        "toggle-alerts-btn"
      );

    if (toggleAlerts) {
      toggleAlerts.onclick = () => {
        alertsOpen = !alertsOpen;
        render();
      };
    }

    const clearButton =
      document.getElementById(
        "clear-live-alerts"
      );

    if (clearButton) {
      clearButton.onclick = clearAlerts;
    }
  }

  function handleEscape(event) {
    if (
      event.key === "Escape" &&
      fullscreenSlot !== null
    ) {
      closeFullscreen();
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────

  async function mount(container) {
    root = container;
    visible = true;

    if (!mounted) {
      mounted = true;

      window.addEventListener(
        "keydown",
        handleEscape
      );

      await loadCameras();
      await restoreStreamStatus();
    }

    render();
  }

  function hide() {
    visible = false;
    closeFullscreen();

    // Important:
    // Do not close camera WebSockets here.
    // app.js intentionally keeps the Live page state alive
    // when navigating to Dashboard / Violations / Health.
  }

  return {
    mount,
    hide,
  };
})();

window.LivePage = LivePage;
