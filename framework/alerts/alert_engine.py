"""
platform_core/alert_engine.py  (REVISED)

Fixed against the real APIs found in ppe_system/backend/alerts/*.py and
pipeline/event_manager.py:

  - EventManager.process(smoothed, annotated_frame) ALREADY saves the
    screenshot internally and returns alert dicts with a "screenshot"
    filename — AlertEngine does NOT save screenshots itself anymore.
  - ViolationGallery.capture(alert, frame) wants the RAW numpy frame
    (it crops from it directly), not JPEG bytes.
  - SpeakerAlert.alert(person_id, violation_type) has no camera_slot
    param. Known limitation carried over from the single-camera
    reference: if this Lite tier ever runs 2+ cameras, two different
    cameras' "person 1" would share the same cooldown key and could
    suppress each other's audio alerts. Flagged here, not silently
    hidden — fix mirrors what the DeepStream port already did (extend
    the cooldown key with slot_id) if/when multi-camera Lite is built.
"""

import time


class AlertEngine:
    def __init__(self, data_manager, mqtt=None, speaker=None, gallery=None):
        self.data_manager = data_manager
        self.mqtt = mqtt
        self.speaker = speaker
        self.gallery = gallery

    def dispatch(self, alert: dict, camera_slot, solution: str, frame=None):
        """
        alert: one dict as produced by EventManager.process(), e.g.
            {
                "person_id": 3,
                "violation_type": "no_helmet",
                "timestamp": 1234567.0,
                "bbox": [...],
                "helmet_frames": 41,
                "vest_frames": 0,
                "screenshot": "1234567_3_no_helmet.jpg",  # already saved
            }
        frame: the RAW frame (np.ndarray) at the moment of violation —
               needed by gallery.capture() for cropping. Pass the same
               frame you handed to EventManager.process(annotated_frame=...).
        """
        # NOTE: gallery.capture() intentionally removed here — EventManager
        # already saves the annotated screenshot (per-slot, DB-tracked via
        # screenshot_path) before dispatch() is even called. Calling gallery
        # here duplicated every violation image into temp/gallery/slotN/ as
        # a second, cropped, DB-untracked copy. self.gallery is left wired
        # in case a future feature wants it, just unused for now.
        if self.mqtt:
            try:
                self.mqtt.publish(alert)
            except Exception as e:
                print(f"[AlertEngine] MQTT publish failed: {e}")

        if self.speaker:
            try:
                # NOTE: no camera_slot arg — see module docstring limitation
                self.speaker.alert(alert["person_id"], alert["violation_type"])
            except Exception as e:
                print(f"[AlertEngine] speaker alert failed: {e}")

        self.data_manager.log_event(
            camera_slot=camera_slot,
            solution=solution,
            event_type=alert.get("violation_type", "unknown"),
            person_id=alert.get("person_id"),
            severity=None,   # solution's rules.json can map violation_type -> severity later
            screenshot_path=alert.get("screenshot"),
            timestamp=alert.get("timestamp", time.time()),
        )
