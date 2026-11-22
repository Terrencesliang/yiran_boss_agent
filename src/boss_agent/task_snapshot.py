"""Server-owned, transactional recruitment plans for the local workbench."""

from contextlib import contextmanager
from hashlib import sha256
import json
import re
import sqlite3
import time
from uuid import uuid4

from boss_agent.recommend_preview import load_preview_task
from boss_agent.task_parser import DEGREES, RECRUITMENT_PARSER_POLICY
from boss_agent.major_catalog import find_major, known_major_group
from boss_agent.task_schema import iter_criteria
from boss_agent.task_validator import task_blockers


PARSER_VERSION = RECRUITMENT_PARSER_POLICY + ":semantic-1"
SCHEMA_VERSION = 1
TTL = 3600
RUN_RETENTION = 30 * 86400


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(serialize(value).encode("utf-8")).hexdigest()


def source_digest(raw):
    return sha256(raw.encode("utf-8")).hexdigest()


def seal(row):
    changing = {"status", "confirmed_at", "snapshot_hash", "confirmation_hash"}
    return digest({key: value for key, value in row.items() if key not in changing})


class TaskSnapshots:
    def __init__(self, file):
        self.file = file
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, owner TEXT, tab TEXT, expires REAL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS tabs (owner TEXT, tab TEXT, revision INTEGER, PRIMARY KEY(owner, tab))")
            db.execute("CREATE TABLE IF NOT EXISTS submissions (plan TEXT PRIMARY KEY, run TEXT NOT NULL, claimed INTEGER NOT NULL)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.file, timeout=10)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def sync_jobs(self, options):
        names_by_id = {}
        unverifiable_names = set()
        for option in options:
            identity, name = option.get("jobId"), option.get("text")
            if not isinstance(name, str) or not name.strip():
                continue
            name = " ".join(name.split())
            if name == "全部职位":
                continue
            if isinstance(identity, str) and re.fullmatch(r"[\w.-]{1,128}", identity):
                names_by_id.setdefault(identity, []).append(name)
            else:
                unverifiable_names.add(name)
        unverifiable_names.update(name for names in names_by_id.values() if len(names) != 1 for name in names)
        version = uuid4().hex
        with self.transaction() as db:
            previous = {row["source_identity"]: row for row in self._jobs(db)}
            db.execute("DELETE FROM jobs")
            for identity, names in names_by_id.items():
                if len(names) != 1 or names[0] in unverifiable_names:
                    continue
                name = names[0]
                old = previous.get(identity)
                key = old["job_id"] if old and old["job_name"] == name else uuid4().hex
                record = dict(job_id=key, job_name=name, source="boss_recommend", source_identity=identity,
                              sync_version=version, updated_at=time.time())
                db.execute("INSERT INTO jobs VALUES (?, ?)", (key, serialize(record)))
            return self._jobs(db)

    @staticmethod
    def _jobs(db):
        return [json.loads(row[0]) for row in db.execute("SELECT record FROM jobs ORDER BY id")]

    def jobs(self):
        with self.transaction() as db:
            return self._jobs(db)

    @staticmethod
    def _job(db, key):
        row = db.execute("SELECT record FROM jobs WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ValueError("岗位身份无法核验，请重新同步并选择岗位")
        return json.loads(row[0])

    def job(self, key):
        with self.transaction() as db:
            return self._job(db, key)

    @staticmethod
    def _advance(db, owner, tab, generation):
        previous = db.execute("SELECT revision FROM tabs WHERE owner=? AND tab=?", (owner, tab)).fetchone()
        if previous and generation < previous[0]:
            raise ValueError("已有更新的招聘要求")
        db.execute("INSERT OR REPLACE INTO tabs VALUES (?, ?, ?)", (owner, tab, generation))

    def invalidate(self, owner, tab, generation):
        with self.transaction() as db:
            self._advance(db, owner, tab, generation)

    def draft(self, owner, tab, generation, job, raw, task, metadata, *, db=None, parent=None, override=None):
        if db is None:
            with self.transaction() as connection:
                return self.draft(owner, tab, generation, job, raw, task, metadata, db=connection,
                                  parent=parent, override=override)
        self._advance(db, owner, tab, generation)
        now = time.time()
        db.execute("DELETE FROM submissions WHERE plan IN (SELECT id FROM plans WHERE expires < ?)", (now - RUN_RETENTION,))
        db.execute("DELETE FROM plans WHERE expires < ? AND id NOT IN (SELECT plan FROM submissions)", (now - TTL,))
        current = self._job(db, job["job_id"])
        if current["source_identity"] != job["source_identity"] or task.job != current["job_name"]:
            raise ValueError("岗位身份已变化")
        wire = task.to_dict()
        row = dict(plan_id=uuid4().hex, owner=owner, tab=tab, generation=generation, job=current,
                   task=wire, task_hash=digest(wire), source_text=raw, source_hash=source_digest(raw),
                   parser_version=PARSER_VERSION, schema_version=SCHEMA_VERSION, status="draft",
                   snapshot_revision=parent["snapshot_revision"] + 1 if parent else 1,
                   parent_plan_id=parent["plan_id"] if parent else None, override=override,
                   created_at=now, confirmed_at=None, expires_at=now+TTL, metadata=metadata)
        row["snapshot_hash"] = seal(row)
        if not parent:
            existing = db.execute("SELECT record FROM plans WHERE owner=? AND tab=? AND expires>?", (owner, tab, time.time()))
            for record, in existing:
                previous = json.loads(record)
                keys = ("job", "source_hash", "task_hash", "generation", "status", "parser_version", "schema_version")
                if all(previous[key] == row[key] for key in keys):
                    return previous
                if previous["generation"] == generation and previous["status"] in {"draft", "confirmed"}:
                    raise ValueError("该页面版本已有规则，请重新解析")
        self._save(db, row)
        return row

    @staticmethod
    def _save(db, row):
        db.execute("INSERT OR REPLACE INTO plans VALUES (?, ?, ?, ?, ?)",
                   (row["plan_id"], row["owner"], row["tab"], row["expires_at"], serialize(row)))

    def read(self, db, owner, key, *, active=True):
        result = db.execute("SELECT record FROM plans WHERE id=? AND owner=?", (key, owner)).fetchone()
        if result is None:
            raise ValueError("规则快照不存在")
        row = json.loads(result[0])
        if (row["parser_version"] != PARSER_VERSION or row["schema_version"] != SCHEMA_VERSION
                or digest(row["task"]) != row["task_hash"] or source_digest(row["source_text"]) != row["source_hash"]
                or seal(row) != row["snapshot_hash"]):
            raise ValueError("规则快照完整性或版本验证失败")
        if row["status"] == "confirmed" or row["confirmed_at"] is not None:
            expected = digest([row["snapshot_hash"], row["confirmed_at"]])
            if row["confirmed_at"] is None or row.get("confirmation_hash") != expected:
                raise ValueError("规则确认记录完整性验证失败")
        if row["status"] == "revoked" or active and row["status"] == "superseded":
            raise ValueError("规则快照已撤销或替代")
        if active:
            if row["expires_at"] <= time.time():
                raise ValueError("规则快照已过期，请重新解析")
            revision = db.execute("SELECT revision FROM tabs WHERE owner=? AND tab=?", (owner, row["tab"])).fetchone()
            if not revision or revision[0] != row["generation"]:
                raise ValueError("规则快照已被新输入替代")
        job = self._job(db, row["job"]["job_id"])
        if job["source_identity"] != row["job"]["source_identity"] or job["job_name"] != row["task"]["job"]:
            raise ValueError("岗位身份已变化")
        return row

    @staticmethod
    def validate(row):
        task = load_preview_task(row["task"])
        blockers = task_blockers(task)
        if blockers:
            raise ValueError("；".join(blockers))
        if task.max_checks is not None and task.max_checks > 500:
            raise ValueError("检查上限不能超过 500")
        raw = row["source_text"]
        if row["metadata"].get("mode") != "human_override" and not anchors_complete(task, row["metadata"]):
            raise ValueError("条件原文依据不完整，请修改招聘原文后重新解析")
        for entry in row["metadata"].get("criteria_sources", []):
            for span in entry["sources"]:
                start, end = span["start"], span["end"]
                if not 0 <= start < end <= len(raw) or raw[start:end] != span["source_text"]:
                    raise ValueError("原文依据无法核验")
        return task

    def confirm(self, owner, key, task_hash):
        with self.transaction() as db:
            row = self.read(db, owner, key)
            if task_hash != row["task_hash"]:
                raise ValueError("任务哈希不一致")
            self.validate(row)
            if row["status"] == "draft":
                row.update(status="confirmed", confirmed_at=time.time())
                row["confirmation_hash"] = digest([row["snapshot_hash"], row["confirmed_at"]])
                self._save(db, row)
            return row

    def revoke(self, owner, key):
        with self.transaction() as db:
            row = self.read(db, owner, key)
            row["status"] = "revoked"
            self._save(db, row)

    def resize(self, owner, key, task_hash, max_checks):
        if type(max_checks) is not int or not 1 <= max_checks <= 500:
            raise ValueError("计划检查人数必须是 1 至 500 的整数")
        with self.transaction() as db:
            row = self.read(db, owner, key)
            if row["task_hash"] != task_hash:
                raise ValueError("任务哈希不一致")
            self.validate(row)
            if db.execute("SELECT 1 FROM submissions WHERE plan=?", (key,)).fetchone():
                raise ValueError("本轮筛选已提交，请从再筛一轮调整人数")
            if (row["task"]["max_checks"] or 20) == max_checks:
                return row
            # 计划检查人数下调时，自动联系目标不能超过它，否则会被阻断
            target = row["task"].get("target_success_count")
            if isinstance(target, int) and target > max_checks:
                target = max_checks
            task = load_preview_task({**row["task"], "max_checks": max_checks, "target_success_count": target})
            self.validate({**row, "task": task.to_dict()})
            row["status"] = "superseded"
            self._save(db, row)
            return self.draft(owner, row["tab"], row["generation"], row["job"], row["source_text"], task,
                              row["metadata"], db=db, parent=row, override={
                                  "actor": owner, "at": time.time(), "reason": "调整计划检查人数",
                                  "before_hash": row["task_hash"], "before": row["task"],
                              })

    def revise(self, owner, key, task_hash, hard, soft, reason):
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise ValueError("请输入修改原因")
        with self.transaction() as db:
            row = self.read(db, owner, key)
            if row["task_hash"] != task_hash:
                raise ValueError("任务哈希不一致")
            # Fixed request metadata and prior blockers are never editable.
            self.validate(row)
            if not all(isinstance(items, list) for items in (hard, soft)):
                raise ValueError("条件必须为列表")
            task = load_preview_task({**row["task"], "hard_filters": [human_condition(item) for item in hard],
                                      "soft_preferences": [human_condition(item) for item in soft]})
            for rule in (leaf for group in task.hard_filters + task.soft_preferences for leaf in iter_criteria(group)):
                self._validate_override(rule)
            metadata = {**row["metadata"], "mode": "human_override", "criteria_sources": [],
                        "previous_criteria_sources": row["metadata"].get("criteria_sources", [])}
            self.validate({**row, "task": task.to_dict(), "metadata": metadata})
            row["status"] = "superseded"
            self._save(db, row)
            return self.draft(owner, row["tab"], row["generation"], row["job"], row["source_text"], task,
                              metadata, db=db, parent=row, override={
                                  "actor": owner, "at": time.time(), "reason": reason.strip(),
                                  "before_hash": row["task_hash"], "before": row["task"],
                              })

    @staticmethod
    def _validate_override(rule):
        field, op, value = rule.field, rule.operator, rule.value
        values = value if isinstance(value, list) else [value]
        allowed = {
            "education_level": lambda: op in {"equals", "gte"} and value in DEGREES.values(),
            "education_mode": lambda: op == "equals" and value in {"full_time", "part_time"},
            "major": lambda: op in {"equals", "one_of"} and all(isinstance(item, str) and find_major(item) for item in values),
            "major_group": lambda: op == "equals" and isinstance(value, str) and known_major_group(value),
            "hands_on_tools": lambda: op == "has_any" and all(isinstance(item, str) for item in values),
            "job_related_requirement": lambda: op == "verify",
            "job_related_preference": lambda: op == "verify",
            "graduation_year": lambda: op in {"equals", "one_of"},
            "graduate_status": lambda: op == "equals" and value == "fresh_graduate",
            "skills": lambda: op in {"equals", "one_of", "has_any"} and all(isinstance(item, str) for item in values),
            "tools": lambda: op in {"equals", "one_of", "has_any"} and all(isinstance(item, str) for item in values),
        }
        if field in {"experience_months", "hr_experience_months"}:
            return  # load_preview_task validates operators and non-negative values.
        if field not in allowed or not allowed[field]():
            raise ValueError("人工修改包含不支持的条件")

    def reserve(self, owner, key, job_id, run_id):
        with self.transaction() as db:
            row = self.read(db, owner, key)
            if row["status"] != "confirmed" or row["job"]["job_id"] != job_id:
                raise ValueError("请选择已确认且属于该岗位的规则")
            self.validate(row)
            existing = db.execute("SELECT run FROM submissions WHERE plan=?", (key,)).fetchone()
            if existing:
                return existing[0], None
            db.execute("INSERT INTO submissions VALUES (?, ?, 0)", (key, run_id))
            return run_id, row

    def release(self, key, run_id):
        with self.transaction() as db:
            db.execute("DELETE FROM submissions WHERE plan=? AND run=? AND claimed=0", (key, run_id))

    def pending(self):
        with self.transaction() as db:
            return [(run, json.loads(record)) for run, record in db.execute(
                "SELECT s.run, p.record FROM submissions s JOIN plans p ON p.id=s.plan WHERE s.claimed=0")]

    def execution(self, owner, key, run_id, task_hash):
        with self.transaction() as db:
            row = self.read(db, owner, key, active=False)
            submission = db.execute("SELECT run, claimed FROM submissions WHERE plan=?", (key,)).fetchone()
            if not submission or submission[0] != run_id or row["task_hash"] != task_hash or row["confirmed_at"] is None:
                raise ValueError("队列与规则快照不一致")
            if submission[1]:
                raise ValueError("规则快照执行已被领取")
            task = self.validate(row)
            db.execute("UPDATE submissions SET claimed=1 WHERE plan=?", (key,))
            return row, task


def anchors_complete(task, metadata):
    count = sum(1 for condition in task.hard_filters + task.soft_preferences for _ in iter_criteria(condition))
    sources = metadata.get("criteria_sources", [])
    return count == len(sources) and all(entry.get("sources") for entry in sources)


def human_condition(condition, depth=0):
    if depth > 16 or not isinstance(condition, dict):
        raise ValueError("人工条件结构无效")
    if set(condition) == {"operator", "conditions"} and isinstance(condition["conditions"], list):
        return {"operator": condition["operator"],
                "conditions": [human_condition(child, depth + 1) for child in condition["conditions"]]}
    if set(condition) not in ({"field", "operator", "value"}, {"field", "operator", "value", "source"}):
        raise ValueError("人工条件结构无效")
    return {**condition, "source": "人工修订"}


def public_snapshot(row):
    return {key: row[key] for key in ("plan_id", "status", "task_hash", "snapshot_revision",
                                    "parser_version", "schema_version", "expires_at", "confirmed_at")}
