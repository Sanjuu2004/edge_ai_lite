const DashboardPage = (() => {
  let root = null;
  let refreshTimer = null;
  let alertData = [];
  let currentStats = {
    persons: 0,
    violations: 0,
    total_alerts: 0,
    fps: 0,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";

    let ms = Number(timestamp);

    if (ms < 1000000000000) {
      ms *= 1000;
    }

    const date = new Date(ms);

    if (Number.isNaN(date.getTime())) {
      return "—";
    }

    return date.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function getViolationMeta(type) {
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

  function complianceRate() {
    const persons = Number(currentStats.persons || 0);
    const violations = Number(currentStats.violations || 0);

    if (persons <= 0) {
      return 100;
    }

    return Math.max(
      0,
      Math.round(((persons - violations) / persons) * 100)
    );
  }

  function render(container) {
    root = container;

    root.innerHTML = `
      <div class="dashboard-page">

        <div class="dashboard-toolbar">
          <div>
            <div class="section-eyebrow">REAL-TIME OVERVIEW</div>
            <div class="section-description">
              Aggregated monitoring data across all active DeepStream pipelines.
            </div>
          </div>

          <button id="dashboard-refresh-btn" class="page-action-btn">
            ↻ Refresh
          </button>
        </div>

        <div class="dashboard-stat-grid">

          ${statCard(
            "👤",
            "Persons Detected",
            "dashboard-persons",
            currentStats.persons,
            "navy",
            "Current active detections"
          )}

          ${statCard(
            "⚠️",
            "Active Violations",
            "dashboard-violations",
            currentStats.violations,
            "danger",
            "Current PPE violations"
          )}

          ${statCard(
            "📣",
            "Total Alerts",
            "dashboard-alerts",
            currentStats.total_alerts,
            "gold",
            "Alerts generated this session"
          )}

          ${statCard(
            "⚡",
            "Combined FPS",
            "dashboard-fps",
            currentStats.fps,
            "success",
            "Across active camera pipelines"
          )}

        </div>

        <div class="dashboard-main-grid">

          <section class="page-card">
            <div class="page-card-header">
              <div>
                <div class="page-card-title">PPE Compliance</div>
                <div class="page-card-subtitle">
                  Current compliance estimate from active detections
                </div>
              </div>

              <div
                id="dashboard-compliance-value"
                class="compliance-large-value"
              >
                ${complianceRate()}%
              </div>
            </div>

            <div class="compliance-body">

              <div class="compliance-ring-wrap radar-sweep-wrap">
                <div
                  id="dashboard-compliance-ring"
                  class="compliance-ring"
                  style="--compliance:${complianceRate() * 3.6}deg;"
                >
                  <div class="compliance-ring-inner">
                    <strong id="dashboard-ring-value">
                      ${complianceRate()}%
                    </strong>
                    <span>COMPLIANT</span>
                  </div>
                </div>
              </div>

              <div class="compliance-breakdown">

                <div class="compliance-row">
                  <div>
                    <span class="metric-dot metric-dot-success"></span>
                    Persons Detected
                  </div>
                  <strong id="dashboard-breakdown-persons">
                    ${currentStats.persons}
                  </strong>
                </div>

                <div class="compliance-row">
                  <div>
                    <span class="metric-dot metric-dot-danger"></span>
                    Current Violations
                  </div>
                  <strong id="dashboard-breakdown-violations">
                    ${currentStats.violations}
                  </strong>
                </div>

                <div class="compliance-row">
                  <div>
                    <span class="metric-dot metric-dot-gold"></span>
                    Session Alerts
                  </div>
                  <strong id="dashboard-breakdown-alerts">
                    ${currentStats.total_alerts}
                  </strong>
                </div>

                <div class="compliance-row">
                  <div>
                    <span class="metric-dot metric-dot-navy"></span>
                    Processing FPS
                  </div>
                  <strong id="dashboard-breakdown-fps">
                    ${currentStats.fps}
                  </strong>
                </div>

              </div>

            </div>
          </section>

          <section class="page-card">
            <div class="page-card-header">
              <div>
                <div class="page-card-title">Pipeline Status</div>
                <div class="page-card-subtitle">
                  DeepStream inference architecture
                </div>
              </div>

              <span class="status-badge success">
                ● OPERATIONAL
              </span>
            </div>

            <div class="pipeline-status-list">

              ${pipelineRow(
                "📷",
                "Video Sources",
                "USB / V4L2",
                "Camera input pipelines"
              )}

              ${pipelineRow(
                "🧠",
                "Inference Engine",
                "YOLOv8",
                "NVIDIA nvinfer"
              )}

              ${pipelineRow(
                "⚙️",
                "Runtime",
                "TensorRT FP16",
                "GPU accelerated inference"
              )}

              ${pipelineRow(
                "🎞️",
                "Video Analytics",
                "DeepStream",
                "GStreamer pipeline"
              )}

              ${pipelineRow(
                "📡",
                "Event Delivery",
                "WebSocket + MQTT",
                "Real-time monitoring"
              )}

            </div>
          </section>

        </div>

        <section class="page-card dashboard-alert-section">

          <div class="page-card-header">
            <div>
              <div class="page-card-title">Recent Violations</div>
              <div class="page-card-subtitle">
                Latest PPE events across all cameras
              </div>
            </div>

            <span
              id="dashboard-alert-count-badge"
              class="count-badge"
            >
              0 EVENTS
            </span>
          </div>

          <div
            id="dashboard-recent-alerts"
            class="dashboard-alert-list"
          >
            ${emptyState(
              "✓",
              "No violations recorded",
              "New PPE events will appear here automatically."
            )}
          </div>

        </section>

      </div>
    `;

    document.getElementById("dashboard-refresh-btn").onclick =
      loadData;

    loadData();

    clearInterval(refreshTimer);
    refreshTimer = setInterval(loadData, 3000);
  }

  function statCard(icon, label, id, value, tone, detail) {
    return `
      <div class="dashboard-stat-card">
        <div class="dashboard-stat-top">
          <div class="dashboard-stat-icon tone-bg-${tone}">
            ${icon}
          </div>

          <span class="dashboard-live-indicator">
            LIVE
          </span>
        </div>

        <div id="${id}" class="dashboard-stat-value tone-${tone}">
          ${value}
        </div>

        <div class="dashboard-stat-label">
          ${label}
        </div>

        <div class="dashboard-stat-detail">
          ${detail}
        </div>
      </div>
    `;
  }

  function pipelineRow(icon, name, value, description) {
    return `
      <div class="pipeline-status-row">
        <div class="pipeline-status-icon">
          ${icon}
        </div>

        <div class="pipeline-status-content">
          <strong>${name}</strong>
          <span>${description}</span>
        </div>

        <div class="pipeline-status-value">
          ${value}
        </div>
      </div>
    `;
  }

  async function loadData() {
    if (!root || !document.body.contains(root)) {
      clearInterval(refreshTimer);
      return;
    }

    try {
      const [statsResponse, alertsResponse] = await Promise.all([
        fetch("/api/stats"),
        fetch("/api/alerts"),
      ]);

      if (!statsResponse.ok) {
        throw new Error(
          `Stats API returned ${statsResponse.status}`
        );
      }

      if (!alertsResponse.ok) {
        throw new Error(
          `Alerts API returned ${alertsResponse.status}`
        );
      }

      currentStats = await statsResponse.json();
      alertData = await alertsResponse.json();

      updateStats();
      updateAlerts();
    } catch (error) {
      console.error("[Dashboard] Refresh failed:", error);
    }
  }

  function updateStats() {
    setText("dashboard-persons", currentStats.persons ?? 0);
    setText("dashboard-violations", currentStats.violations ?? 0);
    setText("dashboard-alerts", currentStats.total_alerts ?? 0);
    setText("dashboard-fps", currentStats.fps ?? 0);

    setText(
      "dashboard-breakdown-persons",
      currentStats.persons ?? 0
    );

    setText(
      "dashboard-breakdown-violations",
      currentStats.violations ?? 0
    );

    setText(
      "dashboard-breakdown-alerts",
      currentStats.total_alerts ?? 0
    );

    setText(
      "dashboard-breakdown-fps",
      currentStats.fps ?? 0
    );

    const rate = complianceRate();

    setText("dashboard-compliance-value", `${rate}%`);
    setText("dashboard-ring-value", `${rate}%`);

    const ring = document.getElementById(
      "dashboard-compliance-ring"
    );

    if (ring) {
      ring.style.setProperty(
        "--compliance",
        `${rate * 3.6}deg`
      );
    }
  }

  function updateAlerts() {
    const list = document.getElementById(
      "dashboard-recent-alerts"
    );

    const badge = document.getElementById(
      "dashboard-alert-count-badge"
    );

    if (!list || !badge) return;

    const recent = Array.isArray(alertData)
      ? alertData.slice(0, 8)
      : [];

    badge.textContent =
      `${alertData.length} EVENT${alertData.length === 1 ? "" : "S"}`;

    if (!recent.length) {
      list.innerHTML = emptyState(
        "✓",
        "No violations recorded",
        "New PPE events will appear here automatically."
      );

      return;
    }

    list.innerHTML = recent
      .map(alert => {
        const meta = getViolationMeta(
          alert.violation_type
        );

        return `
          <div class="dashboard-alert-row">

            <div
              class="dashboard-alert-icon"
              style="
                background:${meta.bg};
                border-color:${meta.border};
              "
            >
              ${meta.icon}
            </div>

            <div class="dashboard-alert-content">
              <strong>
                ${escapeHtml(meta.label)}
              </strong>

              <span>
                Camera ${Number(alert.camera ?? 0) + 1}
                · Person ${escapeHtml(alert.person_id ?? "?")}
              </span>
            </div>

            <div class="dashboard-alert-time">
              ${formatTime(alert.timestamp)}
            </div>

          </div>
        `;
      })
      .join("");
  }

  function setText(id, value) {
    const element = document.getElementById(id);

    if (element) {
      element.textContent = value;
    }
  }

  function emptyState(icon, title, description) {
    return `
      <div class="page-empty-state">
        <div class="page-empty-icon">${icon}</div>
        <strong>${title}</strong>
        <span>${description}</span>
      </div>
    `;
  }

  function destroy() {
    clearInterval(refreshTimer);
    refreshTimer = null;
    root = null;
  }

  return {
    render,
    destroy,
  };
})();

window.DashboardPage = DashboardPage;
