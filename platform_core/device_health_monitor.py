"""
platform_core/device_health_monitor.py

"Device Health Monitor — CPU / GPU / Memory / Temperature" from the
platform diagram.

Auto-detects whether jtop (Jetson-specific, confirmed available in this
environment via jetson-stats==4.3.2) is usable, and falls back to
psutil-only reporting (no GPU temp) on non-Jetson hardware — which is
exactly the Lite tier's target: a customer's plain laptop with no
jtop/Jetson tooling at all.
"""

import psutil
import shutil
import time

_start_time = time.time()

try:
    from jtop import jtop
    _JTOP_AVAILABLE = True
except ImportError:
    _JTOP_AVAILABLE = False


class DeviceHealthMonitor:
    def __init__(self):
        self.jtop_available = _JTOP_AVAILABLE

    def get_health(self):
        if self.jtop_available:
            try:
                return self._get_health_jtop()
            except Exception as e:
                print(f"[DeviceHealthMonitor] jtop read failed, falling back: {e}")
        return self._get_health_generic()

    def _get_health_jtop(self):
        with jtop() as jetson:
            if not jetson.ok():
                raise RuntimeError("jtop not ready")
            stats = jetson.stats
            return {
                "cpu_percent": stats.get("CPU1", 0),  # jtop exposes per-core;
                                                        # caller may want to average
                "gpu_percent": stats.get("GPU", None),
                "memory": self._memory_info(),
                "disk": self._disk_info(),
                "temperature_c": stats.get("Temp CPU", None),
                "uptime_seconds": time.time() - _start_time,
                "source": "jtop",
            }

    def _get_health_generic(self):
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "gpu_percent": None,   # no portable cross-vendor GPU reading
                                    # without vendor-specific tooling
            "memory": self._memory_info(),
            "disk": self._disk_info(),
            "temperature_c": self._try_psutil_temp(),
            "uptime_seconds": time.time() - _start_time,
            "source": "psutil",
        }

    def _memory_info(self):
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "used_gb": round(mem.used / (1024 ** 3), 2),
            "total_gb": round(mem.total / (1024 ** 3), 2),
        }

    def _disk_info(self, path="/"):
        total, used, free = shutil.disk_usage(path)
        return {
            "percent": round(used / total * 100, 1),
            "used_gb": round(used / (1024 ** 3), 2),
            "total_gb": round(total / (1024 ** 3), 2),
        }

    def _try_psutil_temp(self):
        # Only works on Linux with exposed thermal zones; returns None
        # cleanly on macOS/Windows rather than raising.
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            return None
        return None
