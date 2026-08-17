"""
backends/tensorrt_backend/inference.py

TensorRT inference backend for the Lite tier's GPU path. Same public
interface as backends/onnx_backend/inference.py's ONNXInference
(constructor shape, .infer(frame) -> list[Detection]) so stream_manager.py
and api/main.py can swap between them without any other code changing.

Handles the Ultralytics .engine file format directly: Ultralytics prepends
a length-prefixed JSON metadata blob (stride, imgsz, class names, etc.)
before the raw serialized TensorRT engine bytes -- a plain
runtime.deserialize_cuda_engine(f.read()) on the whole file fails with
"magic tag does not match" because it's handed the JSON header too. This
class strips that prefix first.

Uses cuda-python (the `cuda.cudart` module) for device memory management
instead of pycuda, since pycuda failed to build against this Jetson's
toolchain (missing/broken boost-python) while cuda-python installed
cleanly as a prebuilt wheel.
"""

import json
import struct
import time

import numpy as np
import cv2
import tensorrt as trt
from cuda import cudart

from core.nms import class_nms


def _cuda_check(err):
    if isinstance(err, tuple):
        err = err[0]
    if err != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"CUDA error: {err}")


class TensorRTInference:
    def __init__(self, engine_path, conf=0.5, imgsz=640,
                 class_names=None, class_conf=None):
        self.conf = conf
        self.imgsz = imgsz
        # See matching comment in backends/onnx_backend/inference.py --
        # no hardcoded per-class default; passed explicitly per-solution.
        self.class_conf = class_conf or {}

        print(f"[TensorRTInference] Loading {engine_path}")
        meta, engine_bytes = self._read_ultralytics_engine(engine_path)
        print(f"[TensorRTInference] Metadata: {meta}")

        self.class_names = class_names or self._names_from_meta(meta)
        if self.class_names is None:
            raise ValueError(
                "No class names found in engine metadata and none provided. "
                "Pass class_names=['helmet', 'person', 'vest'] explicitly."
            )
        print(f"[TensorRTInference] Classes: {self.class_names}")

        logger = trt.Logger(trt.Logger.WARNING)
        runtime = trt.Runtime(logger)
        self.engine = runtime.deserialize_cuda_engine(engine_bytes)
        if self.engine is None:
            raise RuntimeError(
                f"Failed to deserialize TensorRT engine at {engine_path}. "
                "This engine was likely built with a different TensorRT "
                "version than what's installed (check `dpkg -l | grep tensorrt` "
                "vs `python3 -c 'import tensorrt; print(tensorrt.__version__)'`) "
                "-- re-export with `yolo export ... format=engine` on this device."
            )
        self.context = self.engine.create_execution_context()

        self.input_name = None
        self.output_name = None
        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                self.input_name = name
                self.input_shape = tuple(self.engine.get_tensor_shape(name))
            else:
                self.output_name = name
                self.output_shape = tuple(self.engine.get_tensor_shape(name))

        if self.input_name is None or self.output_name is None:
            raise RuntimeError(
                f"Engine has unexpected I/O tensor layout: "
                f"input={self.input_name}, output={self.output_name}. "
                "Expected exactly one input tensor and one output tensor."
            )

        print(f"[TensorRTInference] input={self.input_name} {self.input_shape}, "
              f"output={self.output_name} {self.output_shape}")

        self._alloc_buffers()

        print("[TensorRTInference] Warming up...")
        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        for _ in range(3):
            self.infer(dummy)
        print("[TensorRTInference] Ready!")

    # -- setup helpers --------------------------------------------------

    def _read_ultralytics_engine(self, path):
        with open(path, "rb") as f:
            meta_len = struct.unpack("<I", f.read(4))[0]
            meta_json = f.read(meta_len).decode("utf-8")
            engine_bytes = f.read()
        meta = json.loads(meta_json)
        return meta, engine_bytes

    def _names_from_meta(self, meta):
        names_raw = meta.get("names")
        if not names_raw:
            return None
        return [names_raw[str(i)] for i in range(len(names_raw))]

    def _alloc_buffers(self):
        input_nbytes = int(np.prod(self.input_shape)) * np.dtype(np.float32).itemsize
        output_nbytes = int(np.prod(self.output_shape)) * np.dtype(np.float32).itemsize

        err, self.d_input = cudart.cudaMalloc(input_nbytes)
        _cuda_check(err)
        err, self.d_output = cudart.cudaMalloc(output_nbytes)
        _cuda_check(err)

        err, self.stream = cudart.cudaStreamCreate()
        _cuda_check(err)

        self.context.set_tensor_address(self.input_name, self.d_input)
        self.context.set_tensor_address(self.output_name, self.d_output)

        self._h_output = np.empty(self.output_shape, dtype=np.float32)

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
        blob = np.ascontiguousarray(np.expand_dims(blob, axis=0))

        err, = cudart.cudaMemcpyAsync(
            self.d_input, blob.ctypes.data, blob.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, self.stream,
        )
        _cuda_check(err)

        self.context.execute_async_v3(self.stream)

        err, = cudart.cudaMemcpyAsync(
            self._h_output.ctypes.data, self.d_output, self._h_output.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, self.stream,
        )
        _cuda_check(err)

        err, = cudart.cudaStreamSynchronize(self.stream)
        _cuda_check(err)

        predictions = self._h_output[0].transpose(1, 0)

        boxes_cxcywh = predictions[:, :4]
        class_scores = predictions[:, 4:]

        class_ids = np.argmax(class_scores, axis=1)
        confidences = np.max(class_scores, axis=1)

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

    def __del__(self):
        try:
            cudart.cudaFree(self.d_input)
            cudart.cudaFree(self.d_output)
            cudart.cudaStreamDestroy(self.stream)
        except Exception:
            pass
