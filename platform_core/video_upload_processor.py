"""
platform_core/video_upload_processor.py

Offline video-file processing for the "Video Upload" page. Mirrors
StreamManager's per-frame pipeline (inference -> tracking (if the active
solution needs it) -> solution.evaluate() -> EventManager -> AlertEngine)
but reads sequential frames from a video FILE via cv2.VideoCapture in a
background thread, instead of a live camera device in a continuous loop.

Job-based rather than slot-based: one instance per upload, tracked by
job_id in api/main.py's upload_jobs dict, thrown away once done (unlike
StreamManager's persistent per-slot instances).

Runs at maximum processing speed (not throttled to the source video's
own FPS) since this is offline batch processing, not a live feed --
progress/total_frames let the frontend show a percentage instead.
"""

import os
import threading
import time

import cv2
import numpy as np

from platform_core.object_tracker import ByteTracker
from platform_core.event_manager import EventManager
from framework.inference.annotation import draw_annotations as _shared_draw_annotations


class VideoUploadProcessor:
    def __init__(self, job_id, video_path, inference_backend, solution,
                 alert_engine, screenshot_root, cooldown_seconds=15):
        self.job_id = job_id
        self.video_path = video_path
        self.inference_backend = inference_backend
        self.solution = solution
        self.alert_engine = alert_engine

        self.tracker = ByteTracker()
        self.event_manager = EventManager(
            cooldown_seconds=cooldown_seconds,
            screenshot_dir=f"{screenshot_root}/upload_{job_id}",
            screenshot_subdir=f"{screenshot_root}/upload_{job_id}",
        )

        # Annotated output video, written alongside the live JPEG stream
        # so the frontend's Download button has a real file once done.
        self.output_dir = os.path.join(os.path.dirname(video_path), "..", "outputs")
        self.output_dir = os.path.abspath(self.output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.output_path = os.path.join(self.output_dir, f"{job_id}_annotated.mp4")
        self._video_writer = None
        self._source_fps = 25.0  # overwritten from the source video once opened

        self.running = False
        self.done = False
        self.error = None
        self._thread = None

        self._lock = threading.Lock()
        self._latest_jpeg = None
        self._latest_stats = {"persons": 0, "violations": 0, "fps": 0,
                               "frame": 0, "total": 0, "progress": 0}
        self._broadcast_queue = []

        self._frame_times = []

    def start(self):
        if self.running or self.done:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_running(self):
        return self.running

    def is_done(self):
        return self.done

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

    def _run(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.error = f"Could not open video file: {self.video_path}"
            self.running = False
            self.done = True
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        source_fps = cap.get(cv2.CAP_PROP_FPS)
        if source_fps and source_fps > 1:
            self._source_fps = source_fps
        frame_index = 0

        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                frame_index += 1
                self._process_frame(frame, frame_index, total_frames)

        except Exception as e:
            self.error = str(e)
            print(f"[VideoUploadProcessor {self.job_id}] Error: {e}")
        finally:
            cap.release()
            if self._video_writer is not None:
                self._video_writer.release()
                self._video_writer = None
            self.running = False
            self.done = True
            with self._lock:
                self._latest_stats["progress"] = 100

    def _process_frame(self, frame: np.ndarray, frame_index: int, total_frames: int):
        detections = self.inference_backend.infer(frame)

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
                camera_slot=f"upload_{self.job_id}",
                solution=self.solution.name,
                frame=frame,
            )

        fps = self._update_fps()

        self._write_frame_to_video(annotated)

        ok, jpeg_bytes = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])

        progress = int((frame_index / total_frames) * 100) if total_frames > 0 else 0

        with self._lock:
            if ok:
                self._latest_jpeg = jpeg_bytes.tobytes()
            self._latest_stats = {
                "persons": solution_stats["persons"],
                "violations": solution_stats["violations"],
                "fps": fps,
                "frame": frame_index,
                "total": total_frames,
                "progress": progress,
            }
            if alerts:
                self._broadcast_queue.extend(alerts)

    def _write_frame_to_video(self, annotated_frame):
        try:
            if self._video_writer is None:
                h, w = annotated_frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._video_writer = cv2.VideoWriter(
                    self.output_path, fourcc, self._source_fps, (w, h)
                )
            self._video_writer.write(annotated_frame)
        except Exception as e:
            print(f"[VideoUploadProcessor {self.job_id}] video write failed: {e}")

    def _update_fps(self):
        now = time.time()
        self._frame_times.append(now)
        cutoff = now - 2.0
        self._frame_times = [t for t in self._frame_times if t >= cutoff]
        if len(self._frame_times) < 2:
            return 0
        span = self._frame_times[-1] - self._frame_times[0]
        return round((len(self._frame_times) - 1) / span, 1) if span > 0 else 0
