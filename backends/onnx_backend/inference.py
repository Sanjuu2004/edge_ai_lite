"""
backends/onnx_backend/inference.py

Lite-tier inference backend. Calls onnxruntime.InferenceSession directly —
NOT via ultralytics.YOLO() — so there is no runtime auto-install behavior,
no implicit GPU assumption, and no PyTorch dependency at all. This is the
module that makes the Lite tier genuinely installable on a plain customer
laptop with `pip install onnxruntime opencv-python numpy`.

Drop-in replacement for pipeline/inference.py's YOLOInference:
same constructor shape, same .infer(frame) -> list[Detection] output.

Assumes the .onnx file was exported via standard `yolo export format=onnx`
(ultralytics), which bakes box-decode (DFL) and class-score sigmoid into
the graph already — so the raw output tensor is [1, 4+num_classes, N]
with (cx, cy, w, h) in *input* pixel space and already-sigmoid class scores.
No further anchor decoding needed.
"""

import time
import numpy as np
import cv2
import onnxruntime as ort

from core.nms import class_nms


class ONNXInference:
    def __init__(self, model_path="models/best.onnx", conf=0.7, imgsz=640,
                 class_names=None, providers=None, class_conf=None):
        self.conf = conf
        self.imgsz = imgsz
        # Optional per-class override, e.g. {"person": 0.75}. Falls back to
        # self.conf for any class not listed. person defaults higher than
        # helmet/vest since person false positives (chairs, mannequins,
        # shadows) are the costliest kind — they can trigger a full
        # violation alert on their own, while a missed helmet/vest is
        # caught by TemporalSmoother's majority vote over several frames.
        # No hardcoded per-class default here -- confidence tuning is
        # solution-specific knowledge (e.g. PPE needs a higher person
        # threshold to avoid chair/furniture false positives), so it's
        # passed in explicitly per-solution from api/main.py's
        # _build_inference_backend() instead of assumed platform-wide.
        self.class_conf = class_conf or {}

        if providers is None:
            providers = ["CPUExecutionProvider"]

        print(f"[ONNXInference] Loading {model_path} with providers={providers}")
        self.session = ort.InferenceSession(model_path, providers=providers)
        actual_providers = self.session.get_providers()
        print(f"[ONNXInference] Session providers in use: {actual_providers}")

        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

        self.class_names = class_names or self._load_class_names_from_metadata()
        if self.class_names is None:
            raise ValueError(
                "No class names found in ONNX metadata and none provided. "
                "Pass class_names=['helmet', 'person', 'vest'] explicitly."
            )

        print(f"[ONNXInference] Classes: {self.class_names}")
        print("[ONNXInference] Warming up...")
        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        for _ in range(3):
            self.infer(dummy)
        print("[ONNXInference] Ready!")

    def _load_class_names_from_metadata(self):
        try:
            meta = self.session.get_modelmeta().custom_metadata_map
            names_raw = meta.get("names")
            if names_raw:
                names_dict = eval(names_raw, {"__builtins__": {}})
                return [names_dict[i] for i in range(len(names_dict))]
        except Exception as e:
            print(f"[ONNXInference] Could not read class names from metadata: {e}")
        return None

    def _letterbox(self, frame, new_size=640, color=(114, 114, 114)):
        h, w = frame.shape[:2]
        scale = min(new_size / h, new_size / w)
        new_h, new_w = int(round(h * scale)), int(round(w * scale))

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        pad_h = new_size - new_h
        pad_w = new_size - new_w
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        padded = cv2.copyMakeBorder(
            resized, top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=color
        )
        return padded, scale, left, top

    def infer(self, frame: np.ndarray) -> list:
        orig_h, orig_w = frame.shape[:2]

        letterboxed, scale, pad_left, pad_top = self._letterbox(frame, self.imgsz)

        blob = cv2.cvtColor(letterboxed, cv2.COLOR_BGR2RGB)
        blob = blob.astype(np.float32) / 255.0
        blob = blob.transpose(2, 0, 1)
        blob = np.expand_dims(blob, axis=0)

        raw_output = self.session.run([self.output_name], {self.input_name: blob})[0]
        predictions = raw_output[0].transpose(1, 0)

        boxes_cxcywh = predictions[:, :4]
        class_scores = predictions[:, 4:]

        class_ids = np.argmax(class_scores, axis=1)
        confidences = np.max(class_scores, axis=1)

        # Per-class threshold instead of one global conf — see class_conf
        # docstring in __init__.
        thresholds = np.array([
            self.class_conf.get(self.class_names[c], self.conf) for c in class_ids
        ])
        keep = confidences >= thresholds
        boxes_cxcywh = boxes_cxcywh[keep]
        class_ids = class_ids[keep]
        confidences = confidences[keep]

        detections = []
        for (cx, cy, bw, bh), cls_id, conf in zip(boxes_cxcywh, class_ids, confidences):
            x1 = cx - bw / 2
            y1 = cy - bh / 2
            x2 = cx + bw / 2
            y2 = cy + bh / 2

            x1 = (x1 - pad_left) / scale
            y1 = (y1 - pad_top) / scale
            x2 = (x2 - pad_left) / scale
            y2 = (y2 - pad_top) / scale

            x1 = max(0.0, min(x1, orig_w))
            y1 = max(0.0, min(y1, orig_h))
            x2 = max(0.0, min(x2, orig_w))
            y2 = max(0.0, min(y2, orig_h))

            detections.append({
                "bbox": [x1, y1, x2, y2],
                "class": self.class_names[int(cls_id)],
                "conf": float(conf),
            })

        return class_nms(detections, iou_threshold=0.4)
