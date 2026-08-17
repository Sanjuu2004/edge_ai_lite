// Tone -> CSS var palette. Matches core/annotation.py's TONE_COLORS
// (BGR side) so a violation's color agrees between the video overlay
// and the web UI. Solutions never hardcode a CSS var directly -- they
// pick one of these four tones per violation_type in their manifest.
const TONE_STYLES = {
  danger:  { color: "var(--danger)",  bg: "var(--danger-bg)",  border: "var(--danger-border)" },
  warn:    { color: "var(--warn)",    bg: "var(--warn-bg)",    border: "var(--warn-border)" },
  gold:    { color: "var(--gold)",    bg: "var(--gold-pale)",  border: "var(--gold-border)" },
  success: { color: "var(--success)", bg: "var(--success-bg)", border: "var(--success-border)" },
};

function vtype(t) {
  const manifestTypes = (window.ACTIVE_MANIFEST && window.ACTIVE_MANIFEST.violation_types) || {};
  const entry = manifestTypes[t];

  if (entry) {
    const style = TONE_STYLES[entry.tone] || TONE_STYLES.danger;
    return { label: entry.label, icon: entry.icon || "⚠️", ...style };
  }

  return {
    label: String(t || "Violation").replaceAll("_", " "),
    icon: "⚠️",
    ...TONE_STYLES.danger,
  };
}

const ThemeManager = (() => {
  const STORAGE_KEY = "edge-ai-theme";
  function getTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    try { localStorage.setItem(STORAGE_KEY, mode); } catch (_) {}
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.textContent = mode === "dark" ? "☀️" : "🌙";
      btn.title = mode === "dark" ? "Switch to light theme" : "Switch to dark theme";
    }
  }
  function toggle() { applyTheme(getTheme() === "dark" ? "light" : "dark"); }
  function init() {
    applyTheme(getTheme());
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) btn.onclick = toggle;
  }
  return { init, toggle, getTheme };
})();
window.ThemeManager = ThemeManager;
window.addEventListener("DOMContentLoaded", () => ThemeManager.init());
