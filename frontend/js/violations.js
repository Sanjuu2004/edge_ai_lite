// frontend/js/violations.js
// Edge AI Platform — Violations Gallery Page
//
// Backend contract:
//   GET    /api/screenshots   -> [{ filename, person_id, violation_type, timestamp, url, camera? }]
//   DELETE /api/screenshots   -> clears all screenshots
//   GET    /api/solutions     -> { available: [...], active: "..." }
//
// Filters/summary cards adapt to whichever solution is currently active
// (ppe_industrial vs driver_monitoring), since /api/screenshots is itself
// scoped server-side to the active solution's events only.

const ViolationsPage = (() => {
    // Filters built dynamically from this app''s manifest.
  // Since each deployed app represents exactly one solution,
  // there is no hardcoded solution registry anymore.
  let FILTERS = [{ k: "all", l: "All Violations" }];

  let root = null;
  let refreshTimer = null;

  let items = [];
  let loading = true;
  let filter = "all";
  let selected = null;
  let clearing = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
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

  function formatFullTime(timestamp) {
    if (!timestamp) return "—";

    let ms = Number(timestamp);
    if (ms < 1000000000000) ms *= 1000;

    const date = new Date(ms);
    if (Number.isNaN(date.getTime())) return "—";

    return date.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function counts() {
    const result = { all: items.length };
    FILTERS.forEach(f => {
      if (f.k === "all") return;
      result[f.k] = items.filter(i => i.violation_type === f.k).length;
    });
    return result;
  }

  function filteredItems() {
    return filter === "all"
      ? items
      : items.filter(i => i.violation_type === filter);
  }

  // ─────────────────────────────────────────────────────────────
  // Data
  // ─────────────────────────────────────────────────────────────

      async function loadActiveSolution() {
    try {
      const res = await fetch("/api/solutions/manifest");
      const data = await res.json();

      const violationTypes =
        (data.manifest && data.manifest.violation_types) || {};

      FILTERS = [
        { k: "all", l: "All Violations" },
        ...Object.keys(violationTypes).map(k => ({
          k,
          l: violationTypes[k].label,
        })),
      ];

      // Reset filter if it no longer exists in this solution.
      if (!FILTERS.some(f => f.k === filter)) {
        filter = "all";
      }
    } catch (error) {
      console.error("[Violations] Failed to load manifest:", error);
    }
  }
    async function loadData() {
    if (!root || !document.body.contains(root)) {
      clearInterval(refreshTimer);
      return;
    }

    try {
      const response = await fetch("/api/screenshots");

      if (!response.ok) {
        throw new Error(`Screenshots API returned ${response.status}`);
      }

      items = await response.json();
    } catch (error) {
      console.error("[Violations] Load failed:", error);
    } finally {
      loading = false;
      renderAll();
    }
  }

  async function clearAll() {
    if (!window.confirm("Delete all violation screenshots?")) {
      return;
    }

    clearing = true;
    renderAll();

    try {
      await fetch("/api/screenshots", { method: "DELETE" });
      items = [];
    } catch (error) {
      console.error("[Violations] Clear failed:", error);
    } finally {
      clearing = false;
      renderAll();
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Rendering
  // ─────────────────────────────────────────────────────────────

  function renderAll() {
    if (!root) return;

    root.innerHTML = `
      <div class="violations-page">
        ${renderToolbar()}
        ${items.length ? renderSummary() : ""}
        ${renderGallery()}
      </div>
      <div id="violations-lightbox-root"></div>
    `;

    bindEvents();

    if (selected) {
      renderLightbox(selected);
    }
  }

  function renderToolbar() {
    const c = counts();

    return `
      <div class="violations-toolbar">

        <div class="violations-filters">
          ${FILTERS.map(f => `
            <button
              class="filter-pill-btn ${filter === f.k ? "active" : ""}"
              data-filter="${f.k}"
            >
              ${f.l}
              <span class="filter-pill-badge">
                ${c[f.k] ?? 0}
              </span>
            </button>
          `).join("")}
        </div>

        <div class="violations-right-controls">
          <div class="violations-refresh-tag">Auto-refresh 4s</div>

          <button id="violations-refresh-btn" class="violations-btn">
            ↺ Refresh
          </button>

          ${
            items.length > 0
              ? `
                <button
                  id="violations-clear-btn"
                  class="violations-btn violations-btn-danger"
                  ${clearing ? "disabled" : ""}
                >
                  ${clearing ? "Clearing..." : "🗑 Clear All"}
                </button>
              `
              : ""
          }
        </div>

      </div>
    `;
  }

  function renderSummary() {
    const c = counts();

    const cards = [
      {
        label: "Total Screenshots",
        value: c.all,
        color: "var(--tone-navy)",
        bg: "var(--tone-navy-bg)",
        border: "var(--tone-navy-bg)",
        icon: "📸",
      },
      ...FILTERS.filter(f => f.k !== "all").map(f => {
        const meta = getViolationMeta(f.k);
        return {
          label: f.l,
          value: c[f.k] ?? 0,
          color: meta.color,
          bg: meta.bg,
          border: meta.border,
          icon: meta.icon,
        };
      }),
    ];

    return `
      <div class="violations-summary-grid">
        ${cards.map(card => `
          <div class="violations-summary-card" style="border-top-color:${card.color};">
            <div class="violations-summary-icon" style="background:${card.bg};border-color:${card.border};">
              ${card.icon}
            </div>
            <div>
              <div class="violations-summary-value" style="color:${card.color};">
                ${card.value}
              </div>
              <div class="violations-summary-label">
                ${card.label}
              </div>
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderGallery() {
    if (loading) {
      return `
        <div style="text-align:center;padding:80px;color:var(--text-muted);font-size:13px;font-family:var(--f-mono);">
          Loading screenshots...
        </div>
      `;
    }

    const filtered = filteredItems();

    if (!filtered.length) {
      return `
        <div class="page-empty-state" style="min-height:280px;background:var(--surface-bg);border:1px solid var(--ivory-border);border-radius:14px;">
          <div class="page-empty-icon" style="width:64px;height:64px;font-size:30px;">📸</div>
          <strong style="font-family:var(--f-heading);font-size:22px;">No violation screenshots yet</strong>
          <span style="max-width:360px;">
            Screenshots are captured automatically when a violation alert fires — from both live feed and video upload.
          </span>
        </div>
      `;
    }

    return `
      <div class="screenshot-grid">
        ${filtered.map(item => renderScreenshotCard(item)).join("")}
      </div>
    `;
  }

  function renderScreenshotCard(item) {
    const meta = getViolationMeta(item.violation_type);
    const key = escapeHtml(item.filename || `${item.person_id}-${item.timestamp}`);

    return `
      <div class="screenshot-card" style="border-top-color:${meta.color};" data-screenshot-key="${key}">
        <div class="screenshot-thumb-wrap">
          <img
            src="${escapeHtml(item.url)}"
            alt="Violation ${escapeHtml(item.person_id)}"
            loading="lazy"
            onerror="this.style.display='none';this.nextElementSibling.style.display='flex';"
          />
          <div class="screenshot-thumb-error" style="display:none;">
            <div style="font-size:28px;margin-bottom:6px;">📷</div>
            Image unavailable
          </div>
          <div class="screenshot-id-badge">ID: ${escapeHtml(item.person_id)}</div>
          <div class="screenshot-zoom-hint">🔍</div>
        </div>

        <div class="screenshot-info">
          <span class="violation-badge" style="background:${meta.bg};color:${meta.color};border-color:${meta.border};">
            ${meta.icon}
            ${escapeHtml(meta.label)}
          </span>
          <div class="screenshot-time">
            ${formatFullTime(item.timestamp)}
          </div>
        </div>
      </div>
    `;
  }

  function renderLightbox(item) {
    const lightboxRoot = document.getElementById("violations-lightbox-root");
    if (!lightboxRoot) return;

    const meta = getViolationMeta(item.violation_type);

    lightboxRoot.innerHTML = `
      <div class="lightbox-overlay" id="lightbox-overlay">
        <div class="lightbox-modal" id="lightbox-modal">

          <div class="lightbox-header">
            <div class="lightbox-header-left">
              <div class="lightbox-icon" style="background:${meta.bg};border-color:${meta.border};">
                ${meta.icon}
              </div>
              <div>
                <div class="lightbox-person">Person ID: ${escapeHtml(item.person_id)}</div>
                <div class="lightbox-time">${formatFullTime(item.timestamp)}</div>
              </div>
            </div>

            <div class="lightbox-header-right">
              <span class="violation-badge" style="background:${meta.bg};color:${meta.color};border-color:${meta.border};">
                ${meta.icon}
                ${escapeHtml(meta.label)}
              </span>
              <button id="lightbox-close-btn" class="lightbox-close-btn">✕</button>
            </div>
          </div>

          <img class="lightbox-image" src="${escapeHtml(item.url)}" alt="violation" />

          <div class="lightbox-footer">
            <a href="${escapeHtml(item.url)}" download target="_blank" rel="noreferrer" class="lightbox-download-btn">
              ⬇ Download
            </a>
          </div>

        </div>
      </div>
    `;

    document.getElementById("lightbox-overlay").onclick = event => {
      if (event.target.id === "lightbox-overlay") {
        closeLightbox();
      }
    };

    document.getElementById("lightbox-close-btn").onclick = closeLightbox;
  }

  function closeLightbox() {
    selected = null;
    const lightboxRoot = document.getElementById("violations-lightbox-root");
    if (lightboxRoot) {
      lightboxRoot.innerHTML = "";
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Events
  // ─────────────────────────────────────────────────────────────

  function bindEvents() {
    if (!root) return;

    root.querySelectorAll("[data-filter]").forEach(btn => {
      btn.onclick = () => {
        filter = btn.dataset.filter;
        renderAll();
      };
    });

    const refreshBtn = document.getElementById("violations-refresh-btn");
    if (refreshBtn) {
      refreshBtn.onclick = () => {
        loading = true;
        renderAll();
        loadActiveSolution().then(loadData);
      };
    }

    const clearBtn = document.getElementById("violations-clear-btn");
    if (clearBtn) {
      clearBtn.onclick = clearAll;
    }

    root.querySelectorAll(".screenshot-card").forEach((card, index) => {
      card.onclick = () => {
        const filtered = filteredItems();
        selected = filtered[index];
        renderAll();
      };
    });
  }

  function handleEscape(event) {
    if (event.key === "Escape" && selected) {
      closeLightbox();
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────

  async function render(container) {
    root = container;
    loading = true;

    window.removeEventListener("keydown", handleEscape);
    window.addEventListener("keydown", handleEscape);

    await loadActiveSolution();
    renderAll();
    loadData();

    clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      loadActiveSolution().then(loadData);
    }, 4000);
  }

  function destroy() {
    clearInterval(refreshTimer);
    refreshTimer = null;
    window.removeEventListener("keydown", handleEscape);
    root = null;
  }

  return { render, destroy };
})();

window.ViolationsPage = ViolationsPage;
