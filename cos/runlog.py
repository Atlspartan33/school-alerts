"""
Run log: one JSONL line per run with per-source status, so failures are
diagnosable after the fact. Local file only; in CI the same record goes
to stdout (GitHub Actions keeps the logs).
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

log = logging.getLogger("family-cos")


def record_run(pipeline: str, statuses: dict, **extra):
    """Append a structured run record. statuses: {source_name: 'ok'|'failed: ...'}"""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pipeline": pipeline,
        "sources": statuses,
        **extra,
    }
    line = json.dumps(record, default=str)

    if os.environ.get("GITHUB_ACTIONS"):
        print(f"RUN_LOG {line}")
        return

    try:
        os.makedirs(os.path.dirname(config.RUN_LOG_FILE), exist_ok=True)
        with open(config.RUN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning(f"Failed to write run log: {e}")
