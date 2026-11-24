"""Session-bound review history and isolated, blind synthetic annotation."""

from collections import Counter
from datetime import datetime, timezone
from hmac import compare_digest
import json
import re
from uuid import uuid4

from boss_agent.task_snapshot import digest, serialize
from boss_agent.education_pipeline import FIELDS as EDUCATION_FIELDS, validate_value


STATUSES = {"PENDING", "IN_REVIEW", "CONFIRMED_MATCH", "CONFIRMED_NON_MATCH",
            "NEED_MORE_EVIDENCE", "UNRESOLVED_CONFLICT"}
COMPLETE = {"CONFIRMED_MATCH", "CONFIRMED_NON_MATCH"}
LABELS = {"PASS", "FAIL", "UNKNOWN", "CONFLICT"}
BUCKETS = ("ranked_candidates", "rejected_candidates", "review_candidates", "capture_failed_candidates")
EDUCATION_SUMMARY_FIELDS = ("education_level", "education_mode", "school", "major", "major_group")


def latest_education_summary(raw, events=()):
    groups = {}
    for fact in raw.get("facts", []):
        groups.setdefault(fact.get("record_id"), []).append(fact)
    overrides = {(event["record_id"], event["field"]): event for event in events}
    evidence = raw.get("evidence", {})

    def values(record_id, facts, field):
        event = overrides.get((record_id, field))
        if event and event["action"] != "revoke":
            return [event["value"]]
        return list(dict.fromkeys(str(fact["raw_value"]) for fact in facts
                                  if fact["field"] == field and fact.get("raw_value") is not None))

    def source_is_dom(fact):
        return any(evidence.get(ref, {}).get("source_type") == "DOM" for ref in fact.get("evidence_ids", []))

    def order(item):
        record_id, facts = item
        dates = []
        for field in ("graduation_year", "education_start_year"):
            candidates = values(record_id, facts, field)
            dates.append(max((int(year) for year in candidates if re.fullmatch(r"(?:19|20)\d{2}", year)), default=0))
        return (dates[0] or dates[1], dates[1], any(source_is_dom(fact) for fact in facts))

    record_id, facts = max(groups.items(), key=order, default=(None, []))
    summary = {field: values(record_id, facts, field) for field in EDUCATION_SUMMARY_FIELDS}
    dom_schools = {fact["raw_value"] for fact in facts if fact["field"] == "school"
                   and isinstance(fact.get("raw_value"), str) and source_is_dom(fact)}
    school_override = overrides.get((record_id, "school"), {})
    if len(dom_schools) == 1 and school_override.get("action") in {None, "revoke"}:
        summary["school"] = [next(iter(dom_schools))]
    return summary


def hr_review_hint(raw):
    if raw.get("resume_model_status") == "failed":
        return "AI 简历识别失败，暂未取得可核验的人力资源经历"
    experiences = raw.get("experiences", [])
    timeline = raw.get("hr_timeline", {})
    if not experiences:
        return "未找到可核算人力资源工作时长的任职记录"
    if timeline.get("confirmed_hr_days"):
        return "已识别部分人力资源经历，累计时长仍待核实"
    if any(row.get("hr_relevance") == "POSSIBLE" and row.get("start_date") and row.get("end_date")
           for row in experiences):
        return "已提取任职日期，职责是否属于人力资源待核实"
    return "缺少可核对的人力资源任职日期或职责"


class ReviewAccessError(ValueError):
    pass


class ReviewConflict(ValueError):
    pass


def flatten(criteria):
    for criterion in criteria:
        yield criterion
        yield from flatten(criterion.get("children", []))


def legacy_criteria(items, evidence, prefix="official"):
    result = []
    for i, item in enumerate(items, 1):
        key = f"{prefix}.{i}"
        refs = []
        for ref in item.get("evidence", []):
            start, end, quote = ref.get("start"), ref.get("end"), ref.get("quote")
            if type(start) is not int or type(end) is not int or not 0 <= start < end or not isinstance(quote, str) or not quote.strip() or len(quote) != end-start:
                continue
            identifier = "legacy-" + digest([ref.get("source"), start, end, quote])[:24]
            evidence[identifier] = dict(source_type=str(ref.get("source", "")).upper(), start=start, end=end,
                                        quote=quote, quality="legacy_report")
            refs.append(identifier)
        observed = [ref["value"] for ref in item.get("evidence", []) if ref.get("value") is not None]
        result.append({key_: value for key_, value in item.items() if key_ not in {"evidence", "children"}} | dict(
            criterion_id=key, observed=observed or None, evidence_ids=refs, rule_version="legacy-eligibility-v1",
            children=legacy_criteria(item.get("children", []), evidence, key)))
    return result


def risk_codes(official, shadow, evidence):
    codes = set(shadow.get("reason_codes", []))
    codes.update(code for criterion in flatten(shadow.get("criteria", [])) for code in criterion.get("reason_codes", []))
    def failed_conditions(items):
        for item in items:
            if item["status"] == "FAIL":
                yield item
                yield from failed_conditions(item.get("children", []))

    failed_fields = {item["field"] for item in failed_conditions(official)}
    hr = any(item["field"] == "hr_experience_months" for item in flatten(official))
    issues = set(evidence.get("issues", []))
    if "major_evidence_missing" in issues:
        codes.add("MAJOR_NOT_PROVIDED")
    if failed_fields & {"major", "major_group"}:
        codes.add("OFFICIAL_MAJOR_FAILED")
    if "hr_experience_months" in failed_fields:
        codes.add("OFFICIAL_HR_FAILED")
    if hr and evidence.get("hr_timeline", {}).get("has_unknown_upper_bound"):
        codes.add("HR_EXPERIENCE_UNKNOWN_UPPER_BOUND")
    if "evidence_conflict" in issues:
        codes.add("EVIDENCE_CONFLICT")
    return sorted(codes)


class ReviewStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.db = workspace.snapshots
        with self.db.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS candidate_reviews
                (run_id TEXT, candidate_id TEXT, reviewer TEXT, version INTEGER, record TEXT,
                 PRIMARY KEY(run_id, candidate_id, version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS education_reviews
                (run_id TEXT, candidate_id TEXT, version INTEGER, record TEXT,
                PRIMARY KEY(run_id, candidate_id, version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS study_annotations
                (study TEXT, case_id TEXT, reviewer TEXT, version INTEGER, record TEXT,
                 PRIMARY KEY(study, case_id, reviewer, version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS study_adjudications
                (study TEXT, case_id TEXT, version INTEGER, record TEXT, PRIMARY KEY(study, case_id, version))""")

    def report(self, owner, run_id):
        with self.workspace.lock:
            run = next((item for item in self.workspace.state["runs"] if item["id"] == run_id
                        and item["status"] == "completed" and item["action"] == "preview"
                        and item.get("_snapshot_execution", {}).get("owner") == owner), None)
        if run is None:
            raise ReviewAccessError("运行不存在或当前会话无权访问")
        path = self.workspace.directory / f"{run_id}-preview.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise ReviewAccessError("运行报告不可用") from error
        audit = report.get("snapshot_execution", {})
        if (audit.get("run_id") != run_id or audit.get("task_hash") != run.get("task_hash")
                or audit.get("plan_id") != run.get("plan_id") or audit.get("job_id") != run.get("job_id")
                or digest(report.get("task")) != audit.get("task_hash")):
            raise ReviewConflict("运行报告与快照不一致")
        return run, report

    @staticmethod
    def rows(report):
        shadow = report.get("shadow_evaluation", {})
        by_id = {row["candidate_id"]: row["shadow"] for row in shadow.get("rows", [])}
        number = 0
        for bucket in BUCKETS:
            for index, row in enumerate(report.get(bucket, []), 1):
                number += 1
                raw = row.get("candidate_evidence", {})
                profile = row.get("candidate_profile") or {}
                refs = dict(raw.get("evidence", {}))
                official = legacy_criteria(row.get("hard_filters", []), refs)
                preferences = legacy_criteria(
                    [item["finding"] for item in (row.get("score_breakdown") or {}).values()],
                    refs, prefix="preference",
                ) if raw.get("resume_model_status") else []
                alternative = by_id.get(row["candidate_id"]) or {}
                refs.update(alternative.get("evidence", {}))
                refs = {key: ref for key, ref in refs.items() if isinstance(ref.get("quote"), str)
                        and ref["quote"].strip() and type(ref.get("start")) is int and type(ref.get("end")) is int
                        and 0 <= ref["start"] < ref["end"] and len(ref["quote"]) == ref["end"]-ref["start"]}
                status = row["eligibility_status"]
                match_percent = row.get("match_percent")
                matched_conditions = row.get("matched_conditions")
                total_conditions = row.get("total_conditions")
                if match_percent is None:
                    states = [item.get("status") for item in row.get("hard_filters") or []]
                    states += [(entry.get("finding") or {}).get("status")
                               for entry in (row.get("score_breakdown") or {}).values()]
                    states = [state for state in states if state in {"PASS", "FAIL", "UNKNOWN"}]
                    if states:
                        total_conditions, matched_conditions = len(states), sum(1 for state in states if state == "PASS")
                        match_percent = round(100 * matched_conditions / total_conditions)
                preference_states = [item.get("status") for item in preferences]
                reason_codes = list(risk_codes(official, alternative, raw))
                if status != "FAIL" and preference_states:
                    if "FAIL" in preference_states:
                        reason_codes.append("PREFERENCE_FAILED")
                    elif "UNKNOWN" in preference_states:
                        reason_codes.append("PREFERENCE_UNVERIFIED")
                diff = bool(alternative and alternative["status"] != status)
                yield dict(candidate_id=row["candidate_id"] or f"unidentified-{bucket}-{index}",
                           candidate_name=profile.get("name"), candidate_number=number,
                           resume_sources=profile.get("resume_sources", {}),
                           education_summary=latest_education_summary(raw),
                           official_status=status, hard_filter_status=row.get("hard_filter_status"),
                           official_criteria=official, preference_criteria=preferences,
                           match_percent=match_percent, matched_conditions=matched_conditions,
                           total_conditions=total_conditions,
                           decision_mode=report.get("decision_mode") or ("strict_all" if report.get("strict_all_conditions") else None),
                           min_core_percent=report.get("min_core_percent"), min_bonus_count=report.get("min_bonus_count"),
                           core_pass=row.get("core_pass"), core_total=row.get("core_total"),
                           bonus_pass=row.get("bonus_pass"), bonus_total=row.get("bonus_total"),
                           strict_all_conditions=report.get("strict_all_conditions") is True,
                           official_score=row.get("score"), official_rank=row.get("rank"), official_preferences=row.get("score_breakdown"),
                           shadow=alternative or None, different=diff,
                           compatibility_different=bool(alternative and alternative["compatibility_status"] != status),
                           candidate_evidence=raw, evidence=refs, capture_reason=row.get("reason"),
                           review_reason=row.get("review_reason"),
                           hr_review_hint=hr_review_hint(raw),
                           reason_codes=reason_codes,
                           evidence_revision=digest([raw, official, alternative, refs]))

    def history(self, run, candidate=None):
        with self.db.transaction() as db:
            if candidate is None:
                rows = db.execute("SELECT record FROM candidate_reviews WHERE run_id=? ORDER BY version", (run,))
            else:
                rows = db.execute("SELECT record FROM candidate_reviews WHERE run_id=? AND candidate_id=? ORDER BY version DESC",
                                  (run, candidate))
            return [json.loads(record) for record, in rows]

    def read(self, owner, run_id, endpoint, candidate=None):
        run, report = self.report(owner, run_id)
        rows = list(self.rows(report))
        history = self.history(run_id, candidate)
        with self.db.transaction() as db:
            events = db.execute("SELECT record FROM education_reviews WHERE run_id=? ORDER BY version",
                                (run_id,)).fetchall()
        education_history = {}
        for event in events:
            record = json.loads(event[0])
            education_history.setdefault(record["candidate_id"], []).append(record)
        if endpoint == "reviews":
            return {"reviews": history}
        for row in rows:
            row_events = [event for event in education_history.get(row["candidate_id"], [])
                          if event["evidence_revision"] == row["evidence_revision"]]
            if not row_events:
                continue
            row["education_summary"] = latest_education_summary(row["candidate_evidence"], row_events)
        if candidate is not None:
            row = next((item for item in rows if item["candidate_id"] == candidate), None)
            if not row:
                raise ReviewAccessError("候选人不属于该运行")
            return {"candidate": row, "history": history, "task": report["task"],
                    "education_history": education_history.get(candidate, [])}
        latest = {item["candidate_id"]: item for item in history}
        summary = Counter(row["official_status"] for row in rows)
        def current_status(row):
            latest_row = latest.get(row["candidate_id"], {})
            return latest_row["review_status"] if latest_row.get("evidence_revision") == row["evidence_revision"] else "PENDING"

        reviews = Counter(map(current_status, rows))
        changed = sum(row["different"] for row in rows)
        compatible_changed = sum(row["compatibility_different"] for row in rows)
        if endpoint == "differences":
            rows = [row for row in rows if row["different"]]
        for row in rows:
            current = latest.get(row["candidate_id"], {})
            row.update(review_status=current_status(row), review_version=current.get("version", 0))
            row.pop("resume_sources")
            if endpoint != "differences":
                row.pop("evidence")
                row.pop("candidate_evidence")
                if row["shadow"]:
                    row["shadow"] = {k: v for k, v in row["shadow"].items() if k not in {"evidence", "education_alternatives"}}
        return {"rows": rows, "summary": dict(summary), "reviews": dict(reviews),
                "reviewed_count": sum(reviews[item] for item in COMPLETE), "different_count": changed,
                "compatibility_different_count": compatible_changed, "checked": report["summary"]["checked"],
                "job": report["job"], "screened_at": run.get("finished") or run.get("created"),
                "snapshot": report["snapshot_execution"],
                "official_engine": report.get("shadow_evaluation", {}).get("official_rule_version", "legacy-eligibility-v1"),
                "shadow_engine": report.get("shadow_evaluation", {}).get("shadow_rule_version"),
                "strict_all_conditions": report.get("strict_all_conditions") is True}

    def write_education(self, owner, run_id, payload):
        allowed = {"candidate_id", "record_id", "field", "action", "value", "reason", "evidence_ids",
                   "expected_version", "evidence_revision"}
        if not isinstance(payload, dict) or set(payload) != allowed:
            raise ValueError("教育复核请求无效")
        _, report = self.report(owner, run_id)
        row = next((row for row in self.rows(report) if row["candidate_id"] == payload["candidate_id"]), None)
        if row is None:
            raise ReviewAccessError("候选人不属于该运行")
        if row["evidence_revision"] != payload["evidence_revision"]:
            raise ReviewConflict("教育证据已更新，请重新打开")
        raw = row["candidate_evidence"]
        record = next((record for record in raw.get("education_records", [])
                       if record["record_id"] == payload["record_id"]), None)
        field, action = payload["field"], payload["action"]
        if record is None or field not in EDUCATION_FIELDS or action not in {"confirm", "correct", "revoke"}:
            raise ValueError("教育记录、字段或操作无效")
        facts = [fact for fact in raw.get("facts", []) if fact["fact_id"] in record["fact_ids"]]
        refs = validate_keys(payload["evidence_ids"], {ref for fact in facts for ref in fact["evidence_ids"] if ref in row["evidence"]})
        value = None if action == "revoke" else validate_value(field, payload["value"])
        if action != "revoke" and (not value or not refs):
            raise ValueError("请填写可校验的字段值并选择本段教育经历的原文")
        if action == "confirm" and not any(fact["field"] == field and set(refs).intersection(fact["evidence_ids"])
                                           and value in {fact["raw_value"], fact["normalized_value"]} for fact in facts):
            raise ValueError("人工确认需要选择已有字段值及其原文")
        reason = validate_reason(payload["reason"])
        with self.db.transaction() as db:
            events = [json.loads(event[0]) for event in db.execute(
                "SELECT record FROM education_reviews WHERE run_id=? AND candidate_id=? ORDER BY version",
                (run_id, row["candidate_id"]))]
            version = events[-1]["version"] if events else 0
            check_version(payload["expected_version"], version)
            latest = next((event for event in reversed(events) if event["record_id"] == record["record_id"]
                           and event["field"] == field and event["evidence_revision"] == row["evidence_revision"]), None)
            if action == "revoke" and (latest is None or latest["action"] == "revoke"):
                raise ValueError("此字段没有可撤销的人工复核")
            result = dict(version=version+1, candidate_id=row["candidate_id"], record_id=record["record_id"],
                          field=field, action=action, value=value, reason=reason, evidence_ids=refs,
                          evidence_revision=row["evidence_revision"], reviewed_at=now(), reviewer_id=owner)
            db.execute("INSERT INTO education_reviews VALUES (?,?,?,?)",
                       (run_id, row["candidate_id"], version+1, serialize(result)))
        return result

    def write(self, owner, run_id, payload):
        allowed = {"candidate_id", "expected_version", "evidence_revision", "review_status", "reason", "condition_ids", "evidence_ids"}
        if not isinstance(payload, dict) or set(payload) != allowed:
            raise ValueError("复核请求字段无效")
        _, report = self.report(owner, run_id)
        candidate = next((row for row in self.rows(report) if row["candidate_id"] == payload["candidate_id"]), None)
        if candidate is None:
            raise ReviewAccessError("候选人不属于该运行")
        if payload["evidence_revision"] != candidate["evidence_revision"]:
            raise ReviewConflict("证据版本已变化，请重新打开")
        status = payload["review_status"]
        reason = validate_reason(payload["reason"])
        if status not in STATUSES:
            raise ValueError("复核状态无效")
        refs = validate_keys(payload["evidence_ids"], candidate["evidence"])
        ids = validate_keys(payload["condition_ids"], {item["criterion_id"] for item in flatten(
            candidate["official_criteria"] + candidate["preference_criteria"] +
            (candidate["shadow"] or {}).get("criteria", []))})
        if status in COMPLETE and (not refs or candidate["official_status"] == "CAPTURE_FAILED"):
            raise ValueError("确定人工结论需要有效证据，采集失败时请补充证据")
        with self.db.transaction() as db:
            version = db.execute("SELECT COALESCE(MAX(version),0) FROM candidate_reviews WHERE run_id=? AND candidate_id=?",
                                 (run_id, payload["candidate_id"])).fetchone()[0]
            check_version(payload["expected_version"], version)
            audit = report["snapshot_execution"]
            result = dict(review_id=uuid4().hex, run_id=run_id, plan_id=audit["plan_id"], task_hash=audit["task_hash"],
                          candidate_id=payload["candidate_id"], evidence_revision=candidate["evidence_revision"],
                          engine_version=report.get("shadow_evaluation", {}).get("official_rule_version", "legacy-eligibility-v1"),
                          shadow_engine_version=report.get("shadow_evaluation", {}).get("shadow_rule_version"),
                          reviewer_id=owner, review_status=status, review_reason=reason, reviewed_at=now(),
                          condition_ids=ids, evidence_ids=refs, version=version+1)
            db.execute("INSERT INTO candidate_reviews VALUES (?, ?, ?, ?, ?)",
                       (run_id, payload["candidate_id"], owner, version+1, serialize(result)))
        return result


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_reason(reason):
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1500:
        raise ValueError("请填写复核理由")
    return reason.strip()


def validate_keys(keys, allowed):
    if not isinstance(keys, list) or len(keys) > 100 or any(not isinstance(item, str) or item not in allowed for item in keys):
        raise ValueError("引用不属于当前候选人与证据版本")
    return list(dict.fromkeys(keys))


def check_version(expected, actual):
    if type(expected) is not int or expected != actual:
        raise ReviewConflict("有更新的复核版本，请重新打开后再提交")


class BlindStudy:
    def __init__(self, store, fixtures, assessment, adjudication_key=None):
        definitions = json.loads(fixtures.read_text(encoding="utf-8"))
        results = json.loads(assessment.read_text(encoding="utf-8"))["rows"]
        identified = {item["case_id"]: item for item in results}
        if {case["id"] for case in definitions} != identified.keys():
            raise ValueError("合成评测集与结果版本不一致")
        self.study = digest([definitions, results])
        self.cases = {digest([self.study, case["id"]])[:32]: case for case in definitions}
        self.results = {key: identified[case["id"]] for key, case in self.cases.items()}
        self.db = store.db
        self.adjudication_key = adjudication_key

    @staticmethod
    def blind(case):
        def criteria(items, prefix):
            return [{**item, "criterion_id": f"{prefix}.{i}",
                     "children": criteria(item.get("conditions", []), f"{prefix}.{i}")}
                    for i, item in enumerate(items, 1)]
        refs = {}
        for source in ("dom", "ocr"):
            for line in re.finditer(r"[^\r\n]+", case.get(source, "")):
                identity = digest([case["id"], source, line.start(), line[0]])[:24]
                refs[identity] = dict(source_type=source.upper(), quote=line[0], start=line.start(),
                                      end=line.end(), quality="synthetic_fixture")
        return {"case_id": case["id"], "criteria": criteria(case["hard"], "hard"), "evidence": refs,
                "capture_note": ("合成样本身份核验失败，证据不可用" if case.get("invalidateIdentity")
                                 else "合成样本中的引用偏移损坏" if case.get("invalidateEvidence")
                                 else "合成样本采集存在截断" if case.get("warnings") else None),
                "evidence_usable": not (case.get("invalidateIdentity") or case.get("invalidateEvidence"))}

    def read(self, owner, case_id=None):
        with self.db.transaction() as db:
            if case_id is None:
                submitted = {row[0] for row in db.execute("SELECT case_id FROM study_annotations WHERE study=? AND reviewer=?",
                                                            (self.study, owner))}
                return {"cases": [{"case_id": f"样本 {i:02}", "key": key, "submitted": key in submitted}
                                  for i, key in enumerate(self.cases, 1)],
                        "differences": sum(result["legacy_status"] != result["shadow"]["status"] for result in self.results.values()),
                        "compatibility_differences": sum(result["legacy_status"] != result["shadow"]["compatibility_status"]
                                                         for result in self.results.values())}
            if case_id not in self.cases:
                raise ReviewAccessError("合成样本不可用")
            blind = self.blind(self.cases[case_id])
            # The descriptive fixture key often contains the expected answer.
            blind["case_id"] = f"样本 {list(self.cases).index(case_id)+1:02}"
            rows = [json.loads(record) for record, in db.execute(
                "SELECT record FROM study_annotations WHERE study=? AND case_id=? ORDER BY reviewer, version",
                (self.study, case_id))]
            adjudications = [json.loads(record) for record, in db.execute(
                "SELECT record FROM study_adjudications WHERE study=? AND case_id=? ORDER BY version DESC",
                (self.study, case_id))]
        if any(row["reviewer_id"] == owner for row in rows):
            disagreements = self.disagreements(rows)
            return {**blind, "comparison": self.results[case_id], "annotations": rows,
                    "disagreements": disagreements, "adjudications": adjudications}
        return blind

    @staticmethod
    def disagreements(rows):
        counts = {}
        for row in rows:
            for decision in row["decisions"]:
                counts.setdefault(decision["criterion_id"], set()).add(decision["status"])
        return sorted(key for key, labels in counts.items() if len(labels) > 1)

    def write(self, owner, case_id, endpoint, payload):
        if case_id not in self.cases:
            raise ReviewAccessError("合成样本不可用")
        blind = self.blind(self.cases[case_id])
        adjudicate = endpoint == "adjudicate"
        keys = {"expected_version", "decisions"} | ({"adjudication_key"} if adjudicate else set())
        if not isinstance(payload, dict) or set(payload) != keys or not isinstance(payload["decisions"], list):
            raise ValueError("标注请求无效")
        if adjudicate:
            key = payload["adjudication_key"]
            if not self.adjudication_key or not isinstance(key, str) or not compare_digest(
                self.adjudication_key.encode(), key.encode()
            ):
                raise ReviewAccessError("裁决需要授权")
        valid = {item["criterion_id"] for item in flatten(blind["criteria"]) if not item["children"]}
        decisions = []
        for item in payload["decisions"]:
            if not isinstance(item, dict) or set(item) != {"criterion_id", "status", "reason", "evidence_ids"}:
                raise ValueError("单项标注无效")
            if item["criterion_id"] not in valid or item["status"] not in LABELS:
                raise ValueError("标注条件或状态无效")
            refs = validate_keys(item["evidence_ids"], blind["evidence"])
            if item["status"] in {"PASS", "FAIL", "CONFLICT"} and (not refs or not blind["evidence_usable"]):
                raise ValueError("确定或冲突标注需要可用证据")
            if item["status"] == "CONFLICT" and len(refs) < 2:
                raise ValueError("冲突标注需要双方证据")
            decisions.append({**item, "reason": validate_reason(item["reason"]), "evidence_ids": refs})
        supplied = {item["criterion_id"] for item in decisions}
        if len(supplied) != len(decisions):
            raise ValueError("重复标注条件")
        with self.db.transaction() as db:
            records = [json.loads(record) for record, in db.execute(
                "SELECT record FROM study_annotations WHERE study=? AND case_id=?", (self.study, case_id))]
            if adjudicate:
                if not any(row["reviewer_id"] == owner for row in records):
                    raise ReviewAccessError("请先独立提交标注")
                if not supplied or not supplied <= set(self.disagreements(records)):
                    raise ValueError("裁决仅适用于已有独立分歧的条件")
                version = db.execute("SELECT COALESCE(MAX(version),0) FROM study_adjudications WHERE study=? AND case_id=?",
                                     (self.study, case_id)).fetchone()[0]
            else:
                if supplied != valid:
                    raise ValueError("请完成全部逐条件标注")
                if any(item["reviewer_id"] == owner for item in records):
                    raise ReviewConflict("盲标已封存，分歧可进入人工裁决")
                version = 0
            check_version(payload["expected_version"], version)
            result = dict(annotation_id=uuid4().hex, reviewer_id=owner, decisions=decisions, reviewed_at=now(),
                          version=version+1, kind="adjudication" if adjudicate else "blind")
            if adjudicate:
                db.execute("INSERT INTO study_adjudications VALUES (?, ?, ?, ?)",
                           (self.study, case_id, version+1, serialize(result)))
            else:
                db.execute("INSERT INTO study_annotations VALUES (?, ?, ?, ?, ?)",
                           (self.study, case_id, owner, version+1, serialize(result)))
        return result
