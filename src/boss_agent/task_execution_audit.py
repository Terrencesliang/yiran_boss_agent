"""Persist structured task intake before candidate work and record its outcome."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from boss_agent.task_validator import RecruitmentTaskBlocked


class TaskExecutionAudit:
    def __init__(self, entry: str, request: str | None, task_file: str | None = None):
        root = Path(os.environ.get("BOSS_RECRUITMENT_AUDIT_DIR", "reports/recruitment_runs"))
        root.mkdir(parents=True, exist_ok=True)
        run_id = uuid4().hex
        self.path = root / f"{run_id}.json"
        self.record = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "entry": entry,
            "input_kind": "explicit_task_file" if task_file else "natural_language",
            "parser_kind": None if task_file else "deterministic",
            "request": request,
            "task_file": task_file,
            "blockers": [],
            "status": "started",
        }

    def __enter__(self):
        self.update()
        return self

    def update(self, **fields):
        self.record.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(self.record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def __exit__(self, error_type, error, traceback):
        if error is not None:
            blockers = self.record["blockers"]
            if isinstance(error, RecruitmentTaskBlocked):
                blockers = error.blockers
            if not blockers:
                blockers = [str(error)] if isinstance(error, (OSError, ValueError)) else [error_type.__name__]
            status = "failed" if self.record.get("status") == "prepared" else "blocked"
            self.update(status=status, blockers=blockers)
