"""
solutions/healthcare_monitoring/logic.py

Healthcare Monitoring solution -- third domain on the platform diagram,
alongside PPE Industrial Safety and Driver Monitoring.

Two features live here, at very different readiness levels:

  ROOM OCCUPANCY (functional today):
    Works with ANY generic object-detection model that has a "person"
    class -- no custom training needed. Reuses ByteTracker (same as PPE)
    to get stable per-person counts, and flags overcrowding when the
    tracked person count exceeds rules.json's max_occupancy for enough
    consecutive frames.

  FALL DETECTION (stub, not wired to inference):
    Deliberately NOT implemented against bounding-box classes -- a
    "person_fallen" object-detection class would need real fall footage
    to train on, which isn't available yet. The intended approach is
    pose-estimation (17-point skeleton keypoints, e.g. YOLOv8-Pose or
    MediaPipe Pose) + a geometric rule (torso angle vs vertical), which
    needs NO custom training data at all -- pretrained pose models
    already exist. check_falls() below is the documented plug-in point
    for when that backend exists; it is not called from evaluate() yet.

Camera setup: reuses the same Jetson camera slots (video0/video2) as
every other solution -- CameraManager/StreamManager are already fully
generic per-slot, nothing camera-specific needed here.
"""

import json
import os

from framework.common.base_solution import BaseSolution

RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.json")


class OccupancyDebouncer:
    """Same confirm/clear-streak pattern as driver_monitoring's
    PresenceDebouncer, applied to a room-level person COUNT instead of
    a single class's presence."""

    def __init__(self, max_occupancy, confirm_frames, clear_frames):
        self.max_occupancy = max_occupancy
        self.confirm_frames = confirm_frames
        self.clear_frames = clear_frames
        self._over_streak = 0
        self._under_streak = 0
        self._confirmed = False

    def update(self, current_count):
        if current_count > self.max_occupancy:
            self._over_streak += 1
            self._under_streak = 0
        else:
            self._under_streak += 1
            self._over_streak = 0

        if self._over_streak >= self.confirm_frames:
            self._confirmed = True
        if self._under_streak >= self.clear_frames:
            self._confirmed = False

        return self._confirmed


class HealthcareMonitoringSolution(BaseSolution):
    name = "healthcare_monitoring"
    requires_tracking = True  # Room Occupancy needs stable person counts

    manifest = {
        "icon": "🏥",
        "name": "Healthcare Monitoring",
        "description": "Room occupancy detection (fall detection coming soon)",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">Healthcare</span> Monitor',
        "model_badge": "YOLOv8 · GPU",
        "doc_title": "Healthcare Monitor",
        "violation_types": {
            "overcrowding": {"label": "Overcrowding", "short_label": "OVERCROWDED", "icon": "🚪", "tone": "warn"},
        },
        "upload": {
            "badge": "YOLOv8 · TensorRT · Healthcare Monitoring",
            "description": "Upload room footage for occupancy monitoring.",
            "init_icon": "🏥",
            "init_title": "Initializing Healthcare Monitoring...",
            "init_subtitle": "Decoding video · Loading inference engine",
            "init_tags": ["YOLOv8", "ByteTrack", "Occupancy Logic"],
            "inference_step_title": "Inference",
            "inference_step_desc": "YOLOv8 detects and counts persons in the room.",
            "capabilities": [
                "✓ Room Occupancy Detection",
                "✓ Annotated Video Output",
                "⧗ Fall Detection (coming soon)",
            ],
        },
    }

    def __init__(self, rules_path=RULES_PATH):
        with open(rules_path) as f:
            self.rules = json.load(f)

        occ = self.rules["occupancy"]
        self.occupancy = OccupancyDebouncer(
            max_occupancy=occ["max_occupancy"],
            confirm_frames=occ["min_violation_frames"],
            clear_frames=occ["clear_after_frames"],
        )

        self.fall_detection_enabled = self.rules["fall_detection"]["enabled"]

    def get_class_names(self) -> list:
        return self.rules["class_names"]

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        """
        tracked_persons: ByteTracker output, same shape as PPE uses --
            [{"track_id": 1, "bbox": [...], "class": "person", ...}, ...]

        Returns a dict shaped like every other solution's smoothed status
        (id -> {violation, violation_type, bbox}), so EventManager /
        AlertEngine / DataManager work completely unchanged:

          - one entry per tracked person, always violation=False (they
            are drawn/visualized but are not themselves the violation --
            occupancy is a room-level condition, not a per-person one)
          - one synthetic "room_occupancy" entry when overcrowding is
            confirmed, carrying violation_type="overcrowding"
        """
        smoothed = {}

        for person in tracked_persons:
            pid = person["track_id"]
            smoothed[pid] = {
                "track_id": pid,
                "bbox": person["bbox"],
                "violation": False,
                "violation_type": None,
                "occluded": person.get("occluded", False),
            }

        overcrowded = self.occupancy.update(len(tracked_persons))

        if overcrowded:
            # Synthetic room-level entry -- bbox spans the union of all
            # tracked persons (or a small placeholder if somehow none),
            # so a screenshot crop has something meaningful to show.
            if tracked_persons:
                x1 = min(p["bbox"][0] for p in tracked_persons)
                y1 = min(p["bbox"][1] for p in tracked_persons)
                x2 = max(p["bbox"][2] for p in tracked_persons)
                y2 = max(p["bbox"][3] for p in tracked_persons)
                room_bbox = [x1, y1, x2, y2]
            else:
                room_bbox = [0, 0, 100, 100]

            smoothed["room_occupancy"] = {
                "track_id": "room_occupancy",
                "bbox": room_bbox,
                "violation": True,
                "violation_type": "overcrowding",
                "occluded": False,
            }

        # Fall detection intentionally NOT called -- see check_falls()
        # docstring. Left as an explicit no-op rather than silently
        # absent, so it's obvious in the code this is a known gap, not
        # an oversight.
        if self.fall_detection_enabled:
            fall_events = self.check_falls(all_detections)
            smoothed.update(fall_events)

        return smoothed

    def check_falls(self, all_detections: list) -> dict:
        """
        STUB -- plug-in point for pose-based fall detection.

        Intended approach once a pose-estimation backend exists:
          1. Run a pose model (YOLOv8-Pose or similar) instead of/
             alongside the plain object detector -- needs a NEW
             inference backend, since pose output shape (keypoints)
             is structurally different from this platform's current
             box-detection Detection dict shape.
          2. For each detected person's skeleton, compute torso angle
             relative to vertical (e.g. angle between shoulder-midpoint
             and hip-midpoint).
          3. Flag a fall when torso_angle exceeds
             rules.json's fall_detection.torso_angle_threshold_deg for
             fall_detection.min_violation_frames consecutive frames
             (same PresenceDebouncer-style confirm/clear pattern used
             elsewhere in this file and in driver_monitoring).

        No training data needed for this approach -- pretrained pose
        models already exist; only the geometric threshold needs tuning.
        """
        raise NotImplementedError(
            "Fall detection requires a pose-estimation backend, which "
            "does not exist in this platform yet. See this method's "
            "docstring for the intended implementation."
        )

    def get_stats(self, smoothed: dict) -> dict:
        # Exclude the synthetic "room_occupancy" entry from the person
        # count -- it's not a real tracked person.
        real_persons = {k: v for k, v in smoothed.items() if k != "room_occupancy"}
        persons = len(real_persons)
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": persons, "violations": violations}
