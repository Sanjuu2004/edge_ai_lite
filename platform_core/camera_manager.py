"""
platform_core/camera_manager.py

"Camera Manager — Multi-camera Support" from the platform diagram.

Tier-agnostic on purpose: it only enumerates and validates devices,
it does not open capture streams itself (that's stream_manager's job,
which differs per tier — OpenCV VideoCapture for Lite, v4l2src/GStreamer
for Pro). Both tiers' stream_manager.py call into this same class.
"""

import glob
import subprocess


class CameraManager:
    def __init__(self, max_cameras=2):
        self.max_cameras = max_cameras

    def list_devices(self):
        """Returns only real video-capture devices, filtering out
        UVC metadata nodes (e.g. /dev/video1, /dev/video3 on this Jetson,
        which are paired-but-unusable metadata nodes next to the real
        /dev/video0, /dev/video2 capture devices)."""
        all_devices = sorted(glob.glob("/dev/video*"))
        return [d for d in all_devices if self._is_capture_device(d)]

    def _is_capture_device(self, path):
        try:
            out = subprocess.check_output(
                ["v4l2-ctl", "-d", path, "--info"],
                stderr=subprocess.DEVNULL, timeout=2,
            ).decode()
            # IMPORTANT: check within "Device Caps" specifically, not the whole
            # output. "Capabilities" lists everything the physical camera
            # supports across ALL its /dev/videoN nodes (e.g. a webcam that
            # exposes both a capture node and a metadata-only node) — so a
            # naive "Video Capture" in out check matches even on the
            # metadata-only node, since that section always lists the full
            # device capability, not this specific node's actual usable caps.
            device_caps_section = out.split("Device Caps")[-1]
            return "Video Capture" in device_caps_section
        except Exception:
            # v4l2-ctl may not exist on non-Linux dev machines; fall back
            # to "assume usable" so Lite tier still works on e.g. macOS/
            # Windows where this check isn't meaningful anyway.
            return True

    def assign_slots(self, requested_devices):
        """Maps a list of requested device paths to slot indices 0..N,
        enforcing max_cameras. Raises if over the limit."""
        if len(requested_devices) > self.max_cameras:
            raise ValueError(
                f"Requested {len(requested_devices)} cameras, "
                f"platform configured for max {self.max_cameras}."
            )
        return {i: dev for i, dev in enumerate(requested_devices)}
