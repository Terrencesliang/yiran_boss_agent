"""Versioned offline-only eligibility diagnostics over Phase 4 evidence."""

from datetime import date
from hashlib import sha256
import re

from boss_agent.candidate_evidence import stable_id, hr_totals
from boss_agent.major_catalog import describe_major
from boss_agent.rule_engine import DEGREE_RANK, evaluate_condition
from boss_agent.task_parser import DEGREES
from boss_agent.task_schema import ConditionGroup, iter_criteria
from boss_agent.work_timeline import HRBounds


VERSION = "shadow-eligibility-v1"
LEGACY_VERSION = "legacy-eligibility-v1"
EDUCATION = {"education_level", "education_mode", "graduation_year", "major", "major_group"}
MAJOR = {"major", "major_group"}
COMPLETE_EDUCATION = re.compile(r"(?:教育经历完整|已完整列出全部教育经历)")
COMPLETE_WORK = re.compile(r"(?:工作及实习经历完整|已完整列出全部工作及实习经历)")
ZERO_HR = re.compile(r"(?:无|从未从事|没有)\s*(?:HR|人力资源|人事)(?:相关)?(?:工作|实习)?(?:经验|经历)|"
                     r"(?:HR|人力资源|人事)(?:相关)?经验\s*[:：]\s*0\s*(?:个月|月)", re.I)

REASONS = {
    "MAJOR_MATCHED": "同一适用教育记录的专业满足范围",
    "MAJOR_OUT_OF_SCOPE": "完整教育记录中的已映射专业均不在指定范围内",
    "MAJOR_NOT_PROVIDED": "当前适用教育记录缺少专业证据",
    "MAJOR_UNMAPPED": "专业原文无法可靠标准化",
    "MAJOR_SOURCE_CONFLICT": "同一教育记录的可靠专业证据存在冲突",
    "MAJOR_SCOPE_UNCLEAR": "岗位专业范围未配置，或现有记录无法可靠排除",
    "HR_EXPERIENCE_WITHIN_RANGE": "可靠的累计区间完全满足要求",
    "HR_EXPERIENCE_EXCEEDS_MAX": "确认下界已超过允许上限",
    "HR_EXPERIENCE_BELOW_MIN": "有依据的可能上界低于最低要求",
    "HR_EXPERIENCE_MISSING_EVIDENCE": "缺少能够证明 HR 经历及其范围的证据",
    "HR_EXPERIENCE_UNKNOWN_UPPER_BOUND": "可能累计时长没有可靠上界",
    "HR_EXPERIENCE_DATE_CONFLICT": "可关联工作记录存在实质性日期矛盾",
    "HR_RECORD_IDENTITY_UNRESOLVED": "跨来源工作记录可能重叠，但无法确认是同一次任职",
    "HR_RELEVANCE_UNCERTAIN": "职责、任职类型或区间不足以确定 HR 时长",
    "CAPTURE_INCOMPLETE": "详情采集不完整",
    "CAPTURE_IDENTITY_MISMATCH": "候选人与详情身份核验失败",
    "CAPTURE_FAILED": "候选人详情采集失败",
    "OCR_UNRELIABLE": "OCR 缺失、截断或质量不足",
    "EVIDENCE_REFERENCE_INVALID": "引文、偏移或源摘要无法核验",
    "EDUCATION_MATCHED": "适用教育记录符合条件",
    "EDUCATION_OUT_OF_SCOPE": "教育记录不符合条件",
    "EDUCATION_NOT_PROVIDED": "当前记录没有可靠的学历或学习形式证据",
    "EDUCATION_SOURCE_CONFLICT": "同一教育记录存在互斥的教育证据",
    "QUALIFICATION_EVIDENCE_MATCHED": "可追溯事实满足条件",
    "QUALIFICATION_EVIDENCE_EXCLUDES": "可追溯事实明确不满足条件",
    "QUALIFICATION_EVIDENCE_MISSING": "缺少支持该条件的确定性证据",
    "RULE_SCOPE_UNCLEAR": "当前没有对应的确定性规则或已确认范围",
    "GROUP_PASS": "条件组逻辑满足",
    "GROUP_FAIL": "条件组逻辑不满足",
    "GROUP_UNKNOWN": "条件组证据不足",
    "GROUP_CONFLICT": "条件组存在未解决的实质性冲突",
}


def combine(operator, statuses):
    if operator == "NOT" and len(statuses) == 1:
        return {"PASS": "FAIL", "FAIL": "PASS"}.get(statuses[0], statuses[0])
    if operator not in {"AND", "OR"} or not statuses:
        return "UNKNOWN"
    decisive, neutral = ("FAIL", "PASS") if operator == "AND" else ("PASS", "FAIL")
    if decisive in statuses:
        return decisive
    if all(status == neutral for status in statuses):
        return neutral
    return "CONFLICT" if "CONFLICT" in statuses else "UNKNOWN"


class EvidenceContext:
    def __init__(self, report, candidate):
        self.report = report
        self.candidate = candidate
        self.refs = dict(report.get("evidence", {}))
        self.sources = candidate.get("sources", {})
        self.manifest = {source["source_id"]: source for source in report.get("sources", [])}
        self.digests = {source: sha256(text.encode("utf-8")).hexdigest() for source, text in self.sources.items()}

    def verified(self, ids):
        result = []
        for key in dict.fromkeys(ids):
            ref = self.refs.get(key, {})
            source = self.manifest.get(ref.get("source_id"), {})
            kind = ref.get("source_type")
            text = self.sources.get(kind.lower()) if isinstance(kind, str) else None
            start, end = ref.get("start"), ref.get("end")
            if (text is None or source.get("source_type") != kind or source.get("characters") != len(text)
                    or source.get("redacted_sha256") != self.digests[kind.lower()]
                    or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or text[start:end] != ref.get("quote") or not text[start:end].strip()):
                return None
            if not str(ref.get("quality", "")).endswith("_partial"):
                result.append(key)
        return result

    def cited(self, source, start, end, quote):
        kind = source.upper()
        source_id = next((key for key, item in self.manifest.items() if item["source_type"] == kind), None)
        if not source_id:
            return None
        key = stable_id(source_id, start, end)
        quality = kind.lower() + "_text"
        if any(item.startswith(source) for item in self.report.get("capture_warnings", [])):
            quality += "_partial"
        self.refs.setdefault(key, dict(source_id=source_id, source_type=kind, start=start, end=end,
                                       quote=quote, quality=quality))
        return key

    def declaration(self, pattern):
        result = []
        for source, text in self.sources.items():
            for line in re.finditer(r"[^\r\n]+", text):
                if pattern.fullmatch(line[0].strip(" 。；;")):
                    if citation := self.cited(source, line.start(), line.end(), line[0]):
                        result.append(citation)
        return self.verified(result) or []


def _finding(condition, path, status, *codes, ids=(), observed=None, children=()):
    grouped = isinstance(condition, ConditionGroup)
    field = condition.operator if grouped else condition.field
    return dict(criterion_id=path, field=field, operator=condition.operator,
                expected=None if grouped else condition.value, requirement=None if grouped else condition.source,
                status=status, reason_codes=list(dict.fromkeys(codes)),
                reason="；".join(REASONS[code] for code in dict.fromkeys(codes)), observed=observed,
                evidence_ids=list(dict.fromkeys(ids)), children=list(children), rule_version=VERSION,
                requires_review=status in {"UNKNOWN", "CONFLICT"})


def _education(rule, path, context, facts):
    relevant = [fact for fact in facts if fact["field"] in MAJOR] if rule.field in MAJOR else [
        fact for fact in facts if fact["field"] == rule.field]
    base = "MAJOR" if rule.field in MAJOR else "EDUCATION"
    observations, ids = [], []
    for fact in relevant:
        verified = context.verified(fact["evidence_ids"])
        if verified is None or not fact["evidence_ids"]:
            return _finding(rule, path, "UNKNOWN", "EVIDENCE_REFERENCE_INVALID")
        if verified:
            observations.append(fact)
            ids.extend(verified)
    if not observations:
        return _finding(rule, path, "UNKNOWN", base + "_NOT_PROVIDED")
    conflict_values = {fact["normalized_value"] or fact["raw_value"] for fact in observations
                       if fact["extraction_status"] == "CONFLICT"}
    if len(conflict_values) > 1:
        return _finding(rule, path, "CONFLICT", base + "_SOURCE_CONFLICT", ids=ids, observed=observations)
    if any(fact["extraction_status"] == "CONFLICT" for fact in observations):
        code = "MAJOR_SCOPE_UNCLEAR" if base == "MAJOR" else "QUALIFICATION_EVIDENCE_MISSING"
        return _finding(rule, path, "UNKNOWN", code, ids=ids, observed=observations)
    if base == "MAJOR":
        if rule.operator not in {"equals", "one_of"}:
            return _finding(rule, path, "UNKNOWN", "MAJOR_SCOPE_UNCLEAR", ids=ids)
        attribute = "major_category" if rule.field == "major_group" else "normalized_major"
        expected = rule.value if isinstance(rule.value, list) else [rule.value]
        if not all(isinstance(item, str) for item in expected):
            return _finding(rule, path, "UNKNOWN", "MAJOR_SCOPE_UNCLEAR", ids=ids)
        accepted = {describe_major(value)[attribute] for value in expected}
        def observed_value(fact):
            # Business vocabulary is usable only when encoded in the confirmed rule.
            return None if fact["major"]["mapping_method"] == "business_alias" else fact["major"][attribute]

        observed = {observed_value(fact) for fact in observations}
        comparisons = {item["fact_id"]: item["status"] for check in context.report.get("major_scope_checks", [])
                       if check["field"] == rule.field and check["expected"] == expected
                       for item in check["observations"]}
        observations = [{**fact, "catalog_comparison": comparisons.get(fact["fact_id"])} for fact in observations]
        # Any scope or exception must already exist in the confirmed task. No implicit category expansion.
        if None in accepted:
            return _finding(rule, path, "UNKNOWN", "MAJOR_SCOPE_UNCLEAR", ids=ids, observed=observations)
        if accepted & observed:
            matching = [e for fact in observations if observed_value(fact) in accepted
                        for e in context.verified(fact["evidence_ids"])]
            return _finding(rule, path, "PASS", "MAJOR_MATCHED", ids=matching, observed=observations)
        if None in observed:
            return _finding(rule, path, "UNKNOWN", "MAJOR_UNMAPPED", ids=ids, observed=observations)
        completeness = context.declaration(COMPLETE_EDUCATION)
        if not completeness or context.report.get("capture_status") != "complete":
            return _finding(rule, path, "UNKNOWN", "MAJOR_SCOPE_UNCLEAR", ids=ids, observed=observations)
        return _finding(rule, path, "FAIL", "MAJOR_OUT_OF_SCOPE", ids=ids + completeness, observed=observations)
    expected = rule.value if isinstance(rule.value, list) else [rule.value]
    values = [fact["normalized_value"] for fact in observations]
    if rule.field == "education_level":
        values = [DEGREES.get(value, value) for value in values]
    elif rule.field == "education_mode":
        values = [{"全日制": "full_time", "非全日制": "part_time"}.get(value, value) for value in values]
    elif rule.field == "graduation_year":
        values = [int(value) for value in values if isinstance(value, str) and value.isdecimal()]
    if not values:
        return _finding(rule, path, "UNKNOWN", "EDUCATION_NOT_PROVIDED", ids=ids)
    if rule.operator == "gte" and rule.field == "education_level" and rule.value in DEGREE_RANK:
        matches = [DEGREE_RANK.get(value, -1) >= DEGREE_RANK[rule.value] for value in values]
    elif rule.operator in {"equals", "one_of"}:
        matches = [value in expected for value in values]
    else:
        return _finding(rule, path, "UNKNOWN", "RULE_SCOPE_UNCLEAR")
    if any(matches) and not all(matches):
        return _finding(rule, path, "CONFLICT", "EDUCATION_SOURCE_CONFLICT", ids=ids, observed=observations)
    ok = all(matches)
    return _finding(rule, path, "PASS" if ok else "FAIL", "EDUCATION_MATCHED" if ok else "EDUCATION_OUT_OF_SCOPE",
                    ids=ids, observed=observations)


def _confirmed_date_conflict(rows, context):
    def key(row):
        return tuple(re.sub(r"\s+", "", (row[name] or "")).casefold() for name in ("company", "position"))

    def sources(row):
        return {context.refs[ref]["source_type"] for ref in row["evidence_ids"]}

    def same_endpoint(first, second):
        return any(
            first["date_precision"][side] == second["date_precision"][side] == "day"
            and tuple(map(int, re.findall(r"\d+", first[side + "_date"])))
            == tuple(map(int, re.findall(r"\d+", second[side + "_date"])))
            for side in ("start", "end")
        )

    pairs = [(first, second) for index, first in enumerate(rows) for second in rows[index + 1:]
             if all(key(first)) and key(first) == key(second) and sources(first).isdisjoint(sources(second))
             and same_endpoint(first, second)]
    counts = {}
    for first, second in pairs:
        for row in (first, second):
            counts[row["experience_id"]] = counts.get(row["experience_id"], 0) + 1
    return [row for pair in pairs if all(counts[item["experience_id"]] == 1 for item in pair) for row in pair]


def _hr(rule, path, context):
    report = context.report
    rows = [row for row in report.get("experiences", []) if row["hr_relevance"] != "EXCLUDED" or row["conflicts"]]
    captured = report.get("capture_status") == "complete"
    references = [ref for row in rows for ref in row["evidence_ids"]]
    ids = context.verified(references)
    if ids is None or any(not row["evidence_ids"] for row in rows):
        return _finding(rule, path, "UNKNOWN", "EVIDENCE_REFERENCE_INVALID")
    valid = set(ids)
    verified = [row for row in rows if set(row["evidence_ids"]) <= valid]
    timeline = report.get("hr_timeline")
    if not timeline:
        return _finding(rule, path, "UNKNOWN", "HR_EXPERIENCE_MISSING_EVIDENCE")
    # Reuse Phase 4 union policy if any observations had to be excluded; never
    # accept an upper bound calculated with partial or unverified observations.
    as_of = date.fromisoformat(timeline["as_of"])
    if len(verified) != len(rows) or len(valid) != len(set(references)):
        timeline = hr_totals(verified, as_of, capture_complete=False)
    elif not captured and not timeline["has_unknown_upper_bound"]:
        timeline = hr_totals(verified, as_of, capture_complete=False)
    disputed = [row for row in verified if "inconsistent_dates_or_unresolved_record_identity" in row["conflicts"]]
    conflict_rows = _confirmed_date_conflict(disputed, context)
    zero = context.declaration(ZERO_HR)
    complete = context.declaration(COMPLETE_WORK)
    if verified and zero and complete and captured and timeline["confirmed_hr_experience"]:
        return _finding(rule, path, "CONFLICT", "HR_RELEVANCE_UNCERTAIN", ids=ids + zero + complete, observed=timeline)
    if not verified:
        if not (zero and complete and captured):
            codes = ["HR_EXPERIENCE_MISSING_EVIDENCE"]
            if rows and rule.operator in {"lte", "between", "equals"}:
                codes.append("HR_EXPERIENCE_UNKNOWN_UPPER_BOUND")
            return _finding(rule, path, "UNKNOWN", *codes, observed=timeline if rows else None)
        ids = zero + complete
        lower = upper = 0
        timeline = {**timeline, "confirmed_zero": True, "confirmed_hr_days": 0, "possible_hr_days_max": 0,
                    "has_unknown_upper_bound": False}
    else:
        lower = timeline["confirmed_hr_days"] or 0
        upper = None if timeline["has_unknown_upper_bound"] else timeline["possible_hr_days_max"]
    op = rule.operator
    if op not in {"gt", "gte", "lte", "between", "equals"}:
        return _finding(rule, path, "UNKNOWN", "RULE_SCOPE_UNCLEAR")
    starts = [row["date_windows"]["possible"][0] for row in verified if row["date_windows"]["possible"]]
    anchor = date.fromisoformat(min(starts)) if starts and all(
        row["date_precision"]["start"] == "day" and row["date_windows"]["status"] == "BOUNDED"
        for row in verified) else None
    threshold = HRBounds(0, None, anchor, (), False, False).threshold
    minimum = rule.value[0] if op == "between" else rule.value if op in {"gt", "gte", "equals"} else None
    maximum = rule.value[1] if op == "between" else rule.value if op in {"lte", "equals"} else None
    lower_ok, upper_ok = True, True
    limits = {}
    if minimum is not None:
        least, most = threshold(minimum)
        offset = int(op == "gt")
        limits["minimum_days_threshold"] = [least + offset, most + offset]
        if upper is not None and upper < least + offset:
            return _finding(rule, path, "FAIL", "HR_EXPERIENCE_BELOW_MIN", ids=ids,
                            observed={**timeline, **limits})
        lower_ok = lower >= most + offset or op == "gt" and minimum == 0 and bool(timeline["confirmed_hr_experience"])
    if maximum is not None:
        least, most = threshold(maximum)
        limits["maximum_days_threshold"] = [least, most]
        if lower > most:
            return _finding(rule, path, "FAIL", "HR_EXPERIENCE_EXCEEDS_MAX", ids=ids, observed={**timeline, **limits})
        upper_ok = upper is not None and upper <= least
    if conflict_rows:
        return _finding(rule, path, "CONFLICT", "HR_EXPERIENCE_DATE_CONFLICT",
                        ids=[key for row in conflict_rows for key in row["evidence_ids"]], observed={**timeline, **limits})
    if disputed:
        return _finding(rule, path, "UNKNOWN", "HR_RECORD_IDENTITY_UNRESOLVED", ids=ids, observed={**timeline, **limits})
    if lower_ok and upper_ok:
        return _finding(rule, path, "PASS", "HR_EXPERIENCE_WITHIN_RANGE", ids=ids, observed={**timeline, **limits})
    causes = []
    if maximum is not None and upper is None:
        causes.append("HR_EXPERIENCE_UNKNOWN_UPPER_BOUND")
    if not lower_ok or upper is not None and not upper_ok or any(row["hr_relevance"] == "POSSIBLE" for row in verified):
        causes.append("HR_RELEVANCE_UNCERTAIN")
    return _finding(rule, path, "UNKNOWN", *(causes or ["HR_EXPERIENCE_MISSING_EVIDENCE"]),
                    ids=ids, observed={**timeline, **limits})


def _evaluate(rule, path, context, facts):
    if isinstance(rule, ConditionGroup):
        children = [_evaluate(child, f"{path}.{index}", context, facts) for index, child in enumerate(rule.conditions, 1)]
        status = combine(rule.operator, [child["status"] for child in children])
        decisive = [child for child in children if child["status"] == status]
        if rule.operator == "NOT":
            decisive = children
        codes = [code for child in decisive for code in child["reason_codes"]] if status in {"CONFLICT", "UNKNOWN"} else []
        return _finding(rule, path, status, "GROUP_" + status, *codes,
                        ids=[ref for child in decisive for ref in child["evidence_ids"]], children=children)
    if rule.field in EDUCATION:
        return _education(rule, path, context, facts)
    if rule.field == "hr_experience_months":
        return _hr(rule, path, context)
    if rule.operator == "verify" or rule.field.startswith("experience_months") or rule.field not in {
        "hands_on_tools", "tools", "skills", "certificates", "graduate_status", "industry_experience",
    }:
        return _finding(rule, path, "UNKNOWN", "RULE_SCOPE_UNCLEAR")
    resume = context.candidate.get("structured_evidence")
    if resume is None:
        return _finding(rule, path, "UNKNOWN", "QUALIFICATION_EVIDENCE_MISSING")
    finding = evaluate_condition(rule, resume, judge=None)
    ids = context.verified([context.cited(item.source, item.start, item.end, item.quote) for item in finding.evidence])
    if ids is None:
        return _finding(rule, path, "UNKNOWN", "EVIDENCE_REFERENCE_INVALID")
    if finding.status == "UNKNOWN" or not ids:
        return _finding(rule, path, "UNKNOWN", "QUALIFICATION_EVIDENCE_MISSING", ids=ids)
    return _finding(rule, path, finding.status, "QUALIFICATION_EVIDENCE_MATCHED" if finding.status == "PASS" else
                    "QUALIFICATION_EVIDENCE_EXCLUDES", ids=ids,
                    observed=[item.value for item in finding.evidence if item.value is not None])


def evaluate_shadow(task, report, candidate, dimensions):
    base = dict(rule_version=VERSION, official=False, score=None, score_breakdown={},
                business_scope_policy="confirmed-task-only-v1",
                catalog_version=report.get("catalog_version"), evidence={}, criteria=[])
    if report.get("identity_status") != "VERIFIED" or report.get("capture_status") == "failed":
        issues = report.get("issues", [])
        code = "CAPTURE_IDENTITY_MISMATCH" if any(issue in {
            "card_open_failed_or_identity_mismatch", "detail_identity_mismatch_or_unverified",
            "detail_changed_during_ocr", "repeated_detail_content_identity_unverified",
        } for issue in issues) else "CAPTURE_FAILED"
        return {**base, "status": "CAPTURE_FAILED", "compatibility_status": "CAPTURE_FAILED",
                "reason_codes": [code], "requires_review": False}
    context = EvidenceContext(report, candidate)
    records = {}
    for fact in report.get("facts", []):
        records.setdefault(fact["record_id"], []).append(fact)
    group = ConditionGroup("AND", task.hard_filters)
    assessments = [(record, facts, _evaluate(group, "hard", context, facts))
                   for record, facts in (records.items() if records else [(None, [])])]
    priority = {"FAIL": 0, "UNKNOWN": 1, "CONFLICT": 2, "PASS": 3}
    record, facts, hard = max(assessments, key=lambda assessment: priority[assessment[2]["status"]])
    status = hard["status"]
    codes = set(hard["reason_codes"])
    warnings = report.get("capture_warnings", [])
    if warnings:
        codes.add("CAPTURE_INCOMPLETE")
        if any(item.startswith("ocr") for item in warnings):
            codes.add("OCR_UNRELIABLE")
        if status == "PASS":
            status = "UNKNOWN"
    breakdown = {}
    if status == "PASS":
        total = sum(item.weight for item in dimensions)
        for index, dimension in enumerate(dimensions, 1):
            finding = _evaluate(dimension.condition, f"preference.{index}", context, facts)
            breakdown[dimension.name] = {"points": 100 * dimension.weight / total if finding["status"] == "PASS" else 0,
                                        "evaluation": finding}
    return {**base, "status": status, "compatibility_status": "UNKNOWN" if status == "CONFLICT" else status,
            "requires_review": status in {"UNKNOWN", "CONFLICT"}, "reason_codes": sorted(codes),
            "review_suggestion": "请核验相关原文或确认规则范围" if status in {"UNKNOWN", "CONFLICT"} else None,
            "education_record_id": record, "criteria": hard["children"], "score_breakdown": breakdown,
            "education_alternatives": [{"record_id": item, "status": finding["status"],
                                        "criteria": finding["children"]} for item, _, finding in assessments if item != record],
            "score": round(sum(item["points"] for item in breakdown.values()), 2) if status == "PASS" else None,
            "evidence": {key: ref for key, ref in context.refs.items() if context.verified([key]) is not None}}
