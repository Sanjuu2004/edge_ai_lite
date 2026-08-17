"""
platform_core/object_tracker.py

Moved unchanged from ppe_system/backend/pipeline/tracker.py.
Confirmed generic — only operates on "person" class detections and
generic bbox/IoU math, no PPE-specific field names. Genuinely shared
infrastructure, usable by any future solution (driver monitoring,
healthcare, etc.) without modification.
"""

import numpy as np

class ByteTracker:
    def __init__(self, max_lost=90, min_iou=0.15, min_hits=5):
        self._next_id  = 1
        self._tracks   = {}
        self._max_lost = max_lost
        self._min_iou  = min_iou
        # A track only counts as "real" (returned by _get_active / used for
        # PPE evaluation) once it has been matched for min_hits consecutive
        # frames. Filters one-off misclassifications (e.g. a chair briefly
        # detected as person for 1-2 frames) without touching confidence
        # thresholds. At target_fps=15, min_hits=5 is ~0.3s — negligible
        # added latency given TemporalSmoother already needs 40 frames
        # (~2.6s) before a violation is confirmed anyway.
        self._min_hits = min_hits

    def update(self, detections: list, frame: np.ndarray) -> list:
        persons = [d for d in detections if d["class"] == "person"]

        for tid in list(self._tracks):
            self._tracks[tid]["lost"] += 1
            if self._tracks[tid]["lost"] > self._max_lost:
                del self._tracks[tid]

        if not persons:
            return self._get_active()

        persons = sorted(persons,
                         key=lambda p: (p["bbox"][2]-p["bbox"][0])*(p["bbox"][3]-p["bbox"][1]),
                         reverse=True)

        matched   = {}
        used_dets = set()
        track_ids = list(self._tracks.keys())

        for tid in track_ids:
            tbbox      = self._tracks[tid]["bbox"]
            best_score = -1
            best_i     = None

            for i, p in enumerate(persons):
                if i in used_dets:
                    continue
                iou       = self._iou(tbbox, p["bbox"])
                centroid  = self._centroid_dist(tbbox, p["bbox"])
                th        = tbbox[3] - tbbox[1]
                norm_dist = centroid / max(th, 1)
                score     = iou - norm_dist * 0.3

                if score > best_score and (iou > self._min_iou or norm_dist < 0.5):
                    best_score = score
                    best_i     = i

            if best_i is not None:
                matched[tid] = best_i
                used_dets.add(best_i)

        for tid, det_i in matched.items():
            bbox = self._extend_bbox(persons[det_i]["bbox"], frame)
            prev_hits = self._tracks[tid].get("hits", 0)
            self._tracks[tid] = {
                "bbox":     bbox,
                "lost":     0,
                "conf":     persons[det_i]["conf"],
                "occluded": False,
                "hits":     prev_hits + 1,
            }

        for i, p in enumerate(persons):
            if i not in used_dets:
                bbox = self._extend_bbox(p["bbox"], frame)
                self._tracks[self._next_id] = {
                    "bbox":     bbox,
                    "lost":     0,
                    "conf":     p["conf"],
                    "occluded": False,
                    "hits":     1,
                }
                self._next_id += 1

        self._mark_occlusions()

        return self._get_active()

    def _mark_occlusions(self):
        track_list = [(tid, t) for tid, t in self._tracks.items() if t["lost"] == 0]

        for i in range(len(track_list)):
            tid_a, track_a = track_list[i]
            self._tracks[tid_a]["occluded"] = False

        for i in range(len(track_list)):
            tid_a, track_a = track_list[i]
            a_area = self._area(track_a["bbox"])

            for j in range(len(track_list)):
                if i == j:
                    continue
                tid_b, track_b = track_list[j]
                b_area = self._area(track_b["bbox"])

                inter  = self._intersection_area(track_a["bbox"], track_b["bbox"])
                if inter == 0:
                    continue

                smaller_area = min(a_area, b_area)
                occlusion_ratio = inter / max(smaller_area, 1)

                if occlusion_ratio > 0.35:
                    if a_area <= b_area:
                        self._tracks[tid_a]["occluded"] = True
                    else:
                        self._tracks[tid_b]["occluded"] = True

    def _extend_bbox(self, bbox, frame):
        x1, y1, x2, y2 = bbox
        h  = y2 - y1
        w  = x2 - x1
        fh = frame.shape[0] if frame is not None else 9999
        fw = frame.shape[1] if frame is not None else 9999
        y2 = min(y2 + h * 0.20, fh)
        y1 = max(y1 - h * 0.03, 0)
        x1 = max(x1 - w * 0.02, 0)
        x2 = min(x2 + w * 0.02, fw)
        return [x1, y1, x2, y2]

    def _get_active(self):
        results = []
        for tid, track in self._tracks.items():
            if track["lost"] == 0 and track.get("hits", 0) >= self._min_hits:
                results.append({
                    "track_id": tid,
                    "bbox":     track["bbox"],
                    "class":    "person",
                    "conf":     track.get("conf", 1.0),
                    "occluded": track.get("occluded", False)
                })
        return results

    def _area(self, bbox):
        return max(0, bbox[2]-bbox[0]) * max(0, bbox[3]-bbox[1])

    def _intersection_area(self, a, b):
        x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
        x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
        return max(0, x2-x1) * max(0, y2-y1)

    def _iou(self, a, b):
        inter = self._intersection_area(a, b)
        if inter == 0: return 0.0
        a1 = self._area(a)
        a2 = self._area(b)
        return inter / (a1 + a2 - inter)

    def _centroid_dist(self, a, b):
        ax = (a[0]+a[2])/2; ay = (a[1]+a[3])/2
        bx = (b[0]+b[2])/2; by = (b[1]+b[3])/2
        return ((ax-bx)**2 + (ay-by)**2)**0.5
