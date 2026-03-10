import json
import os
from pathlib import Path

report_path = Path(os.getenv("PHASE10_REPORT", "tests/evals/phase10_report.json"))
min_pass = float(os.getenv("PHASE10_MIN_PASS", "0.95"))

if not report_path.exists():
    raise SystemExit(f"Report not found: {report_path}")

data = json.loads(report_path.read_text())

total = data.get("total", 0)
passed = data.get("passed", 0)
rate = (passed / total) if total else 0.0

print(f"Phase10 pass rate: {passed}/{total} = {rate:.2%}")
if rate < min_pass:
    raise SystemExit(f"FAIL: pass rate {rate:.2%} below threshold {min_pass:.2%}")
