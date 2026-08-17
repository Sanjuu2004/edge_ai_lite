"""
solutions/driver_monitoring/logic.py

Driver Monitoring solution -- "Smart Industrial Safety"'s sibling on the
platform diagram, but structurally different from ppe_industrial: the
underlying model has NO person/driver class, just five direct detail
classes (Open Eye, Closed Eye, Cigarette, Phone, Seatbelt) from a single
fixed cabin camera. There is exactly one subject in frame by construction,
so this solution sets requires_tracking = False and skips ByteTracker
entirely -- stream_manager.py won't even run tracking for this solution.

Each of the four monitored conditions (drowsy eyes, phone, cigarette,
seatbelt) is independent -- a driver can be drowsy AND on the phone at
once, unlike PPE's helmet/vest which collapse into one combined
violation_type. To let platform_core's EventManager / AlertEngine /
DataManager stay completely generic and unmodified (they already just
operate on "any dict of id -> {violation, violation_type, bbox}"), each
condition is reported under its own pseudo track_id
(driver_eyes / driver_phone / driver_cigarette / driver_seatbelt) so each
gets independent cooldown/alert-dedup state instead of colliding under
one shared "driver" id.
"""

import json
import os
from collections import deque

from solutions.base_solution import BaseSolution

RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.json")


def _best_of(detections, cls_name):
    """Return the highest-confidence detection of a given class this
    frame, or None if absent."""
    matches = [d for d in detections if d["class"] == cls_name]
    if not matches:
        return None
    return max(matches, key=lambda d: d["conf"])


class PerclosTracker:
    """Rolling percentage-of-eyes-closed drowsiness detector."""

    def __init__(self, window_frames=45, closed_ratio_threshold=0.7,
                 min_observed_frames=20):
        self.window_frames = window_frames
        self.closed_ratio_threshold = closed_ratio_threshold
        self.min_observed_frames = min_observed_frames
        self._buf = deque(maxlen=window_frames)

    def update(self, open_eye_det, closed_eye_det):
        """Only records an observation when at least one eye-state class
        was actually detected this frame -- frames where the driver's
        eyes aren't visible at all (occluded/turned away) are skipped
        rather than counted as either state, so PERCLOS isn't distorted
        by camera angle/occlusion gaps."""
        if closed_eye_det is None and open_eye_det is None:
            return None  # no observation this frame

        if closed_eye_det is not None and (
            open_eye_det is None or closed_eye_det["conf"] >= open_eye_det["conf"]
        ):
            self._buf.append(1)
            latest_bbox = closed_eye_det["bbox"]
        else:
            self._buf.append(0)
            latest_bbox = open_eye_det["bbox"]

        if len(self._buf) < self.min_observed_frames:
            return {"drowsy": False, "bbox": latest_bbox, "ratio": 0.0}

        ratio = sum(self._buf) / len(self._buf)
        return {
            "drowsy": ratio >= self.closed_ratio_threshold,
            "bbox": latest_bbox,
            "ratio": ratio,
        }


class PresenceDebouncer:
    """Generic 'must be seen N consecutive frames to confirm, must be
    absent M consecutive frames to clear' debouncer -- used identically
    for phone and cigarette (confirm-on-presence) and, inverted, for
    seatbelt (confirm-on-absence)."""

    def __init__(self, confirm_frames, clear_frames, invert=False):
        self.confirm_frames = confirm_frames
        self.clear_frames = clear_frames
        self.invert = invert  # True = seatbelt-style (violation on absence)
        self._present_streak = 0
        self._absent_streak = 0
        self._confirmed = False
        self._last_bbox = None

    def update(self, detection):
        if detection is not None:
            self._present_streak += 1
            self._absent_streak = 0
            self._last_bbox = detection["bbox"]
        else:
            self._absent_streak += 1
            self._present_streak = 0

        if not self.invert:
            # confirm on sustained presence, clear on sustained absence
            if self._present_streak >= self.confirm_frames:
                self._confirmed = True
            if self._absent_streak >= self.clear_frames:
                self._confirmed = False
        else:
            # confirm on sustained absence (seatbelt not detected), clear
            # on sustained presence
            if self._absent_streak >= self.confirm_frames:
                self._confirmed = True
            if self._present_streak >= self.clear_frames:
                self._confirmed = False

        return {"confirmed": self._confirmed, "bbox": self._last_bbox}


class DriverMonitoringSolution(BaseSolution):
    name = "driver_monitoring"
    requires_tracking = False

    manifest = {
        "icon": "🚗",
        "name": "Driver Monitoring",
        "description": "Drowsiness, phone, smoking & seatbelt detection",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">Driver</span> Monitor',
        "model_badge": "YOLOv8 · GPU",
        "doc_title": "Driver Monitor",
        "violation_types": {
            "drowsy": {"label": "Drowsy", "short_label": "DROWSY", "icon": "😴", "tone": "danger"},
            "phone_usage": {"label": "Phone Usage", "short_label": "PHONE", "icon": "📱", "tone": "warn"},
            "smoking": {"label": "Smoking", "short_label": "SMOKING", "icon": "🚬", "tone": "warn"},
            "no_seatbelt": {"label": "No Seatbelt", "short_label": "NO BELT", "icon": "🔒", "tone": "gold"},
        },
        "upload": {
            "badge": "YOLOv8 · TensorRT · Driver Monitoring",
            "description": "Upload cabin footage for driver safety processing.",
            "init_icon": "🚗",
            "init_title": "Initializing Driver Monitoring...",
            "init_subtitle": "Decoding video · Loading inference engine",
            "init_tags": ["YOLOv8", "PERCLOS", "Driver Logic"],
            "inference_step_title": "Inference",
            "inference_step_desc": "YOLOv8 detects eyes, phone, cigarette & seatbelt.",
            "capabilities": [
                "✓ Drowsiness Detection (PERCLOS)",
                "✓ Phone Usage Detection",
                "✓ Smoking Detection",
                "✓ Seatbelt Compliance",
                "✓ Annotated Video Output",
            ],
        },
    }

    def __init__(self, rules_path=RULES_PATH):
        with open(rules_path) as f:
            self.rules = json.load(f)

        p = self.rules["perclos"]
        self.perclos = PerclosTracker(
            window_frames=p["window_frames"],
            closed_ratio_threshold=p["closed_ratio_threshold"],
            min_observed_frames=p["min_observed_frames"],
        )

        ph = self.rules["phone"]
        self.phone = PresenceDebouncer(
            confirm_frames=ph["min_consecutive_frames"],
            clear_frames=ph["clear_after_missing_frames"],
        )

        cg = self.rules["cigarette"]
        self.cigarette = PresenceDebouncer(
            confirm_frames=cg["min_consecutive_frames"],
            clear_frames=cg["clear_after_missing_frames"],
        )

        sb = self.rules["seatbelt"]
        self.seatbelt = PresenceDebouncer(
            confirm_frames=sb["absence_min_consecutive_frames"],
            clear_frames=sb["clear_after_present_frames"],
            invert=True,
        )

        # Proxy for "is a driver actually in the seat" -- this model has
        # no person/driver class, so an empty room would otherwise get
        # confirmed as a seatbelt violation (technically true: no seatbelt
        # WAS ever detected -- but there's also no one to wear one). Only
        # let the seatbelt debouncer accumulate its absence streak while
        # a face has been seen recently; freeze it (no observation)
        # otherwise, rather than confirming against an empty scene.
        self._frames_since_face_seen = 999999
        self._face_recency_window = 45  # ~3s at 15fps, matches PERCLOS window

        # Fallback bbox (full-frame-ish placeholder) for the rare case a
        # violation confirms before any bbox was ever recorded for that
        # channel -- shouldn't normally happen given the frame thresholds
        # above, but keeps EventManager's status["bbox"] access safe.
        self._fallback_bbox = [0, 0, 100, 100]

    def get_class_names(self) -> list:
        return self.rules["class_names"]

    def get_stats(self, smoothed: dict) -> dict:
        # There is always exactly one driver by construction (single fixed
        # cabin camera, requires_tracking=False, "driver present" treated
        # as always-true per earlier decision) -- so persons is a constant
        # 1, not len(smoothed), which would otherwise count the 4 status
        # channels (eyes/phone/cigarette/seatbelt) as 4 people.
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": 1, "violations": violations}

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        """
        tracked_persons: unused (requires_tracking = False), kept only so
        stream_manager.py can call every solution with the same two-arg
        signature without branching per solution.
        all_detections: raw per-frame detections from the inference
        backend, e.g. [{"bbox": [...], "class": "Phone", "conf": 0.81}, ...]

        Returns a dict shaped identically to PPEIndustrialSolution's
        output (id -> status dict with violation/violation_type/bbox) so
        EventManager.process() works completely unchanged.
        """
        open_eye = _best_of(all_detections, "Open Eye")
        closed_eye = _best_of(all_detections, "Closed Eye")
        phone_det = _best_of(all_detections, "Phone")
        cigarette_det = _best_of(all_detections, "Cigarette")
        seatbelt_det = _best_of(all_detections, "Seatbelt")

        perclos_result = self.perclos.update(open_eye, closed_eye)
        phone_result = self.phone.update(phone_det)
        cigarette_result = self.cigarette.update(cigarette_det)

        if open_eye is not None or closed_eye is not None:
            self._frames_since_face_seen = 0
        else:
            self._frames_since_face_seen += 1

        face_recently_seen = self._frames_since_face_seen < self._face_recency_window

        if seatbelt_det is not None or face_recently_seen:
            seatbelt_result = self.seatbelt.update(seatbelt_det)
        else:
            # No face recently, no seatbelt detected -- likely an empty
            # seat, not a violation. Report last-known confirmed state
            # without advancing the absence streak.
            seatbelt_result = {
                "confirmed": self.seatbelt._confirmed,
                "bbox": self.seatbelt._last_bbox,
            }

        smoothed = {}

        if perclos_result is not None:
            smoothed["driver_eyes"] = {
                "track_id": "driver_eyes",
                "bbox": perclos_result["bbox"],
                "violation": perclos_result["drowsy"],
                "violation_type": "drowsy" if perclos_result["drowsy"] else None,
                "occluded": False,
            }

        # Only report a channel when there is something real to show:
        # either a genuine detection bbox has been observed at least once,
        # or the channel is a CONFIRMED violation (which needs some bbox
        # for screenshot cropping, even if it falls back to a placeholder
        # because the violation is absence-based, like seatbelt). A
        # channel that has simply never seen its class this session
        # (e.g. no phone ever detected) is skipped entirely, rather than
        # rendered as a permanent placeholder "OK" box at a fixed corner.
        if phone_result["bbox"] is not None or phone_result["confirmed"]:
            smoothed["driver_phone"] = {
                "track_id": "driver_phone",
                "bbox": phone_result["bbox"] or self._fallback_bbox,
                "violation": phone_result["confirmed"],
                "violation_type": "phone_usage" if phone_result["confirmed"] else None,
                "occluded": False,
            }

        if cigarette_result["bbox"] is not None or cigarette_result["confirmed"]:
            smoothed["driver_cigarette"] = {
                "track_id": "driver_cigarette",
                "bbox": cigarette_result["bbox"] or self._fallback_bbox,
                "violation": cigarette_result["confirmed"],
                "violation_type": "smoking" if cigarette_result["confirmed"] else None,
                "occluded": False,
            }

        if seatbelt_result["bbox"] is not None or seatbelt_result["confirmed"]:
            smoothed["driver_seatbelt"] = {
                "track_id": "driver_seatbelt",
                "bbox": seatbelt_result["bbox"] or self._fallback_bbox,
                "violation": seatbelt_result["confirmed"],
                "violation_type": "no_seatbelt" if seatbelt_result["confirmed"] else None,
                "occluded": False,
            }

        return smoothed
