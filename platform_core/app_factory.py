"""
platform_core/app_factory.py

Builds a fully self-contained, single-solution FastAPI app. Each product
(apps/ppe/main.py, apps/driver/main.py, apps/healthcare/main.py) calls
create_app(...) with its own solution class + isolated data paths, gets
back a ready-to-run `app` object, and that's the entire entrypoint file.

This is the actual mechanism behind "three separate products sharing one
platform_core" -- there is exactly ONE copy of every route, every piece
of startup wiring, every cleanup task. Three products differ only by
which solution class and which paths they're constructed with, never by
duplicated code.
"""

import json
import os
import shutil
import time
import asyncio
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from framework.inference.backends.onnx_backend.inference import ONNXInference
from platform_core.data_manager import DataManager
from platform_core.alert_engine import AlertEngine
from framework.camera.stream_manager import StreamManager
from framework.camera.camera_manager import CameraManager
from platform_core.device_health_monitor import DeviceHealthMonitor
from platform_core.video_upload_processor import VideoUploadProcessor
from alerts.mqtt_publisher import MQTTPublisher
from alerts.speaker_alert import SpeakerAlert
from alerts.violation_gallery import ViolationGallery

MAX_SLOTS = 2
UPLOAD_RETENTION_SECONDS = 2 * 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
ACCEPTED_VIDEO_EXTENSIONS = {"mp4", "avi", "mov", "mkv", "webm"}


def _build_inference_backend(model_dir, class_conf=None):
    engine_path = os.path.join(model_dir, "best.engine")
    onnx_path = os.path.join(model_dir, "best.onnx")

    if not os.path.isfile(engine_path) and not os.path.isfile(onnx_path):
        raise RuntimeError(
            f"No model found for this solution. Add best.onnx or best.engine to {model_dir}."
        )

    if os.path.isfile(engine_path):
        try:
            from framework.inference.backends.tensorrt_backend.inference import TensorRTInference
            backend = TensorRTInference(engine_path=engine_path, conf=0.5, class_conf=class_conf)
            print(f"[app_factory] Using TensorRT backend (GPU) for {model_dir}")
            return backend
        except Exception as e:
            print(f"[app_factory] TensorRT unavailable ({e}), falling back to ONNX/CPU")
    else:
        print(f"[app_factory] No engine file at {engine_path}, using ONNX/CPU")

    backend = ONNXInference(model_path=onnx_path, conf=0.5, class_conf=class_conf)
    print(f"[app_factory] Using ONNX Runtime backend (CPU) for {model_dir}")
    return backend


def create_app(solution_class, model_dir, frontend_dir, data_root,
                app_title=None, mqtt_topic=None, class_conf=None):
    """
    solution_class: a BaseSolution subclass, e.g. PPEIndustrialSolution
    model_dir: folder containing best.onnx / best.engine for this solution
    frontend_dir: this product's own frontend/ folder (generic, manifest-driven)
    data_root: an isolated folder for this product's runtime state --
        data_root/data/platform.db, data_root/screenshots/, data_root/uploads/,
        data_root/outputs/, data_root/temp/gallery/ -- so running PPE,
        Driver, and Healthcare simultaneously on the same dev machine
        (for testing) never collides on files, DB rows, or ports.
    class_conf: optional per-class confidence override dict, e.g.
        {"person": 0.75} for PPE's chair-false-positive fix.
    """
    solution_name = solution_class.name
    manifest = getattr(solution_class, "manifest", {})

    DATA_DIR = os.path.join(data_root, "data")
    SCREENSHOTS_DIR = os.path.join(data_root, "screenshots")
    UPLOADS_DIR = os.path.join(data_root, "uploads")
    OUTPUTS_DIR = os.path.join(data_root, "outputs")
    GALLERY_ROOT = os.path.join(data_root, "temp", "gallery")

    for d in (DATA_DIR, SCREENSHOTS_DIR, UPLOADS_DIR, OUTPUTS_DIR, GALLERY_ROOT):
        os.makedirs(d, exist_ok=True)

    DB_PATH = os.path.join(DATA_DIR, "platform.db")

    app = FastAPI(title=app_title or f"{solution_name} Platform")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    _data_manager = DataManager(db_path=DB_PATH)
    _mqtt = MQTTPublisher(broker=os.getenv("MQTT_BROKER", "localhost"),
                           topic=mqtt_topic or f"{solution_name}/alerts",
                           client_id=f"edge_ai_{solution_name}")
    _speaker = SpeakerAlert(cooldown_seconds=15)
    _camera_manager = CameraManager(max_cameras=2)
    _health_monitor = DeviceHealthMonitor()

    _inference_backend = _build_inference_backend(model_dir, class_conf=class_conf)

    managers = {}
    for slot in range(MAX_SLOTS):
        solution = solution_class()
        gallery = ViolationGallery(save_dir=os.path.join(GALLERY_ROOT, f"slot{slot}"))
        alert_engine = AlertEngine(_data_manager, mqtt=_mqtt, speaker=_speaker, gallery=gallery)
        managers[slot] = StreamManager(
            slot_id=slot,
            inference_backend=_inference_backend,
            solution=solution,
            alert_engine=alert_engine,
            screenshot_root=SCREENSHOTS_DIR,
        )

    upload_jobs = {}

    class StartRequest(BaseModel):
        device: str

    # ── Cameras / manifest ──────────────────────────────────────────

    @app.get("/api/cameras")
    def list_cameras():
        return {"cameras": _camera_manager.list_devices()}

    @app.get("/api/solutions")
    def get_solution_info():
        # Kept for frontend backward-compat -- always returns exactly
        # one solution, since this app IS that solution.
        return {"available": [solution_name], "active": solution_name}

    @app.get("/api/solutions/manifest")
    def get_manifest():
        return {"active": solution_name, "manifest": manifest}

    # ── Live streams ─────────────────────────────────────────────────

    @app.post("/api/stream/{slot}/start")
    def start_stream(slot: int, req: StartRequest):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        try:
            managers[slot].start(req.device)
        except Exception as e:
            raise HTTPException(500, str(e))
        return {"status": "started", "slot": slot, "device": req.device}

    @app.post("/api/stream/{slot}/stop")
    def stop_stream(slot: int):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        managers[slot].stop()
        return {"status": "stopped", "slot": slot}

    @app.get("/api/stream/{slot}/status")
    def stream_status(slot: int):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        m = managers[slot]
        return {"running": m.is_running(), "device": m.device}

    @app.websocket("/ws/stream/{slot}")
    async def stream_ws(websocket: WebSocket, slot: int):
        await websocket.accept()
        if slot not in managers:
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid slot"}))
            await websocket.close()
            return

        m = managers[slot]
        try:
            while True:
                if not m.is_running():
                    await websocket.send_text(json.dumps(
                        {"type": "stats", "persons": 0, "violations": 0, "fps": 0, "alerts": []}
                    ))
                    await asyncio.sleep(0.2)
                    continue

                frame = m.get_latest_jpeg()
                if frame is not None:
                    await websocket.send_bytes(frame)

                stats = m.get_latest_stats()
                alerts = m.pop_new_alerts()
                await websocket.send_text(json.dumps({"type": "stats", **stats, "alerts": alerts}))
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass

    # ── Alerts / stats / health ─────────────────────────────────────

    @app.get("/api/alerts")
    def get_alerts():
        return _data_manager.get_recent_events(limit=100, solution=solution_name)

    @app.get("/api/stats")
    def get_stats():
        total = {"persons": 0, "violations": 0, "total_alerts": 0, "fps": 0}
        for m in managers.values():
            s = m.get_latest_stats()
            total["persons"] += s["persons"]
            total["violations"] += s["violations"]
            total["fps"] += s["fps"]
        counts = _data_manager.get_event_counts(solution=solution_name)
        total["total_alerts"] = sum(counts.values())
        return total

    @app.get("/api/health")
    def system_health():
        health = _health_monitor.get_health()
        health["streams"] = {
            str(slot): {"running": m.is_running(), "device": m.device, "stats": m.get_latest_stats()}
            for slot, m in managers.items()
        }
        health["active_streams"] = sum(1 for m in managers.values() if m.is_running())
        health["active_solution"] = solution_name
        health["tier"] = "lite"
        return health

    # ── Video upload ─────────────────────────────────────────────────

    @app.post("/api/upload")
    async def upload_video(file: UploadFile = File(...)):
        extension = (file.filename or "").rsplit(".", 1)[-1].lower()
        if extension not in ACCEPTED_VIDEO_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: .{extension}")

        job_id = uuid.uuid4().hex
        save_path = os.path.join(UPLOADS_DIR, f"{job_id}.{extension}")
        try:
            with open(save_path, "wb") as out_file:
                shutil.copyfileobj(file.file, out_file)
        except Exception as e:
            raise HTTPException(500, f"Failed to save upload: {e}")

        solution = solution_class()
        gallery = ViolationGallery(save_dir=os.path.join(GALLERY_ROOT, f"upload_{job_id}"))
        alert_engine = AlertEngine(_data_manager, mqtt=_mqtt, speaker=None, gallery=gallery)

        processor = VideoUploadProcessor(
            job_id=job_id,
            video_path=save_path,
            inference_backend=_inference_backend,
            solution=solution,
            alert_engine=alert_engine,
            screenshot_root=SCREENSHOTS_DIR,
        )
        upload_jobs[job_id] = processor
        processor.start()

        return {"job_id": job_id, "solution": solution_name}

    @app.websocket("/ws/process/{job_id}")
    async def process_ws(websocket: WebSocket, job_id: str):
        await websocket.accept()
        processor = upload_jobs.get(job_id)
        if processor is None:
            await websocket.send_text(json.dumps({"type": "error", "message": "Unknown job_id"}))
            await websocket.close()
            return

        try:
            while True:
                frame = processor.get_latest_jpeg()
                if frame is not None:
                    await websocket.send_bytes(frame)

                stats = processor.get_latest_stats()
                alerts = processor.pop_new_alerts()
                await websocket.send_text(json.dumps({"type": "stats", **stats, "alerts": alerts}))

                if processor.is_done() and processor.get_latest_stats().get("progress", 0) >= 100:
                    await asyncio.sleep(0.2)
                    break
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass

    @app.get("/api/download/{job_id}")
    def download_annotated_video(job_id: str):
        processor = upload_jobs.get(job_id)
        if processor is None:
            raise HTTPException(404, "Unknown job_id")
        if not processor.is_done():
            raise HTTPException(409, "Video is still processing.")
        if not os.path.isfile(processor.output_path):
            raise HTTPException(404, "Annotated video not found.")
        return FileResponse(processor.output_path, media_type="video/mp4",
                             filename=f"annotated_{job_id[:8]}.mp4")

    # ── Screenshots ──────────────────────────────────────────────────

    @app.get("/api/screenshots")
    def list_screenshots():
        events = _data_manager.get_recent_events(limit=500, solution=solution_name)
        result = []
        for e in events:
            path = e.get("screenshot_path")
            if not path:
                continue
            result.append({
                "filename": os.path.basename(path),
                "person_id": e.get("person_id"),
                "violation_type": e.get("event_type"),
                "timestamp": e.get("timestamp"),
                "url": f"/screenshots/{path}",
                "camera": e.get("camera_slot"),
            })
        return result

    @app.delete("/api/screenshots")
    def clear_screenshots():
        cleared_paths = _data_manager.clear_screenshot_paths()
        removed = 0
        for rel_path in cleared_paths:
            full_path = os.path.join(SCREENSHOTS_DIR, rel_path)
            if os.path.isfile(full_path):
                try:
                    os.remove(full_path)
                    removed += 1
                except OSError as e:
                    print(f"[app_factory] failed to remove {full_path}: {e}")
        return {"cleared": len(cleared_paths), "files_removed": removed}

    # ── Retention cleanup (uploads older than 2 days) ────────────────

    def _cleanup_old_upload_files():
        now = time.time()
        removed_files = removed_dirs = 0

        def _is_old(path):
            try:
                return (now - os.path.getmtime(path)) > UPLOAD_RETENTION_SECONDS
            except OSError:
                return False

        for directory in (UPLOADS_DIR, OUTPUTS_DIR):
            if not os.path.isdir(directory):
                continue
            for fname in os.listdir(directory):
                fpath = os.path.join(directory, fname)
                if os.path.isfile(fpath) and _is_old(fpath):
                    try:
                        os.remove(fpath)
                        removed_files += 1
                    except OSError as e:
                        print(f"[cleanup] failed to remove {fpath}: {e}")

        for root in (GALLERY_ROOT, SCREENSHOTS_DIR):
            if not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                if not entry.startswith("upload_"):
                    continue
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path) and _is_old(entry_path):
                    try:
                        shutil.rmtree(entry_path)
                        removed_dirs += 1
                    except OSError as e:
                        print(f"[cleanup] failed to remove {entry_path}: {e}")

        stale_ids = [
            jid for jid, p in upload_jobs.items()
            if _is_old(getattr(p, "video_path", "")) or not os.path.exists(getattr(p, "video_path", ""))
        ]
        for jid in stale_ids:
            upload_jobs.pop(jid, None)

        removed_events = _data_manager.delete_events_matching_camera_prefix("upload_")

        if removed_files or removed_dirs or removed_events:
            print(f"[cleanup] Removed {removed_files} file(s), {removed_dirs} folder(s), "
                  f"{removed_events} stale DB event(s)")

    async def _cleanup_loop():
        while True:
            try:
                _cleanup_old_upload_files()
            except Exception as e:
                print(f"[cleanup] unexpected error: {e}")
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    @app.on_event("startup")
    async def _start_cleanup_task():
        _cleanup_old_upload_files()
        asyncio.create_task(_cleanup_loop())

    # ── Static frontend ──────────────────────────────────────────────

    app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

    return app
