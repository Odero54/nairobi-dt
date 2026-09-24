"""Step 0: build the study area tiers. Run from the repository root."""

import json
import logging
import sys
from pathlib import Path

from nairobi_dt.study_area import run

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    report = run(root / "config" / "study_area.yaml", root)
    print(json.dumps(report["area_km2"], indent=2))
    print("Validation passed" if report["checks"]["passed"] else "Validation FAILED — open the map")
    sys.exit(0 if report["checks"]["passed"] else 1)
