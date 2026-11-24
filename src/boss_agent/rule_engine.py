"""Evidence-based three-valued rules for recruitment previews."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from hashlib import sha256
from typing import Callable, Literal

from boss_agent.resume_structure import (
    EDUCATION_FIELDS, ResumeObservation,
    extract_resume_observations, split_resume_sources,
)
from boss_agent.major_catalog import CATALOG_VERSION, find_major, normalize_major_name
from boss_agent.task_parser import DEGREES, FIELDS, NUMBER, _number
from boss_agent.task_schema import LEGACY_EXPERIENCE_FIELDS, MULTI_VALUE_FIELDS, Condition, ConditionGroup, Criterion, iter_criteria
from boss_agent.task_validator import is_sensitive
from boss_agent.work_timeline import HR_DURATION_POLICY, WorkRecord, extract_work_records, measure_hr_days, measure_work_months


type Status = Literal["PASS", "FAIL", "UNKNOWN"]
type SemanticJudge = Callable[[Criterion, dict[str, str]], str]


@dataclass(frozen=True)
class Evidence:
    source: str
    quote: str
    start: int
    end: int
    anonymous_candidate_id: str | None = None
    field: str | None = None
    value: str | int | None = None
    date_precision: str | None = None
    conflict: bool = False
    capture_status: str | None = None
    record_id: str | None = None
    record_school: str | None = None
    taxonomy_version: str | None = None


@dataclass(frozen=True)
class Fact:
    value: str | int | None
    evidence: Evidence


@dataclass(frozen=True)
class Finding:
    field: str
    expected: object
    status: Status
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    children: list[Finding] = field(default_factory=list)
    method: str = "rule"

    def to_dict(self) -> dict:
        result = asdict(self)
        result["missing_evidence_reason"] = self.reason if self.status == "UNKNOWN" else None
        result["fields_used"] = sorted({item.field for item in self.evidence if item.field})
        result["children"] = [child.to_dict() for child in self.children]
        return result


@dataclass(frozen=True)
class ResumeEvidence:
    sources: dict[str, str]
    facts: dict[str, list[Fact]]
    work_records: list[WorkRecord] = field(default_factory=list)
    as_of: date = field(default_factory=date.today)
    observations: tuple[ResumeObservation, ...] = ()
    anonymous_candidate_id: str | None = None
    capture_status: str | None = None


DEGREE_RANK = {degree: rank for rank, degree in enumerate(DEGREES.values())}
LABELS = {
    **FIELDS, "毕业届别": "graduation_year", "学历": "education_level", "学习形式": "education_mode",
    "应届资格": "graduate_status", "实操工具": "hands_on_tools", "工作经验": "experience_months",
}


def _normalize(field: str, value: str) -> str | int | None:
    value = value.strip()
    if not value:
        return None
    if field == "education_level":
        return DEGREES.get(value)
    if field == "education_mode":
        return {"全日制": "full_time", "非全日制": "part_time"}.get(value)
    if field == "major":
        major = find_major(value)
        return major.name if major else value
    if field == "major_group":
        return normalize_major_name(value)
    if field == "graduate_status":
        return {"应届": "fresh_graduate", "应届生": "fresh_graduate", "非应届": "not_fresh_graduate"}.get(value)
    if field == "graduation_year":
        match = re.fullmatch(r"(\d{4}|\d{2})(?:年|届)?", value)
        return int(match[1]) + (2000 if len(match[1]) == 2 else 0) if match else None
    if field.startswith("experience_months") or field in LEGACY_EXPERIENCE_FIELDS.values():
        match = re.fullmatch(rf"({NUMBER})\s*(年|个?月)", value)
        return _number(match[1]) * (12 if match[2] == "年" else 1) if match else None
    return value


def _extract_line_facts(field: str, value: str, evidence: Evidence) -> list[Fact]:
    if field in MULTI_VALUE_FIELDS:
        if value.strip() in {"无", "没有"}:
            return [Fact(None, evidence)]
        values = re.split(r"[,，、;；]", value)
    else:
        values = [value]
    normalized = [_normalize(field, item) for item in values]
    return [Fact(item, evidence) for item in normalized if item is not None]


def extract_resume_evidence(sources: dict[str, str], *, as_of: date | None = None,
                            candidate_id: str | None = None, capture_status: str | None = None,
                            education_observations=None) -> ResumeEvidence:
    """Extract explicit, labeled observations with exact offsets in their source."""
    facts: dict[str, list[Fact]] = {}
    anonymous_id = sha256(candidate_id.encode()).hexdigest()[:16] if candidate_id else None
    for source, text in sources.items():
        for line in re.finditer(r"[^\r\n]+", text):
            match = re.fullmatch(r"\s*([^:：]+)[:：]\s*(.*?)\s*", line[0])
            if not match:
                continue
            label, value = match.groups()
            label = label.strip()
            field = LABELS.get(label)
            duration_scope = re.fullmatch(r"(.+?)(?:工作)?经验", label)
            if field is None and duration_scope:
                field = LEGACY_EXPERIENCE_FIELDS.get(duration_scope[1], f"experience_months:{duration_scope[1]}")
            if not field:
                continue
            if field in EDUCATION_FIELDS:
                continue
            evidence = Evidence(source, line[0], line.start(), line.end(), anonymous_id,
                                field, value, capture_status=capture_status)
            facts.setdefault(field, []).extend(_extract_line_facts(field, value, evidence))
    observations = tuple(extract_resume_observations(sources) if education_observations is None else education_observations)
    for item in observations:
        value = _normalize(item.field, item.value)
        if value is not None:
            fact = Fact(value, Evidence(item.source, item.quote, item.start, item.end, anonymous_id, item.field,
                                        value, item.date_precision, item.conflict, capture_status, item.record_id,
                                        item.record_school))
            if fact not in facts.setdefault(item.field, []):
                facts[item.field].append(fact)
            major = find_major(item.value) if item.field == "major" else None
            if major and major.group:
                group = Fact(major.group, replace(fact.evidence, field="major_group", value=major.group,
                                                  taxonomy_version=CATALOG_VERSION))
                facts.setdefault("major_group", []).append(group)
    return ResumeEvidence(dict(sources), facts, extract_work_records(sources), as_of or date.today(),
                          observations, anonymous_id, capture_status)


def _same(a: object, b: object) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return type(a) is type(b) and a == b


def _matches(rule: Criterion, value: str | int) -> bool | None:
    def matches(expected):
        if rule.field in {"major", "major_group"} and isinstance(expected, str):
            return _same(value, _normalize(rule.field, expected))
        return _same(value, expected)

    if rule.operator == "equals":
        return matches(rule.value)
    if rule.operator in ("one_of", "has_any"):
        return any(matches(item) for item in rule.value)
    if rule.operator == "between":
        return rule.value[0] <= value <= rule.value[1] if type(value) is int else None
    if rule.operator == "gt":
        return value > rule.value if type(value) is int else None
    if rule.operator in ("gte", "lte"):
        reference = rule.value
        if rule.field == "education_level":
            value = DEGREE_RANK.get(value)
            reference = DEGREE_RANK.get(reference)
        if type(value) is int and type(reference) is int:
            return value >= reference if rule.operator == "gte" else value <= reference
    return None


def _work_duration(rule: Criterion, resume: ResumeEvidence) -> Finding | None:
    field = rule.field
    if field == "hr_experience_months":
        return _hr_duration(rule, resume)
    legacy_scopes = {alias: scope for scope, alias in LEGACY_EXPERIENCE_FIELDS.items()}
    if field != "experience_months" and not field.startswith("experience_months:") and field not in legacy_scopes:
        return None
    scope = legacy_scopes.get(field, field.removeprefix("experience_months:") if ":" in field else "")
    prefix, separator, value = scope.partition(":")
    category = prefix if separator and prefix in {"role", "industry", "duty", "kind"} else None
    term = value if category else scope or None
    bounds = measure_work_months(resume.work_records, resume.as_of, category=category, term=term)
    if bounds is None:
        # Unclassified history cannot prove a scoped summary false.
        return None
    evidence = _work_evidence(resume, field, bounds.evidence, bounds.conflict)
    stated = resume.facts.get(field, [])
    if bounds.conflict:
        return Finding(field, rule.value, "UNKNOWN", "不同来源的同一工作记录日期冲突", evidence, method="timeline")
    if any(type(item.value) is int and (
        item.value < bounds.minimum or bounds.maximum is not None and item.value > bounds.maximum
    ) for item in stated):
        return Finding(field, rule.value, "UNKNOWN", "简历自述年限与工作区间冲突",
                       [*evidence, *[item.evidence for item in stated]], method="timeline")
    lo, hi = bounds.minimum, bounds.maximum
    status = "UNKNOWN"
    if rule.operator == "gt" and type(rule.value) is int:
        if hi is not None and hi <= rule.value:
            status = "FAIL"
        elif lo > rule.value:
            status = "PASS"
    elif rule.operator in {"gte", "lte", "between", "equals"}:
        target = rule.value
        limits = {
            "gte": lambda: (target, None),
            "lte": lambda: (0, target),
            "between": lambda: tuple(target),
            "equals": lambda: (target, target),
        }
        floor, ceiling = limits[rule.operator]()
        if type(floor) is int and (ceiling is None or type(ceiling) is int):
            if (ceiling is not None and lo > ceiling) or (hi is not None and hi < floor):
                status = "FAIL"
            elif lo >= floor and (ceiling is None or (hi is not None and hi <= ceiling)):
                status = "PASS"
    reason = f"可确认工作月数范围：{lo} 至 {hi if hi is not None else '上限未知'}"
    return Finding(field, rule.value, status, reason, evidence, method="timeline")


def _work_evidence(resume, field, observations, conflict):
    records = {record.evidence: record for record in resume.work_records}
    return [
        Evidence(item.source, item.quote, item.start, item.end, resume.anonymous_candidate_id, field,
                 f"{records[item].start or '?'} / {records[item].end or '?'}",
                 item.date_precision, conflict, resume.capture_status)
        for item in observations
    ]


def _hr_duration(rule: Criterion, resume: ResumeEvidence) -> Finding | None:
    bounds = measure_hr_days(resume.work_records, resume.as_of)
    if bounds is None:
        return None
    evidence = _work_evidence(resume, rule.field, bounds.evidence, bounds.conflict)
    lo, hi = bounds.minimum_days, bounds.maximum_days
    if bounds.conflict:
        return Finding(rule.field, rule.value, "UNKNOWN", "同一 HR 工作记录存在日期冲突", evidence, method=HR_DURATION_POLICY)
    stated = resume.facts.get(rule.field, [])
    if any(type(item.value) is int and (
        item.value == 0 and bounds.has_work or
        item.value > 0 and (hi is not None and hi < bounds.threshold(item.value - 1)[0]
                            or lo > bounds.threshold(item.value + 1)[1])
    ) for item in stated):
        return Finding(rule.field, rule.value, "UNKNOWN", "HR 自述与工作记录冲突",
                       [*evidence, *(item.evidence for item in stated)], method=HR_DURATION_POLICY)
    operator, target = rule.operator, rule.value
    if operator == "between":
        lower, upper = target
    else:
        lower = target if operator in {"equals", "gt", "gte"} else None
        upper = target if operator in {"equals", "lte"} else None
    tests = []
    if type(lower) is int:
        lower_min, lower_max = bounds.threshold(lower)
        step = int(operator == "gt")
        tests.append("PASS" if lo >= lower_max + step else
                     "FAIL" if hi is not None and hi < lower_min + step else "UNKNOWN")
    if type(upper) is int:
        upper_min, upper_max = bounds.threshold(upper)
        tests.append("PASS" if hi is not None and hi <= upper_min else "FAIL" if lo > upper_max else "UNKNOWN")
    status = combine_status("AND", tests) if tests else "UNKNOWN"
    return Finding(rule.field, target, status, f"可确认 HR 日数：{lo} 至 {hi if hi is not None else '上限未知'}",
                   evidence, method=HR_DURATION_POLICY)


def _deterministic(rule: Criterion, resume: ResumeEvidence) -> Finding:
    if rule.field == "resume_keywords":
        # 关键词匹配：用于「做过 RAG 项目」这类没有结构化字段的要求
        corpus = "\n".join(str(text or "") for text in (resume.sources or {}).values()).lower()
        keywords = [str(item) for item in (rule.value if isinstance(rule.value, list) else [rule.value]) if str(item).strip()]
        hits = [item for item in keywords if item.lower() in corpus]
        reason = (f"简历出现关键词：{'、'.join(hits)}" if hits
                  else f"简历未出现关键词：{'、'.join(keywords)}")
        return Finding(rule.field, rule.value, "PASS" if hits else "FAIL", reason, [])
    duration = _work_duration(rule, resume)
    if duration is not None:
        return duration
    field = rule.field
    facts = resume.facts.get(field, [])
    evidence = list(dict.fromkeys(item.evidence for item in facts))
    if any(item.conflict for item in evidence):
        return Finding(field, rule.value, "UNKNOWN", "来源之间存在冲突，需要核实", evidence)
    results = {_matches(rule, item.value) for item in facts if item.value is not None}
    explicit_absence = any(item.value is None for item in facts)
    if field in MULTI_VALUE_FIELDS:
        if explicit_absence and any(item.value is not None for item in facts):
            return Finding(rule.field, rule.value, "UNKNOWN", "简历同时声明有和无该类经历或资质", evidence)
        if explicit_absence:
            return Finding(rule.field, rule.value, "FAIL", "简历明确声明无该项信息", evidence)
        if True in results:
            return Finding(rule.field, rule.value, "PASS", "简历明确列出匹配项", evidence)
        return Finding(rule.field, rule.value, "UNKNOWN", "已列信息不足以证明具备或缺失该项", evidence)
    observed = {item.value for item in facts}
    if len(observed) > 1:
        return Finding(rule.field, rule.value, "UNKNOWN", "存在不同观测值，需要核实", evidence)
    if results == {True}:
        return Finding(rule.field, rule.value, "PASS", "明确观测值满足条件", evidence)
    if results == {False}:
        return Finding(rule.field, rule.value, "FAIL", "明确观测值不满足条件", evidence)
    return Finding(rule.field, rule.value, "UNKNOWN", "缺少可核实的明确观测值", evidence)


def scope_education_evidence(resume: ResumeEvidence, conditions: list[Condition]) -> ResumeEvidence:
    """When there are multiple records, select a single record for the education conjunction."""
    relevant = [condition for condition in conditions
                if all(rule.field in EDUCATION_FIELDS for rule in iter_criteria(condition))]
    if not relevant:
        return resume
    records_by_source = {}
    for name in EDUCATION_FIELDS:
        for fact in resume.facts.get(name, []):
            if fact.evidence.record_id:
                records_by_source.setdefault(fact.evidence.source, {})[fact.evidence.record_id] = fact.evidence.record_school
    schools = {school for records in records_by_source.values() for school in records.values() if school}
    if len(schools) <= 1 and not any(len(records) > 1 for records in records_by_source.values()):
        return resume
    record_groups = {}
    for records in records_by_source.values():
        for record, school in records.items():
            key = school if school and all(list(group.values()).count(school) <= 1
                                           for group in records_by_source.values()) else record
            record_groups.setdefault(key, set()).add(record)
    projections = [
        replace(resume, facts={
            name: [fact for fact in facts if fact.evidence.record_id in record_groups[key]]
            if name in EDUCATION_FIELDS else facts
            for name, facts in resume.facts.items()
        }) for key in sorted(record_groups)
    ]
    def priority(projected):
        statuses = [evaluate_condition(condition, projected).status for condition in relevant]
        overall = combine_status("AND", statuses)
        return {"FAIL": 0, "UNKNOWN": 1, "PASS": 2}[overall], statuses.count("PASS")

    return max(projections, key=priority)


def _read_model_finding(raw: str, provided: dict[str, tuple[str, int, str]]) -> tuple[Status, list[Evidence]]:
    data = json.loads(raw)
    if type(data) is not dict or set(data) != {"status", "evidence"} or data["status"] not in ("PASS", "FAIL", "UNKNOWN"):
        raise ValueError("invalid_model_output")
    if type(data["evidence"]) is not list:
        raise ValueError("invalid_model_evidence")
    evidence = []
    for item in data["evidence"]:
        if type(item) is not dict or set(item) != {"source", "quote"}:
            raise ValueError("invalid_model_evidence")
        key, quote = item["source"], item["quote"]
        if type(key) is not str or key not in provided or type(quote) is not str or not quote.strip():
            raise ValueError("invalid_model_evidence")
        source, offset, text = provided[key]
        position = text.find(quote)
        if position < 0:
            raise ValueError("unverifiable_model_evidence")
        evidence.append(Evidence(source, quote, offset + position, offset + position + len(quote)))
    if data["status"] != "UNKNOWN" and not evidence:
        raise ValueError("missing_model_evidence")
    return data["status"], evidence


def _semantic(rule: Criterion, resume: ResumeEvidence, judge: SemanticJudge | None) -> Finding:
    if judge is None or not any(resume.sources.values()):
        return Finding(rule.field, rule.value, "UNKNOWN", "缺少语义判断或简历证据")
    provided_batches = []
    for source, text in resume.sources.items():
        if not text.strip():
            continue
        if len(text) <= 8000:
            provided_batches.append({source: (source, 0, text)})
        else:
            blocks = split_resume_sources({source: text})
            items = [(f"{source}:{index}", (source, block.start, block.text))
                     for index, block in enumerate(blocks)]
            provided_batches.extend(dict(items[index:index + 2]) for index in range(0, len(items), 2))
    if len(provided_batches) > 12:
        return Finding(rule.field, rule.value, "UNKNOWN", "语义评估超过分块预算，需人工复核", method="llm")
    try:
        observations = [
            _read_model_finding(judge(rule, {key: value[2] for key, value in batch.items()}), batch)
            for batch in provided_batches
        ]
    except Exception:
        return Finding(rule.field, rule.value, "UNKNOWN", "模型失败、格式错误或引文无法核实", method="llm")
    evidence = list(dict.fromkeys(
        replace(item, anonymous_candidate_id=resume.anonymous_candidate_id, field=rule.field,
                capture_status=resume.capture_status)
        for _, items in observations for item in items
    ))
    decisions = {status for status, _ in observations} - {"UNKNOWN"}
    if len(decisions) > 1:
        return Finding(rule.field, rule.value, "UNKNOWN", "不同分块证据互相冲突", evidence, method="llm")
    status = next(iter(decisions), "UNKNOWN")
    if status == "FAIL" and "UNKNOWN" in {decision for decision, _ in observations}:
        status = "UNKNOWN"
    reason = "语义判断引用已核对的简历原文" if status != "UNKNOWN" else "现有证据不足以判定"
    return Finding(rule.field, rule.value, status, reason, evidence, method="llm")


def combine_status(operator: str, statuses: list[Status]) -> Status:
    if operator == "NOT" and len(statuses) == 1:
        return {"PASS": "FAIL", "FAIL": "PASS", "UNKNOWN": "UNKNOWN"}[statuses[0]]
    if operator == "AND":
        return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    if operator == "OR":
        return "PASS" if "PASS" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "FAIL"
    return "UNKNOWN"


def evaluate_condition(
    condition: Condition, resume: ResumeEvidence, judge: SemanticJudge | None = None,
    *, model_findings=None,
) -> Finding:
    if isinstance(condition, ConditionGroup):
        children = [evaluate_condition(child, resume, judge, model_findings=model_findings) for child in condition.conditions]
        status = combine_status(condition.operator, [child.status for child in children]) if children else "UNKNOWN"
        evidence = list(dict.fromkeys(item for child in children for item in child.evidence))
        return Finding(condition.operator, None, status, "按子条件的三值结果求值", evidence, children)
    if is_sensitive(condition):
        return Finding(condition.field, None, "UNKNOWN", "不适当条件已排除")
    if model_findings is not None:
        from boss_agent.resume_model import requirement_key

        return model_findings.get(requirement_key(condition)) or Finding(
            condition.field, condition.value, "UNKNOWN", "未取得可核验的 AI 判定", method="llm")
    return _semantic(condition, resume, judge) if condition.operator == "verify" else _deterministic(condition, resume)
