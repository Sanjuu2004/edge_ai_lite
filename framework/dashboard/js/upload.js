const UploadPage = (() => {
  let root = null;
  let selectedFile = null;
  let uploading = false;
  let status = "idle"; // idle | ready | uploading | processing | done | error
  let jobId = null;
  let ws = null;
  let objectUrl = null;
  let progress = 0;
  let stats = { persons: 0, violations: 0, frame: 0, total: 0, fps: 0 };
  let alerts = [];
  let firstFrame = false;
  let errorMessage = "";

  const ACCEPTED_EXTENSIONS = ["mp4", "avi", "mov", "mkv", "webm"];

  // Pulled once from this app'''s manifest at render() time -- no
  // hardcoded per-solution copy in JS anymore.
  let uploadMeta = {
    badge: "", description: "", initIcon: "🎬", initTitle: "Initializing...",
    initSubtitle: "", initTags: [], inferenceStepTitle: "Inference",
    inferenceStepDesc: "", capabilities: [],
  };

  function meta() {
    return uploadMeta;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(
      Math.floor(Math.log(bytes) / Math.log(1024)),
      units.length - 1
    );
    return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function getViolationMeta(type) {
    if (typeof vtype === "function") return vtype(type);
    return {
      label: String(type || "Violation").replaceAll("_", " "),
      color: "var(--danger)", bg: "var(--danger-bg)", border: "var(--danger-border)", icon: "⚠️",
    };
  }

  function formatTime(timestamp) {
    if (!timestamp) return "--:--:--";
    let ms = Number(timestamp);
    if (ms < 1000000000000) ms *= 1000;
    const date = new Date(ms);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return date.toLocaleTimeString();
  }

  // ─────────────────────────────────────────────────────────────
  // Data
  // ─────────────────────────────────────────────────────────────

  async function loadActiveSolution() {
    try {
      const res = await fetch("/api/solutions/manifest");
      const data = await res.json();
      const m = (data.manifest && data.manifest.upload) || {};
      uploadMeta = {
        badge: m.badge || "",
        description: m.description || "",
        initIcon: m.init_icon || "🎬",
        initTitle: m.init_title || "Initializing...",
        initSubtitle: m.init_subtitle || "",
        initTags: m.init_tags || [],
        inferenceStepTitle: m.inference_step_title || "Inference",
        inferenceStepDesc: m.inference_step_desc || "",
        capabilities: m.capabilities || [],
      };
    } catch (error) {
      console.error("[UploadPage] Failed to load manifest:", error);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Rendering — full (structural) render
  // ─────────────────────────────────────────────────────────────

  async function render(container) {
    root = container;
    await loadActiveSolution();
    renderAll();
  }

  function renderAll() {
    if (!root) return;

    root.innerHTML = `
<div class="upload-page">

        <div class="dashboard-toolbar">
<div>
<div class="section-eyebrow">OFFLINE VIDEO ANALYSIS</div>
<div class="section-description">
              ${meta().description}
</div>
</div>
<span class="status-badge gold">${meta().badge}</span>
</div>

        ${
          status === "idle" || status === "ready"
            ? renderDropStage()
            : renderProcessingLayout()
        }

      </div>
    `;

    bindEvents();

    if ((status === "processing" || status === "done") && firstFrame) {
      const img = document.getElementById("upload-stream-frame");
      if (img && objectUrl) img.src = objectUrl;
    }
  }

  function renderDropStage() {
    return `
<div class="upload-layout">

        <section class="page-card upload-main-card">

          <div class="page-card-header">
<div>
<div class="page-card-title">Upload Video</div>
<div class="page-card-subtitle">Supported formats: MP4, AVI, MOV, MKV and WEBM</div>
</div>
</div>

          <div id="video-drop-zone" class="video-drop-zone">
<input id="video-file-input" type="file" accept="video/*,.mp4,.avi,.mov,.mkv,.webm" hidden>
<div class="upload-zone-icon">🎬</div>
<div class="upload-zone-title">Drop a video file here</div>
<div class="upload-zone-description">or select a file from your device</div>
<button id="browse-video-btn" class="primary-action-btn">Choose Video</button>
</div>

          <div id="selected-video-panel" class="selected-video-panel" style="display:${selectedFile ? "flex" : "none"};">
            ${selectedFile ? renderSelectedPanel() : ""}
</div>

          <div id="upload-result" class="upload-result ${errorMessage ? "error" : ""}" style="display:${errorMessage ? "block" : "none"};">
            ${errorMessage ? `<strong>⚠ Upload failed</strong><span>${escapeHtml(errorMessage)}</span>` : ""}
</div>

        </section>

        ${renderInfoColumn()}

      </div>
    `;
  }

  function renderSelectedPanel() {
    return `
<div class="selected-video-icon">🎥</div>
<div class="selected-video-info">
<strong>${escapeHtml(selectedFile.name)}</strong>
<span>${formatBytes(selectedFile.size)} · ${escapeHtml(selectedFile.type || "Video file")}</span>
</div>
<button id="remove-video-btn" class="file-remove-btn">✕</button>
<button id="upload-video-btn" class="primary-action-btn">
        ${uploading ? "Uploading..." : "Upload &amp; Start Detection"}
</button>
    `;
  }

  function renderProcessingLayout() {
    return `
<div class="upload-layout">

        <section class="page-card upload-main-card">

          <div class="page-card-header">
<div>
<div class="page-card-title">${escapeHtml(selectedFile?.name || "Processing")}</div>
<div class="page-card-subtitle">
                ${status === "done" ? "Processing complete" : "Analyzing this video"}
</div>
</div>
<div style="display:flex;gap:8px;">
              ${
                status === "done"
                  ? `<a id="upload-download-btn" href="/api/download/${jobId}" download="annotated_${(jobId || "").slice(0,8)}.mp4" class="primary-action-btn" style="text-decoration:none;display:inline-flex;align-items:center;">⬇ Download Annotated Video</a>`
                  : ""
              }
<button id="upload-reset-btn" class="page-action-btn">
                ${status === "done" ? "✓ Process Another Video" : "⏹ Stop"}
</button>
</div>
</div>

          <div style="padding:18px;">

            ${
              status === "processing" && !firstFrame
                ? renderInitScreen()
                : renderStreamArea()
            }

            ${status === "processing" ? renderProgressBar() : ""}

            ${renderStatCards()}

          </div>

        </section>

        <aside class="live-alerts-panel" style="max-height:640px;">

          <div class="live-alerts-header">
<div class="live-alerts-title">
<span id="upload-alerts-icon" class="live-alerts-title-icon ${alerts.length ? "danger" : "safe"}">
                ${alerts.length ? "⚠" : "✓"}
</span>
<span>Live Alerts</span>
</div>
<span id="upload-alert-count" class="live-alert-count ${alerts.length ? "danger" : ""}">${alerts.length}</span>
</div>

          <div id="upload-alert-list" class="live-alert-list">
            ${renderAlertItems()}
</div>

        </aside>

      </div>
    `;
  }

  function renderInitScreen() {
    const m = meta();
    return `
<div style="aspect-ratio:16/9;border-radius:14px;margin-bottom:16px;background:var(--navy);border:1px solid var(--gold-border);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;">
<div style="position:relative;width:90px;height:90px;">
<div style="position:absolute;inset:0;border-radius:50%;border:1.5px solid var(--gold);animation:ripple 2.4s ease-out infinite;opacity:0;"></div>
<div style="position:absolute;inset:0;border-radius:50%;border:1.5px solid var(--gold);animation:ripple 2.4s ease-out 0.7s infinite;opacity:0;"></div>
<div style="position:absolute;inset:0;border-radius:50%;border:1.5px solid var(--gold);animation:ripple 2.4s ease-out 1.4s infinite;opacity:0;"></div>
<div style="position:absolute;inset:10px;border-radius:50%;border:3px solid var(--navy);border-top:3px solid var(--gold);animation:spin 0.9s linear infinite;"></div>
<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:30px;">${m.initIcon}</div>
</div>
<div style="text-align:center;">
<div style="font-size:16px;font-weight:700;color:var(--gold-light);margin-bottom:8px;font-family:var(--f-heading);animation:subtlePulse 2s ease infinite;">
            ${m.initTitle}
</div>
<div style="font-size:12px;color:rgba(255,255,255,0.3);font-family:var(--f-mono);">
            ${m.initSubtitle}
</div>
</div>
<div style="display:flex;gap:8px;">
          ${m.initTags.map(t => `
<div style="padding:4px 12px;border-radius:20px;font-size:11px;border:1px solid var(--gold-border);color:var(--gold-light);font-family:var(--f-mono);">${t}</div>
          `).join("")}
</div>
</div>
    `;
  }

  function renderStreamArea() {
    return `
<div class="camera-video-area" style="border-radius:12px;border:1px solid var(--gold-border);margin-bottom:16px;">
<img id="upload-stream-frame" class="camera-frame" style="display:${firstFrame ? "block" : "none"};" alt="Processing feed"/>

        ${
          !firstFrame
            ? `
<div class="camera-placeholder">
<div class="camera-placeholder-icon">🎬</div>
<div class="camera-placeholder-title">Waiting for frames...</div>
</div>
            `
            : ""
        }

        ${
          firstFrame
            ? `
<div id="upload-fps-overlay" class="camera-overlay-fps">${stats.fps} FPS</div>
<div id="upload-live-badge" class="camera-overlay-live" style="${status === 'done' ? 'background:var(--success-bg);border-color:var(--success-border);color:var(--success);animation:none;' : ''}">
                ${status === "done" ? "✓ COMPLETE" : "● PROCESSING"}
</div>
<span class="scan-corner top-left"></span>
<span class="scan-corner top-right"></span>
<span class="scan-corner bottom-left"></span>
<span class="scan-corner bottom-right"></span>
            `
            : ""
        }
</div>
    `;
  }

  function renderProgressBar() {
    return `
<div style="margin-bottom:16px;">
<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:8px;color:var(--text-muted);">
<span style="font-family:var(--f-mono);">
            Frame <strong id="upload-frame-current" style="color:var(--navy);">${stats.frame}</strong> of <strong id="upload-frame-total" style="color:var(--navy);">${stats.total}</strong>
</span>
<span id="upload-progress-pct" style="color:var(--gold);font-weight:600;font-family:var(--f-mono);">${progress}%</span>
</div>
<div class="upload-progress-track">
<div id="upload-progress-fill" class="upload-progress-bar" style="width:${progress}%;"></div>
</div>
</div>
    `;
  }

  function renderStatCards() {
    const cards = [
      { id: "upload-stat-persons",    label: "Persons",    value: stats.persons,    color: "var(--tone-navy)",   bg: "var(--tone-navy-bg)",          border: "var(--tone-navy-border)",          icon: "👤" },
      { id: "upload-stat-violations", label: "Violations", value: stats.violations, color: "var(--danger)", bg: "var(--danger-bg)", border: "var(--danger-border)", icon: "⚠️" },
      { id: "upload-stat-alerts",     label: "Alerts",     value: alerts.length,    color: "var(--gold)",   bg: "var(--gold-pale)", border: "var(--gold-border)",  icon: "🔔" },
    ];

    return `
<div class="violations-summary-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:0;">
        ${cards.map(c => `
<div class="violations-summary-card" style="border-top-color:${c.color};">
<div class="violations-summary-icon" style="background:${c.bg};border-color:${c.border};">${c.icon}</div>
<div>
<div id="${c.id}" class="violations-summary-value" style="color:${c.color};">${c.value}</div>
<div class="violations-summary-label">${c.label}</div>
</div>
</div>
        `).join("")}
</div>
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

    return alerts.map(alert => {
      const m = getViolationMeta(alert.violation_type);
      return `
<div class="live-alert-item" style="background:${m.bg};border-color:${m.border};">
<div class="live-alert-main">
<div class="live-alert-icon" style="border-color:${m.border};">${m.icon}</div>
<div>
<div class="live-alert-person" style="color:${m.color};">ID:${escapeHtml(alert.person_id ?? "?")}</div>
<div class="live-alert-type">${escapeHtml(m.label)}</div>
</div>
</div>
<div class="live-alert-time">${formatTime(alert.timestamp)}</div>
</div>
      `;
    }).join("");
  }

  function renderInfoColumn() {
    const m = meta();
    return `
<aside class="upload-info-column">

        <section class="page-card">
<div class="page-card-header">
<div>
<div class="page-card-title">Processing Pipeline</div>
<div class="page-card-subtitle">Video analysis workflow</div>
</div>
</div>
<div class="upload-pipeline">
            ${pipelineStep("01", "Upload", "Video stored securely on the Jetson device.")}
            ${pipelineConnector()}
            ${pipelineStep("02", "Decode", "Video frames are read and decoded sequentially.")}
            ${pipelineConnector()}
            ${pipelineStep("03", m.inferenceStepTitle, m.inferenceStepDesc)}
            ${pipelineConnector()}
            ${pipelineStep("04", "Results", "Annotated output and violation events are generated.")}
</div>
</section>

        <section class="page-card upload-capability-card">
<div class="page-card-title">Analysis Capabilities</div>
<div class="capability-list">
            ${m.capabilities.map(c => `<div>${c}</div>`).join("")}
</div>
</section>

      </aside>
    `;
  }

  function pipelineStep(number, title, description) {
    return `
<div class="upload-pipeline-step">
<div class="upload-pipeline-number">${number}</div>
<div><strong>${title}</strong><span>${description}</span></div>
</div>
    `;
  }

  function pipelineConnector() {
    return `<div class="upload-pipeline-connector"></div>`;
  }

  // ─────────────────────────────────────────────────────────────
  // Incremental (non-flickering) UI updates — used for every stats
  // WebSocket message while processing, instead of a full renderAll().
  // ─────────────────────────────────────────────────────────────

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function updateProcessingUi() {
    setText("upload-frame-current", stats.frame);
    setText("upload-frame-total", stats.total);
    setText("upload-progress-pct", `${progress}%`);

    const fill = document.getElementById("upload-progress-fill");
    if (fill) fill.style.width = `${progress}%`;

    setText("upload-fps-overlay", `${stats.fps} FPS`);

    setText("upload-stat-persons", stats.persons);
    setText("upload-stat-violations", stats.violations);
    setText("upload-stat-alerts", alerts.length);

    const list = document.getElementById("upload-alert-list");
    if (list) list.innerHTML = renderAlertItems();

    const count = document.getElementById("upload-alert-count");
    if (count) {
      count.textContent = alerts.length;
      count.classList.toggle("danger", alerts.length > 0);
    }

    const icon = document.getElementById("upload-alerts-icon");
    if (icon) {
      icon.textContent = alerts.length ? "⚠" : "✓";
      icon.classList.toggle("danger", alerts.length > 0);
      icon.classList.toggle("safe", alerts.length === 0);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Events
  // ─────────────────────────────────────────────────────────────

  function bindEvents() {
    const dropZone = document.getElementById("video-drop-zone");
    const fileInput = document.getElementById("video-file-input");
    const browseButton = document.getElementById("browse-video-btn");

    if (browseButton) {
      browseButton.onclick = e => { e.stopPropagation(); fileInput.click(); };
    }
    if (dropZone) {
      dropZone.onclick = () => fileInput.click();
      dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add("dragging"); };
      dropZone.ondragleave = () => dropZone.classList.remove("dragging");
      dropZone.ondrop = e => {
        e.preventDefault();
        dropZone.classList.remove("dragging");
        const file = e.dataTransfer.files?.[0];
        if (file) selectFile(file);
      };
    }
    if (fileInput) {
      fileInput.onchange = e => {
        const file = e.target.files?.[0];
        if (file) selectFile(file);
      };
    }

    const removeBtn = document.getElementById("remove-video-btn");
    if (removeBtn) removeBtn.onclick = removeFile;

    const uploadBtn = document.getElementById("upload-video-btn");
    if (uploadBtn) uploadBtn.onclick = startUpload;

    const resetBtn = document.getElementById("upload-reset-btn");
    if (resetBtn) resetBtn.onclick = resetAll;
  }

  function selectFile(file) {
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !ACCEPTED_EXTENSIONS.includes(extension)) {
      errorMessage = "Choose an MP4, AVI, MOV, MKV, or WEBM video.";
      renderAll();
      return;
    }
    selectedFile = file;
    status = "ready";
    errorMessage = "";
    renderAll();
  }

  function removeFile() {
    selectedFile = null;
    status = "idle";
    errorMessage = "";
    renderAll();
  }

  async function startUpload() {
    if (!selectedFile || uploading) return;

    uploading = true;
    status = "uploading";
    errorMessage = "";
    renderAll();

    try {
      await loadActiveSolution();

      const formData = new FormData();
      formData.append("file", selectedFile);

      const response = await fetch("/api/upload", { method: "POST", body: formData });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || `Server returned ${response.status}`);
      }

      jobId = data.job_id;

      status = "processing";
      firstFrame = false;
      stats = { persons: 0, violations: 0, frame: 0, total: 0, fps: 0 };
      alerts = [];
      progress = 0;

      connectProcessingWs();
    } catch (error) {
      console.error("[UploadPage] Upload failed:", error);
      status = "ready";
      errorMessage = error.message || "Unable to upload video.";
    } finally {
      uploading = false;
      renderAll();
    }
  }

  function connectProcessingWs() {
    if (ws) { try { ws.close(); } catch (_) {} }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/process/${jobId}`);
    ws.binaryType = "blob";

    ws.onmessage = event => {
      if (event.data instanceof Blob) {
        const wasFirstFrame = firstFrame;
        firstFrame = true;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(event.data);

        const img = document.getElementById("upload-stream-frame");
        if (img && !wasFirstFrame) {
          // First frame arriving flips the layout from init-screen to
          // stream-area, which IS a structural change -- needs a full render.
          renderAll();
        } else if (img) {
          // Every frame after that is just a src swap -- no re-render.
          img.src = objectUrl;
          img.style.display = "block";
        } else {
          renderAll();
        }
        return;
      }

      try {
        const data = JSON.parse(event.data);
        if (data.type !== "stats") return;

        progress = data.progress ?? progress;
        stats = {
          persons: data.persons ?? 0,
          violations: data.violations ?? 0,
          frame: data.frame ?? 0,
          total: data.total ?? 0,
          fps: data.fps ?? 0,
        };

        if (Array.isArray(data.alerts) && data.alerts.length) {
          alerts = [...data.alerts, ...alerts].slice(0, 100);
        }

        if (progress >= 100 && status !== "done") {
          status = "done";
          renderAll(); // structural change -- Download button appears
          return;
        }

        // Steady-state processing: update numbers/lists in place only,
        // no full teardown -- this is what stops the flicker.
        updateProcessingUi();
      } catch (error) {
        console.warn("[UploadPage] Invalid WS message:", error);
      }
    };

    ws.onclose = () => {
      if (status === "processing") {
        status = "done";
        renderAll();
      }
    };
  }

  function resetAll() {
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }

    selectedFile = null;
    status = "idle";
    jobId = null;
    progress = 0;
    stats = { persons: 0, violations: 0, frame: 0, total: 0, fps: 0 };
    alerts = [];
    firstFrame = false;
    errorMessage = "";

    renderAll();
  }

  function destroy() {
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    root = null;
  }

  return { render, destroy };
})();

window.UploadPage = UploadPage;
