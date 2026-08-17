"""
apps/driver/main.py — Driver Monitoring product. Standalone, port 8004.
Run:
    uvicorn apps.driver.main:app --host 0.0.0.0 --port 8004 --reload
"""
import os
from framework.common.app_factory import create_app
from solutions.driver_monitoring.logic import DriverMonitoringSolution

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = create_app(
    solution_class=DriverMonitoringSolution,
    model_dir=os.path.join(BASE_DIR, "solutions", "driver_monitoring", "models"),
    frontend_dir=os.path.join(BASE_DIR, "framework", "dashboard"),
    data_root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    app_title="Driver Monitoring",
)
