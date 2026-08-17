"""
platform_core/event_manager.py

Moved unchanged from ppe_system/backend/pipeline/event_manager.py.
Confirmed generic — operates only on 'violation'/'violation_type' keys,
no PPE-specific field names. Genuinely shared infrastructure across
any future solution.

NOTE (kept, not silently fixed): this still saves screenshots directly
to a hardcoded SCREENSHOT_DIR="screenshots" via cv2.imwrite. That's a
minor infra coupling worth revisiting later (ideally the save path
should be injected, e.g. per camera_slot subfolder, matching the
DeepStream tier's screenshots/slot0/, slot1/ structure) — but kept
as-is for now to stay a faithful port and not introduce untested
behavior changes.
"""

import time
import os
import cv2
from collections import defaultdict

class EventManager:
    def __init__(self, cooldown_seconds=30, screenshot_dir="screenshots", screenshot_subdir=""):
        self.cooldown = cooldown_seconds
        self.last_alert = defaultdict(float)
        self.alerted_types = defaultdict(set)
        self.history = []

        # Injected per-slot save path (e.g. screenshot_dir="screenshots/slot0",
        # screenshot_subdir="slot0") — replaces the old hardcoded global
        # SCREENSHOT_DIR. screenshot_subdir is what gets stored as the
        # relative path in alert["screenshot"], so DataManager/the API can
        # build a URL against a single /screenshots static mount without
        # re-deriving the slot from anywhere else.
        self.screenshot_dir = screenshot_dir
        self.screenshot_subdir = screenshot_subdir
        os.makedirs(self.screenshot_dir, exist_ok=True)

    def process(self, smoothed_status: dict, annotated_frame=None) -> list:
        alerts = []
        now = time.time()

        for pid, status in smoothed_status.items():
            if not status.get("violation", False):
                self.alerted_types[pid].clear()
                self.last_alert.pop(pid, None)
                continue

            vtype = status.get("violation_type")
            if not vtype:
                continue

            if vtype in self.alerted_types[pid]:
                continue

            if self.last_alert[pid] != 0:
                if now - self.last_alert[pid] < self.cooldown:
                    if not (
                        vtype == "no_helmet_no_vest"
                        and "no_helmet_no_vest" not in self.alerted_types[pid]
                    ):
                        continue

            self.last_alert[pid] = now
            self.alerted_types[pid].add(vtype)

            screenshot_file = None
            if annotated_frame is not None:
                fname = f"{int(now)}_{pid}_{vtype}.jpg"
                fpath = os.path.join(self.screenshot_dir, fname)
                cv2.imwrite(fpath, annotated_frame)
                screenshot_file = f"{self.screenshot_subdir}/{fname}" if self.screenshot_subdir else fname

            alert = {
                "person_id": pid,
                "violation_type": vtype,
                "timestamp": now,
                "bbox": status["bbox"],
                "helmet_frames": status.get("helmet_frames", 0),
                "vest_frames": status.get("vest_frames", 0),
                "screenshot": screenshot_file,
            }

            alerts.append(alert)
            self.history.append(alert)

        return alerts

    def get_history(self):
        return sorted(self.history, key=lambda x: x["timestamp"], reverse=True)[:100]
