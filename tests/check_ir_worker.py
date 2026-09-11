"""
Integration check of the stub script the API sends to the FreeCAD worker, run
through the worker's own execute_freecad_script() (preprocessor, freecadcmd,
output validation, drawing/PDF) - locally, without Docker, Redis or MQTT.

    python tests/check_ir_worker.py [--ir tests/fixtures/ir_cases/motor_mount_034.json]

Layout mirrors the container: <tolery-freecad>/storage/<user>/input/script.py so
the script's own `project_root = script_dir/../../..` resolves to the worker
repo root (which carries FreeCadUtil and sheetmetal).
"""
import argparse
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_ROOT = ROOT.parent / "tolery-freecad"
FREECAD_BIN = os.environ.get("FREECAD_BIN", r"C:\Program Files\FreeCAD 1.0\bin")


def _stub_modules():
    """The worker imports redis / mqtt at module level; none of that is needed here."""
    if "redis" not in sys.modules:
        try:
            import redis  # noqa: F401
        except ImportError:
            sys.modules["redis"] = types.SimpleNamespace(Redis=object)

    class _Mqtt:
        def publish_progress(self, *a, **k):
            return True

        def publish_status(self, *a, **k):
            return True

        def publish_outcome(self, *a, **k):
            return True

    sys.modules["mqtt_client"] = types.SimpleNamespace(get_mqtt_manager=lambda *a, **k: _Mqtt())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", default=str(ROOT / "tests/fixtures/ir_cases/motor_mount_034.json"))
    ap.add_argument("--user", default="irworker_%d" % int(time.time()))
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from src.core.ir_flow import make_stub_script, sanitize_title
    from src.core.ir_validate import validate

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    ir.pop("_expect", None)
    val = validate(ir)
    assert val.ok, (val.questions, val.errors)
    title = sanitize_title(ir.get("name") or val.plan.label)
    code = make_stub_script(val.ir, title, val.ir.get("material"))

    storage = WORKER_ROOT / "storage"
    user_dir = storage / args.user
    (user_dir / "input").mkdir(parents=True, exist_ok=True)
    (user_dir / "output").mkdir(parents=True, exist_ok=True)
    script_path = user_dir / "input" / "script.py"
    script_path.write_text(code, encoding="utf-8")

    os.environ["STORAGE_PATH"] = str(storage)
    os.environ["PATH"] = FREECAD_BIN + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _stub_modules()
    os.chdir(str(WORKER_ROOT))
    sys.path.insert(0, str(WORKER_ROOT))
    sys.path.insert(0, str(WORKER_ROOT / "src" / "utils"))   # technical_drawing_generator, as /app/src/utils in the container
    import script_preprocessor
    import worker

    # the API server pre-processes the uploaded script (title / output dir) before queuing it
    processed_path, pre = script_preprocessor.save_preprocessed_script(str(script_path), args.user, str(user_dir / "output"))
    print("preprocessor:", pre.method_used if hasattr(pre, "method_used") else "", getattr(pre, "changes", None))
    t0 = time.time()
    result = worker.execute_freecad_script(processed_path, args.user, str(user_dir / "output"))
    dt = time.time() - t0
    outcome_path = user_dir / "output" / "_job_outcome.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8")) if outcome_path.exists() else {}
    files = sorted(p.name for p in (user_dir / "output").iterdir())
    print("worker result status:", result.get("status"), "| code:", result.get("code"), "| %.1fs" % dt)
    print("outcome:", outcome.get("status"), outcome.get("code"), (outcome.get("message") or "")[:120])
    print("output files:", files)
    ok = outcome.get("status") in ("complete", "partial_success") and any(f.endswith(".step") for f in files) and any(f.endswith(".obj") for f in files)
    print("PDF drawing:", "yes" if any(f.endswith(".pdf") for f in files) else "no")
    print("RESULT:", "OK" if ok else "FAIL")
    if not ok:
        for log in (user_dir / "output").glob("_freecadcmd_std*"):
            print("----", log.name); print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
