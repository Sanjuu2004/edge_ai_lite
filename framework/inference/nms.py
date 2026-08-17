"""
core/nms.py

Extracted verbatim from ppe_system/backend/pipeline/inference.py's
YOLOInference._class_nms / ._iou.

Zero framework dependency (no torch, no ultralytics, no onnxruntime) —
pure Python + list math. This is why it moves to core/ unchanged and is
shared by both the Pro (DeepStream) and Lite (ONNX Runtime) tiers.
"""


def iou(a, b):
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    a1 = (a[2] - a[0]) * (a[3] - a[1])
    a2 = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (a1 + a2 - inter)


def class_nms(detections: list, iou_threshold: float = 0.4) -> list:
    """
    Apply NMS separately per class.
    Removes duplicate boxes of the same class overlapping the same object.
    Keeps the highest-confidence box when two same-class boxes overlap
    above iou_threshold.
    """
    if not detections:
        return []

    by_class = {}
    for d in detections:
        by_class.setdefault(d["class"], []).append(d)

    kept = []
    for cls, dets in by_class.items():
        dets = sorted(dets, key=lambda x: x["conf"], reverse=True)
        suppressed = [False] * len(dets)
        for i in range(len(dets)):
            if suppressed[i]:
                continue
            kept.append(dets[i])
            for j in range(i + 1, len(dets)):
                if suppressed[j]:
                    continue
                if iou(dets[i]["bbox"], dets[j]["bbox"]) > iou_threshold:
                    suppressed[j] = True
    return kept
