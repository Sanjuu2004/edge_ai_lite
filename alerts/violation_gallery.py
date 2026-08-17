"""alerts/violation_gallery.py — moved unchanged from ppe_system/backend/alerts/violation_gallery.py"""

import cv2
import os
import time
import base64
from collections import defaultdict

class ViolationGallery:
    def __init__(self, save_dir="temp/gallery", max_per_person=5):
        self.save_dir      = save_dir
        self.max_per_person = max_per_person
        self.gallery       = defaultdict(list)
        os.makedirs(save_dir, exist_ok=True)

    def capture(self, alert: dict, frame):
        if frame is None:
            return

        pid   = alert["person_id"]
        vtype = alert["violation_type"]
        ts    = int(alert["timestamp"])
        bbox  = alert.get("bbox")

        snapshot = self._crop(frame, bbox)

        fname = f"{self.save_dir}/pid{pid}_{vtype}_{ts}.jpg"
        cv2.imwrite(fname, snapshot)

        entry = {
            "person_id":      pid,
            "violation_type": vtype,
            "timestamp":      alert["timestamp"],
            "image_path":     fname,
        }

        self.gallery[pid].append(entry)

        if len(self.gallery[pid]) > self.max_per_person:
            old = self.gallery[pid].pop(0)
            if os.path.exists(old["image_path"]):
                os.remove(old["image_path"])

    def get_gallery(self):
        result = []
        for pid, entries in self.gallery.items():
            for e in entries:
                entry = dict(e)
                if os.path.exists(e["image_path"]):
                    with open(e["image_path"], "rb") as f:
                        entry["image_b64"] = base64.b64encode(f.read()).decode()
                else:
                    entry["image_b64"] = None
                result.append(entry)

        return sorted(result, key=lambda x: x["timestamp"], reverse=True)

    def clear(self):
        for pid, entries in self.gallery.items():
            for e in entries:
                if os.path.exists(e["image_path"]):
                    os.remove(e["image_path"])
        self.gallery.clear()

    def _crop(self, frame, bbox):
        h, w = frame.shape[:2]
        if bbox:
            x1,y1,x2,y2 = [int(v) for v in bbox]
            pad = 20
            x1  = max(0, x1-pad); y1 = max(0, y1-pad)
            x2  = min(w, x2+pad); y2 = min(h, y2+pad)
            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                th, tw = crop.shape[:2]
                if tw > 400:
                    scale = 400/tw
                    crop  = cv2.resize(crop, (400, int(th*scale)))
                return crop
        return cv2.resize(frame, (400, 225))
