"""
pipeline/capture.py

Moved unchanged from ppe_system/backend/pipeline/capture.py.
Tier-specific by nature (OpenCV VideoCapture) but requires zero changes.
"""

import cv2
import threading
import time

class CaptureThread:
    def __init__(self, source: str, reconnect_delay: float = 3.0):
        self.source          = source
        self.reconnect_delay = reconnect_delay
        self._frame          = None
        self._lock           = threading.Lock()
        self._running        = False
        self._thread         = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            # Wait meaningfully longer than one read()+release() cycle
            # should ever take. The old 3s cap could return "successfully"
            # while the background thread was still mid cap.release(),
            # so a same-device restart immediately after could race
            # against a device that wasn'''t actually free yet.
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                print(
                    f"[Capture] WARNING: capture thread for {self.source} "
                    f"did not stop within 8s -- device may still be held. "
                    f"A same-device restart right now could fail to open."
                )

    def get_frame(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def _run(self):
        while self._running:
            cap = cv2.VideoCapture(self.source)

            if not cap.isOpened():
                print(f"[Capture] Cannot open {self.source} — retrying in {self.reconnect_delay}s")
                time.sleep(self.reconnect_delay)
                continue

            print(f"[Capture] Connected to {self.source}")

            while self._running:
                ret, frame = cap.read()
                if not ret:
                    print("[Capture] Stream lost — reconnecting...")
                    break

                with self._lock:
                    self._frame = frame

            cap.release()
            if self._running:
                time.sleep(self.reconnect_delay)

        print("[Capture] Stopped")
