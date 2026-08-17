"""
apps/healthcare/main.py — Healthcare Monitoring product. Standalone, port 8005.
Run:
    uvicorn apps.healthcare.main:app --host 0.0.0.0 --port 8005 --reload
"""
import os
from framework.common.app_factory import create_app
from applications.healthcare.logic import HealthcareMonitoringSolution

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = create_app(
    solution_class=HealthcareMonitoringSolution,
    model_dir=os.path.join(BASE_DIR, "solutions", "healthcare_monitoring", "models"),
    frontend_dir=os.path.join(BASE_DIR, "framework", "dashboard"),
    data_root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    app_title="Healthcare Monitoring",
)
