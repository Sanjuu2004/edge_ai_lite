"""
apps/ppe/main.py — PPE Industrial Safety product.
Standalone, port 8003. Shares platform_core/backends/core/alerts with the
other two products, but has zero code/data/runtime overlap with them.

Run:
    cd ~/ppe_platform_lite
    uvicorn apps.ppe.main:app --host 0.0.0.0 --port 8003 --reload
"""
import os
from framework.common.app_factory import create_app
from solutions.ppe_industrial.logic import PPEIndustrialSolution

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = create_app(
    solution_class=PPEIndustrialSolution,
    model_dir=os.path.join(BASE_DIR, "solutions", "ppe_industrial", "models"),
    frontend_dir=os.path.join(BASE_DIR, "framework", "dashboard"),
    data_root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    app_title="PPE Industrial Safety",
    class_conf={"person": 0.75},  # chair-false-positive fix, PPE-specific
)
