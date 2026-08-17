"""
core/annotation.py

Shared frame-annotation drawing for both the live-camera path
(platform_core/stream_manager.py) and offline-upload path
(platform_core/video_upload_processor.py).

Fully manifest-driven: colors/labels for each violation_type come from
whichever solution is active (solution.manifest["violation_types"]), not
hardcoded per-domain dicts here. This is what lets one shared drawing
function serve PPE, Driver Monitoring, Healthcare Monitoring -- or any
future solution -- without ever being edited again when a new domain
is added.
"""

import cv2

_FONT = cv2.FONT_HERSHEY_SIMPLEX

# The only hardcoded color knowledge left: a small tone->BGR palette.
# Solutions pick a tone (danger/warn/gold/success) per violation_type in
# their manifest; this is the one place that tone maps to an actual
# color, so the palette itself stays consistent platform-wide.
TONE_COLORS = {
    "danger": (50, 50, 220),
    "warn": (0, 165, 255),
    "gold": (10, 190, 220),
    "success": (90, 200, 90),
}
COLOR_OK = TONE_COLORS["success"]
COLOR_NEUTRAL = (180, 130, 40)  # fallback for an unrecognized violation_type


def color_for(violation_type, is_violation, violation_types_manifest):
    if not is_violation or not violation_type:
        return COLOR_OK
    entry = (violation_types_manifest or {}).get(violation_type)
    if not entry:
        return COLOR_NEUTRAL
    return TONE_COLORS.get(entry.get("tone"), COLOR_NEUTRAL)


def label_for(violation_type, is_violation, violation_types_manifest):
    if not is_violation or not violation_type:
        return "OK"
    entry = (violation_types_manifest or {}).get(violation_type)
    if entry and entry.get("short_label"):
        return entry["short_label"]
    return violation_type.replace("_", " ").upper()


def _draw_label(frame, text, x1, y1, bg_color, font_scale=0.32, thickness=1):
    (tw, th), baseline = cv2.getTextSize(text, _FONT, font_scale, thickness)
    pad = 3
    lx = max(x1, 0)
    ly = max(y1 - th - pad * 2, 0)

    cv2.rectangle(
        frame, (lx, ly), (lx + tw + pad * 2, ly + th + baseline + pad * 2),
        bg_color, -1,
    )
    cv2.putText(
        frame, text, (lx + pad, ly + th + pad),
        _FONT, font_scale, (255, 255, 255), thickness, cv2.LINE_AA,
    )


def draw_annotations(frame, smoothed: dict, persons: int, violations: int,
                      violation_types_manifest: dict = None):
    """
    violation_types_manifest: the active solution's
    manifest["violation_types"] dict, e.g.
        {"no_helmet": {"short_label": "NO HELMET", "tone": "warn"}, ...}
    Pass None/{} for a solution with no violation types yet defined --
    everything just draws as green "OK" boxes.
    """
    for pid, status in smoothed.items():
        x1, y1, x2, y2 = [int(v) for v in status["bbox"]]
        is_violation = bool(status.get("violation"))
        vtype = status.get("violation_type")

        color = color_for(vtype, is_violation, violation_types_manifest)
        thickness = 3 if is_violation else 2

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        label = label_for(vtype, is_violation, violation_types_manifest)
        _draw_label(frame, label, x1, y1, color)

    return frame
