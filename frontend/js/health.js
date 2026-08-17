const HealthPage = (() => {
  let root = null;
  let refreshTimer = null;

  function render(container) {
    root = container;

    root.innerHTML = `
      <div class="health-page">

        <div class="dashboard-toolbar">

          <div>
            <div class="section-eyebrow">
              JETSON ORIN SYSTEM TELEMETRY
            </div>

            <div class="section-description">
              Live resource utilization and DeepStream pipeline status.
            </div>
          </div>

          <div class="health-toolbar-right">
            <span
              id="health-last-update"
              class="health-last-update"
            >
              Waiting for telemetry...
            </span>

            <button
              id="health-refresh-btn"
              class="page-action-btn"
            >
              ↻ Refresh
            </button>
          </div>

        </div>

        <div class="health-overview-card">

          <div class="health-overview-main">

            <div
              id="health-system-indicator"
              class="health-system-indicator"
            >
              <span class="health-system-pulse"></span>
            </div>

            <div>
              <div class="health-overview-label">
                SYSTEM STATUS
              </div>

              <div
                id="health-system-status"
                class="health-overview-status"
              >
                Loading...
              </div>

              <div class="health-overview-subtitle">
                NVIDIA Jetson Orin · DeepStream PPE Platform
              </div>
            </div>

          </div>

          <div class="health-overview-meta">

            <div>
              <span>Active Streams</span>
              <strong id="health-active-streams">0 / 2</strong>
            </div>

            <div>
              <span>System Uptime</span>
              <strong id="health-uptime">—</strong>
            </div>

          </div>

        </div>

        <div class="health-metric-grid">

          ${metricCard(
            "⚙️",
            "CPU Usage",
            "health-cpu",
            "health-cpu-bar",
            "%"
          )}

          ${metricCard(
            "🎮",
            "GPU Usage",
            "health-gpu",
            "health-gpu-bar",
            "%"
          )}

          ${metricCard(
            "🧠",
            "Memory Usage",
            "health-memory",
            "health-memory-bar",
            "%"
          )}

          ${metricCard(
            "💾",
            "Disk Usage",
            "health-disk",
            "health-disk-bar",
            "%"
          )}

        </div>

        <div class="health-detail-grid">

          <section class="page-card">

            <div class="page-card-header">
              <div>
                <div class="page-card-title">
                  Hardware Details
                </div>

                <div class="page-card-subtitle">
                  Current Jetson system information
                </div>
              </div>

              <span class="status-badge success">
                ● EDGE DEVICE
              </span>
            </div>

            <div class="health-info-list">

              ${infoRow(
                "CPU Cores",
                "health-cpu-count",
                "—"
              )}

              ${infoRow(
                "Temperature",
                "health-temperature",
                "—"
              )}

              ${infoRow(
                "Memory",
                "health-memory-detail",
                "—"
              )}

              ${infoRow(
                "Storage",
                "health-disk-detail",
                "—"
              )}

              ${infoRow(
                "Inference Runtime",
                null,
                "TensorRT FP16"
              )}

              ${infoRow(
                "Video Analytics",
                null,
                "NVIDIA DeepStream"
              )}

            </div>

          </section>

          <section class="page-card">

            <div class="page-card-header">
              <div>
                <div class="page-card-title">
                  Stream Pipelines
                </div>

                <div class="page-card-subtitle">
                  Independent camera pipeline health
                </div>
              </div>
            </div>

            <div id="health-stream-list">
              ${renderStreamPlaceholder()}
            </div>

          </section>

        </div>

      </div>
    `;

    document.getElementById("health-refresh-btn").onclick =
      loadHealth;

    loadHealth();

    clearInterval(refreshTimer);
    refreshTimer = setInterval(loadHealth, 2000);
  }

  function metricCard(icon, label, valueId, barId, unit) {
    return `
      <div class="health-metric-card">

        <div class="health-metric-header">
          <div class="health-metric-icon">
            ${icon}
          </div>

          <span>${label}</span>
        </div>

        <div class="health-metric-reading">
          <strong id="${valueId}">—</strong>
          <span>${unit}</span>
        </div>

        <div class="health-progress-track">
          <div
            id="${barId}"
            class="health-progress-bar"
            style="width:0%;"
          ></div>
        </div>

      </div>
    `;
  }

  function infoRow(label, id, value) {
    return `
      <div class="health-info-row">
        <span>${label}</span>

        <strong ${id ? `id="${id}"` : ""}>
          ${value}
        </strong>
      </div>
    `;
  }

  function renderStreamPlaceholder() {
    return `
      <div class="page-empty-state compact">
        <div class="page-empty-icon">⌛</div>
        <strong>Loading pipeline state</strong>
        <span>Waiting for backend telemetry.</span>
      </div>
    `;
  }

  async function loadHealth() {
    if (!root || !document.body.contains(root)) {
      clearInterval(refreshTimer);
      return;
    }

    try {
      const response = await fetch("/api/health");

      if (!response.ok) {
        throw new Error(
          `Health API returned ${response.status}`
        );
      }

      const data = await response.json();

      updateHealth(data);
    } catch (error) {
      console.error("[Health] Load failed:", error);

      setText(
        "health-system-status",
        "Telemetry Unavailable"
      );

      const indicator = document.getElementById(
        "health-system-indicator"
      );

      if (indicator) {
        indicator.classList.add("danger");
      }
    }
  }

  function updateHealth(data) {
    const cpu = Number(data.cpu_percent ?? 0);
    const gpu =
      data.gpu_percent === null ||
      data.gpu_percent === undefined
        ? null
        : Number(data.gpu_percent);

    const memory = Number(
      data.memory?.percent ?? 0
    );

    const disk = Number(
      data.disk?.percent ?? 0
    );

    setText("health-cpu", cpu.toFixed(1));
    setText(
      "health-gpu",
      gpu === null ? "N/A" : gpu.toFixed(1)
    );

    setText("health-memory", memory.toFixed(1));
    setText("health-disk", disk.toFixed(1));

    setBar("health-cpu-bar", cpu);
    setBar("health-gpu-bar", gpu ?? 0);
    setBar("health-memory-bar", memory);
    setBar("health-disk-bar", disk);

    setText(
      "health-cpu-count",
      data.cpu_count ?? "—"
    );

    setText(
      "health-temperature",
      data.temperature_c === null ||
      data.temperature_c === undefined
        ? "Unavailable"
        : `${data.temperature_c} °C`
    );

    setText(
      "health-memory-detail",
      data.memory
        ? `${data.memory.used_gb} GB / ${data.memory.total_gb} GB`
        : "—"
    );

    setText(
      "health-disk-detail",
      data.disk
        ? `${data.disk.used_gb} GB / ${data.disk.total_gb} GB`
        : "—"
    );

    setText(
      "health-active-streams",
      `${data.active_streams ?? 0} / 2`
    );

    setText(
      "health-uptime",
      formatUptime(data.uptime_seconds)
    );

    setText(
      "health-last-update",
      `Updated ${new Date().toLocaleTimeString()}`
    );

    const critical =
      cpu >= 95 ||
      memory >= 95 ||
      disk >= 95 ||
      (
        data.temperature_c !== null &&
        data.temperature_c !== undefined &&
        Number(data.temperature_c) >= 85
      );

    setText(
      "health-system-status",
      critical
        ? "Attention Required"
        : "System Operational"
    );

    const indicator = document.getElementById(
      "health-system-indicator"
    );

    if (indicator) {
      indicator.classList.toggle(
        "danger",
        critical
      );
    }

    renderStreams(data.streams || {});
  }

  function renderStreams(streams) {
    const container = document.getElementById(
      "health-stream-list"
    );

    if (!container) return;

    container.innerHTML = [0, 1]
      .map(slotId => {
        const stream =
          streams[String(slotId)] || {};

        const running = Boolean(stream.running);
        const stats = stream.stats || {};

        return `
          <div class="health-stream-row">

            <div class="health-stream-left">

              <div
                class="health-stream-status ${
                  running ? "online" : ""
                }"
              ></div>

              <div>
                <strong>
                  Camera ${slotId + 1}
                </strong>

                <span>
                  ${
                    stream.device ||
                    "No camera assigned"
                  }
                </span>
              </div>

            </div>

            <div class="health-stream-stats">

              <div>
                <span>STATUS</span>
                <strong class="${
                  running
                    ? "tone-success"
                    : "tone-muted"
                }">
                  ${
                    running
                      ? "RUNNING"
                      : "STOPPED"
                  }
                </strong>
              </div>

              <div>
                <span>FPS</span>
                <strong>
                  ${stats.fps ?? 0}
                </strong>
              </div>

              <div>
                <span>PERSONS</span>
                <strong>
                  ${stats.persons ?? 0}
                </strong>
              </div>

              <div>
                <span>VIOLATIONS</span>
                <strong class="tone-danger">
                  ${stats.violations ?? 0}
                </strong>
              </div>

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

  function setBar(id, value) {
    const element = document.getElementById(id);

    if (!element) return;

    const safeValue = Math.max(
      0,
      Math.min(100, Number(value || 0))
    );

    element.style.width = `${safeValue}%`;

    element.classList.toggle(
      "warning",
      safeValue >= 75 && safeValue < 90
    );

    element.classList.toggle(
      "danger",
      safeValue >= 90
    );
  }

  function formatUptime(seconds) {
    if (
      seconds === null ||
      seconds === undefined
    ) {
      return "—";
    }

    const total = Number(seconds);
    const days = Math.floor(total / 86400);
    const hours = Math.floor(
      (total % 86400) / 3600
    );

    const minutes = Math.floor(
      (total % 3600) / 60
    );

    if (days > 0) {
      return `${days}d ${hours}h ${minutes}m`;
    }

    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }

    return `${minutes}m`;
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

window.HealthPage = HealthPage;
