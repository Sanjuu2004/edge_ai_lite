"""
solutions/ppe_industrial/logic.py

Owns PPEAssociator and TemporalSmoother — both moved unchanged from
ppe_system/backend/pipeline/{ppe_logic.py, smoother.py}.

TemporalSmoother lives HERE, not in platform_core/, because it's
hardcoded to helmet/vest field names (status['helmet'], status['vest'],
self.helmet_buf, self.vest_buf) despite its generic-sounding name — a
future solution (driver monitoring, healthcare) would need its own
smoother variant with its own field names, not this one.

EventManager stays in platform_core/ and is NOT owned here — it's
genuinely generic (works off 'violation'/'violation_type' only) and is
called separately by stream_manager.py after this solution produces
smoothed per-person status.
"""

from collections import defaultdict, deque

from solutions.base_solution import BaseSolution


# ── PPEAssociator — moved unchanged from pipeline/ppe_logic.py ──────────
class PPEAssociator:
    def __init__(self, iou_threshold=0.1):
        self.iou_threshold = iou_threshold

    def associate(self, tracked_persons, all_detections):
        helmets = [d for d in all_detections if d['class'] == 'helmet']
        vests   = [d for d in all_detections if d['class'] == 'vest']
        results = {}

        helmet_owner = self._assign_ppe(helmets, tracked_persons, 'helmet')
        vest_owner   = self._assign_ppe(vests,   tracked_persons, 'vest')

        for person in tracked_persons:
            pid         = person['track_id']
            helmet_worn = pid in helmet_owner
            vest_worn   = pid in vest_owner

            results[pid] = {
                'track_id':       pid,
                'bbox':           person['bbox'],
                'helmet':         helmet_worn,
                'vest':           vest_worn,
                'occluded':       person.get('occluded', False),
                'violation':      not helmet_worn or not vest_worn,
                'violation_type': self._get_violation_type(helmet_worn, vest_worn)
            }
        return results

    def _get_head_percent(self, p_h: float) -> float:
        if p_h > 400:
            return 0.13
        elif p_h > 200:
            t = (p_h - 200) / 200
            return 0.17 - t * 0.04
        elif p_h > 100:
            t = (p_h - 100) / 100
            return 0.22 - t * 0.05
        else:
            return 0.28

    def _assign_ppe(self, items, persons, item_type):
        owners = set()
        if not items or not persons:
            return owners

        for item in items:
            ix1, iy1, ix2, iy2 = item['bbox']
            i_cx = (ix1 + ix2) / 2
            i_cy = (iy1 + iy2) / 2
            i_w  = ix2 - ix1
            i_h  = iy2 - iy1

            best_pid   = None
            best_score = -1

            for person in persons:
                pid  = person['track_id']
                px1, py1, px2, py2 = person['bbox']
                p_h  = py2 - py1
                p_w  = px2 - px1
                p_cx = (px1 + px2) / 2
                p_cy = (py1 + py2) / 2

                iou_full = self._iou(item['bbox'], [px1, py1, px2, py2])

                in_person = (px1 - p_w*0.15 <= i_cx <= px2 + p_w*0.15 and
                             py1 - p_h*0.10 <= i_cy <= py2 + p_h*0.10)

                h_overlap = max(0, min(ix2, px2) - max(ix1, px1))
                h_ratio   = h_overlap / max(p_w, 1)

                if p_h > 0:
                    rel_y = (i_cy - py1) / p_h
                else:
                    rel_y = 0.5

                if item_type == 'helmet':
                    head_pct = self._get_head_percent(p_h)
                    head_bottom = py1 + p_h * head_pct

                    if i_cy > head_bottom:
                        continue
                    if iy2 > head_bottom + p_h * 0.10:
                        continue
                    if iy1 > py1 + p_h * 0.35:
                        continue

                    v_score = max(0, 1 - rel_y * (1.0 / head_pct))

                else:  # vest
                    v_score = 1.0 - abs(rel_y - 0.45) * 2.0
                    v_score = max(0, v_score)

                    if iy2 < py1 + p_h * 0.10:
                        continue
                    if i_cy < py1 or i_cy > py2 + p_h * 0.1:
                        continue

                if item_type == 'helmet':
                    size_ok = 0.10 <= (i_w / max(p_w, 1)) <= 0.75
                else:
                    size_ok = 0.25 <= (i_w / max(p_w, 1)) <= 1.3

                dist       = ((i_cx-p_cx)**2 + (i_cy-p_cy)**2)**0.5
                norm_dist  = dist / max(p_h, 1)
                prox_score = max(0, 1 - norm_dist)

                score = (iou_full   * 3.0 +
                         h_ratio    * 1.5 +
                         v_score    * 1.0 +
                         prox_score * 0.8 +
                         (0.5 if in_person else 0) +
                         (0.3 if size_ok   else 0))

                if score > best_score:
                    best_score = score
                    best_pid   = pid

            if best_pid is not None and best_score > 1.0:
                owners.add(best_pid)

        return owners

    def _iou(self, box1, box2):
        x1 = max(box1[0], box2[0]); y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2]); y2 = min(box1[3], box2[3])
        inter = max(0, x2-x1) * max(0, y2-y1)
        if inter == 0: return 0.0
        a1 = (box1[2]-box1[0]) * (box1[3]-box1[1])
        a2 = (box2[2]-box2[0]) * (box2[3]-box2[1])
        return inter / (a1+a2-inter)

    def _get_violation_type(self, helmet, vest):
        if not helmet and not vest: return "no_helmet_no_vest"
        if not helmet:              return "no_helmet"
        if not vest:                return "no_vest"
        return None


# ── TemporalSmoother — moved unchanged from pipeline/smoother.py ────────
class TemporalSmoother:
    def __init__(self, buffer_size=7, min_violation_frames=40):
        self.buffer_size          = buffer_size
        self.min_violation_frames = min_violation_frames
        self._init_state()

    def _init_state(self):
        self.helmet_buf        = defaultdict(lambda: deque(maxlen=self.buffer_size))
        self.vest_buf          = defaultdict(lambda: deque(maxlen=self.buffer_size))
        self.violation_counter = defaultdict(lambda: defaultdict(int))
        self._prev_pids        = set()

    def reset(self):
        self._init_state()
        print("[Smoother] Reset — all counters cleared")

    def update(self, ppe_status: dict) -> dict:
        smoothed    = {}
        current_ids = set(ppe_status.keys())

        new_ids  = current_ids - self._prev_pids
        gone_ids = self._prev_pids - current_ids

        if len(new_ids) == 1 and len(gone_ids) == 1:
            new_pid  = list(new_ids)[0]
            gone_pid = list(gone_ids)[0]

            if gone_pid in self.violation_counter:
                self.violation_counter[new_pid]['helmet'] = \
                    self.violation_counter[gone_pid]['helmet']
                self.violation_counter[new_pid]['vest'] = \
                    self.violation_counter[gone_pid]['vest']
                self.helmet_buf[new_pid] = deque(
                    self.helmet_buf[gone_pid], maxlen=self.buffer_size)
                self.vest_buf[new_pid] = deque(
                    self.vest_buf[gone_pid], maxlen=self.buffer_size)

        self._prev_pids = current_ids

        for pid, status in ppe_status.items():
            occluded = status.get('occluded', False)

            self.helmet_buf[pid].append(int(status['helmet']))
            self.vest_buf[pid].append(int(status['vest']))

            buf_len     = len(self.helmet_buf[pid])
            helmet_vote = sum(self.helmet_buf[pid]) >= buf_len / 2
            vest_vote   = sum(self.vest_buf[pid])   >= buf_len / 2

            if not occluded:
                if not helmet_vote:
                    self.violation_counter[pid]['helmet'] += 1
                else:
                    self.violation_counter[pid]['helmet'] = 0

                if not vest_vote:
                    self.violation_counter[pid]['vest'] += 1
                else:
                    self.violation_counter[pid]['vest'] = 0

            helmet_confirmed = self.violation_counter[pid]['helmet'] >= self.min_violation_frames
            vest_confirmed = self.violation_counter[pid]['vest'] >= self.min_violation_frames

            smoothed[pid] = {
                **status,
                'helmet':           helmet_vote,
                'vest':             vest_vote,
                'helmet_confirmed': helmet_confirmed,
                'vest_confirmed':   vest_confirmed,
                'violation':        helmet_confirmed or vest_confirmed,
                'violation_type':   self._get_type(helmet_confirmed, vest_confirmed),
                'helmet_frames':    self.violation_counter[pid]['helmet'],
                'vest_frames':      self.violation_counter[pid]['vest'],
                'occluded':         occluded,
            }

        return smoothed

    def _get_type(self, h, v):
        if h and v:  return "no_helmet_no_vest"
        if h:        return "no_helmet"
        if v:        return "no_vest"
        return None


# ── The actual solution plug-in ──────────────────────────────────────────
class PPEIndustrialSolution(BaseSolution):
    name = "ppe_industrial"

    manifest = {
        "icon": "🛡️",
        "name": "PPE Industrial Safety",
        "description": "Helmet & vest compliance detection",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">PPE</span> Monitor',
        "model_badge": "YOLOv8 · GPU",
        "doc_title": "PPE Monitor",
        "violation_types": {
            "no_helmet_no_vest": {"label": "No Helmet + No Vest", "short_label": "NO HELMET+VEST", "icon": "🚫", "tone": "danger"},
            "no_helmet": {"label": "No Helmet", "short_label": "NO HELMET", "icon": "⛑️", "tone": "warn"},
            "no_vest": {"label": "No Vest", "short_label": "NO VEST", "icon": "🦺", "tone": "gold"},
        },
        "upload": {
            "badge": "YOLOv8 · TensorRT · PPE Industrial",
            "description": "Upload recorded footage for PPE compliance processing.",
            "init_icon": "🦺",
            "init_title": "Initializing PPE Detection...",
            "init_subtitle": "Decoding video · Loading inference engine",
            "init_tags": ["YOLOv8", "ByteTrack", "PPE Logic"],
            "inference_step_title": "Inference",
            "inference_step_desc": "YOLOv8 detects persons and PPE (helmet/vest).",
            "capabilities": [
                "✓ Person Detection",
                "✓ Helmet Compliance",
                "✓ Safety Vest Compliance",
                "✓ Violation Event Detection",
                "✓ Annotated Video Output",
            ],
        },
    }

    def __init__(self, iou_threshold=0.1, buffer_size=7, min_violation_frames=40):
        self.associator = PPEAssociator(iou_threshold=iou_threshold)
        self.smoother = TemporalSmoother(
            buffer_size=buffer_size,
            min_violation_frames=min_violation_frames,
        )

    def get_class_names(self) -> list:
        return ["helmet", "person", "vest"]

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        """
        Note: signature is wider than BaseSolution's abstract stub
        (tracked_persons AND all_detections, not just one list) —
        PPEAssociator genuinely needs both. base_solution.py's abstract
        contract will need a second look once a second solution
        (e.g. driver_monitoring, which may not need this split) is built,
        to confirm the shared shape actually generalizes.

        Returns SMOOTHED per-person status (not final alerts) —
        stream_manager.py passes this dict into platform_core's
        EventManager.process() separately to get the actual alert list.
        """
        ppe_status = self.associator.associate(tracked_persons, all_detections)
        smoothed = self.smoother.update(ppe_status)
        return smoothed
