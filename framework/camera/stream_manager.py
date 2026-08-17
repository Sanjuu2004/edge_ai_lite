"""
platform_core/stream_manager.py

Owns one independent live-camera pipeline for the Lite tier:

    CaptureThread -> ONNXInference.infer() -> ByteTracker.update()
    -> solution.evaluate() -> EventManager.process() -> AlertEngine.dispatch()
    -> draw annotations -> JPEG encode -> exposed to API layer

Mirrors the shape of the DeepStream tier's StreamManager (get_latest_jpeg,
get_latest_stats, pop_new_alerts) so the api/ layer and frontend genuinely
don't need to know which tier is running underneath.

One instance per camera slot — same isolation reasoning as the DeepStream
build (independent tracker/smoother/event-manager state per camera, no
cross-camera track ID collisions).
"""

import threading
import time

import cv2
import numpy as np

from framework.camera.capture import CaptureThread
from framework.tracking.object_tracker import ByteTracker
from framework.events.event_manager import EventManager
from framework.inference.annotation import draw_annotations as _shared_draw_annotations


class StreamManager:
    def __init__(self, slot_id, inference_backend, solution, alert_engine,
                 device=None, cooldown_seconds=15, target_fps=15, screenshot_root=None):
        self.slot_id = slot_id
        self.inference_backend = inference_backend   # ONNXInference instance
        self.solution = solution                     # e.g. PPEIndustrialSolution instance
        self.alert_engine = alert_engine
        self.device = device
        self.target_fps = target_fps

        self.tracker = ByteTracker()

        # screenshot_root lets api/main.py namespace screenshots per
        # ACTIVE SOLUTION (screenshots/{solution_name}/slot{N}/) so
        # switching the platform-wide active solution never mixes
        # violation galleries between e.g. ppe_industrial and
        # driver_monitoring. Falls back to the old flat-per-slot path
        # if not provided, for any caller that hasn't been updated.
        root = screenshot_root or "screenshots"
        self.event_manager = EventManager(
            cooldown_seconds=cooldown_seconds,
            screenshot_dir=f"{root}/slot{slot_id}",
            screenshot_subdir=f"{root}/slot{slot_id}",
        )

        self.capture = None
        self.running = False
        self._thread = None

        self._lock = threading.Lock()
        self._latest_jpeg = None
        self._latest_stats = {"persons": 0, "violations": 0, "fps": 0}
        self._broadcast_queue = []

        self._frame_times = []

    def is_running(self):
        return self.running

    def start(self, device):
        if self.running:
            return
        self.device = device
        self.capture = CaptureThread(source=device)
        self.capture.start()

        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[StreamManager slot={self.slot_id}] Started on {device}")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self.capture:
            self.capture.stop()
            # Small settle delay -- gives the OS/driver a moment to fully
            # release the V4L2 device handle before this slot (or a
            # different solution's StreamManager, e.g. right after a
            # solution switch) tries to reopen the same physical camera.
            # Cheap insurance against the CaptureThread.stop() race
            # documented in pipeline/capture.py.
            time.sleep(0.5)
        with self._lock:
            self._latest_jpeg = None
        print(f"[StreamManager slot={self.slot_id}] Stopped")

    def get_latest_jpeg(self):
        with self._lock:
            return self._latest_jpeg

    def get_latest_stats(self):
        with self._lock:
            return dict(self._latest_stats)

    def pop_new_alerts(self):
        with self._lock:
            alerts, self._broadcast_queue = self._broadcast_queue, []
            return alerts

    def _run_loop(self):
        frame_interval = 1.0 / self.target_fps

        while self.running:
            loop_start = time.time()

            frame = self.capture.get_frame()
            if frame is None:
                time.sleep(0.05)
                continue

            self._process_frame(frame)

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _process_frame(self, frame: np.ndarray):
        detections = self.inference_backend.infer(frame)

        # Some solutions (driver_monitoring) have no person/driver class
        # at all -- a single fixed camera with one subject by construction
        # -- so running ByteTracker for them would be wasted compute and
        # produce nothing useful (it only ever matches class=="person").
        if getattr(self.solution, "requires_tracking", True):
            tracked_persons = self.tracker.update(detections, frame)
        else:
            tracked_persons = []

        smoothed = self.solution.evaluate(tracked_persons, detections)

        solution_stats = self.solution.get_stats(smoothed)
        annotated = _shared_draw_annotations(
            frame.copy(), smoothed,
            solution_stats["persons"], solution_stats["violations"],
            violation_types_manifest=getattr(self.solution, "manifest", {}).get("violation_types"),
        )

        alerts = self.event_manager.process(smoothed, annotated_frame=annotated)

        for alert in alerts:
            self.alert_engine.dispatch(
                alert,
                camera_slot=self.slot_id,
                solution=self.solution.name,
                frame=frame,   # raw frame, matches ViolationGallery.capture()'s expectation
            )

        persons = solution_stats["persons"]
        violations = solution_stats["violations"]
        fps = self._update_fps()

        ok, jpeg_bytes = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])

        with self._lock:
            if ok:
                self._latest_jpeg = jpeg_bytes.tobytes()
            self._latest_stats = {
                "persons": persons,
                "violations": violations,
                "fps": fps,
            }
            if alerts:
                self._broadcast_queue.extend(alerts)

    def _update_fps(self):
        now = time.time()
        self._frame_times.append(now)
        # keep last ~2 seconds of timestamps
        cutoff = now - 2.0
        self._frame_times = [t for t in self._frame_times if t >= cutoff]
        if len(self._frame_times) < 2:
            return 0
        span = self._frame_times[-1] - self._frame_times[0]
        return round((len(self._frame_times) - 1) / span, 1) if span > 0 else 0
