from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

from boss_agent.knowledge_base import load_knowledge_base
from boss_agent.knowledge_base import normalize_text
from boss_agent.job_profile import JobProfile, ScoreDimension, apply_job_profile
from boss_agent.rule_engine import ResumeEvidence, SemanticJudge, combine_status, evaluate_condition, extract_resume_evidence, scope_education_evidence
from boss_agent.task_schema import RecruitmentTask, iter_criteria
from boss_agent.task_validator import RecruitmentTaskBlocked, is_sensitive, task_blockers


BUSINESS_KEYWORDS = (
    "天猫",
    "淘宝",
    "京东",
    "抖音",
    "小红书",
    "拼多多",
    "美妆",
    "个护",
    "类目",
    "店铺",
    "分销",
    "渠道",
    "社群",
    "团购",
    "投放",
    "推广",
    "gmv",
    "销售额",
    "增长",
    "团队",
    "管理",
)


def score_recommendation(
    resume: dict[str, Any],
    job_title: str,
    knowledge_dir: Path | None = None,
    min_score: int = 70,
) -> dict[str, Any]:
    job_title = job_title.strip()
    resume_text = _resume_text(resume)
    normalized_resume = normalize_text(resume_text)
    reasons: list[str] = []
    risks: list[str] = []
    score = 0

    expected_city = str(resume.get("expectedCity") or resume.get("city") or "").strip()
    job_doc = _find_job_doc(job_title, knowledge_dir)
    job_sections = job_doc.get("sections", {}) if job_doc else {}
    job_text = "\n".join(str(value) for value in job_sections.values())
    normalized_job_text = normalize_text(job_text)

    job_city = _extract_job_city(job_sections)
    if job_city and expected_city:
        if job_city in expected_city or expected_city in job_city:
            score += 20
            reasons.append(f"期望城市匹配：{expected_city}")
        else:
            risks.append(f"期望城市为 {expected_city}，与岗位城市 {job_city} 不一致")
            score -= 10
    elif expected_city:
        score += 10
        reasons.append(f"已识别期望城市：{expected_city}")
    else:
        risks.append("未识别期望城市")

    title_text = normalize_text(
        " ".join(
            str(resume.get(key) or "")
            for key in ("expectedTitle", "currentTitle", "detailTextPreview", "rawTextPreview")
        )
    )
    title_overlap = _keyword_overlap(_keywords_from_text(job_title), title_text)
    if title_overlap:
        score += min(20, title_overlap * 10)
        reasons.append("候选人职位方向与岗位名称有重合")
    else:
        risks.append("职位方向与目标岗位名称重合度较弱")

    requirement_keywords = _keywords_from_text(job_text)
    matched_requirements = _keyword_overlap(requirement_keywords, normalized_resume)
    if matched_requirements:
        score += min(35, matched_requirements * 5)
        reasons.append(f"简历命中岗位要求关键词 {matched_requirements} 项")
    elif normalized_job_text:
        risks.append("简历暂未命中岗位要求关键词")

    matched_business = [keyword for keyword in BUSINESS_KEYWORDS if normalize_text(keyword) in normalized_resume]
    if matched_business:
        score += min(20, len(set(matched_business)) * 4)
        reasons.append("业务关键词匹配：" + "、".join(sorted(set(matched_business))[:6]))
    else:
        risks.append("缺少平台、类目、渠道或业务结果等关键信号")

    education = str(resume.get("education") or "")
    if any(keyword in education for keyword in ("本科", "硕士", "研究生", "统招", "大专")):
        score += 5
        reasons.append(f"学历信息可用：{education}")

    score = max(0, min(100, score))
    if score >= min_score:
        decision = "greet"
    elif score >= 50:
        decision = "review"
    else:
        decision = "skip"

    summary = summarize_resume(resume)
    greeting = build_recommendation_greeting(resume, job_title, summary) if decision == "greet" else ""
    return {
        "match_score": score,
        "decision": decision,
        "reasons": reasons,
        "risks": risks,
        "summary": summary,
        "greeting": greeting,
    }


def summarize_resume(resume: dict[str, Any]) -> str:
    name = str(resume.get("name") or resume.get("candidateName") or "候选人").strip()
    title = str(resume.get("expectedTitle") or resume.get("currentTitle") or "").strip()
    city = str(resume.get("expectedCity") or resume.get("city") or "").strip()
    experience = str(resume.get("yearsOfExperience") or resume.get("experience") or "").strip()
    education = str(resume.get("education") or "").strip()
    highlights = [str(item).strip() for item in resume.get("workHighlights", []) if str(item).strip()]
    pieces = [name]
    if title:
        pieces.append(f"方向：{title}")
    if city:
        pieces.append(f"城市：{city}")
    if experience:
        pieces.append(f"经验：{experience}")
    if education:
        pieces.append(f"学历：{education}")
    if highlights:
        pieces.append("亮点：" + "；".join(highlights[:3]))
    return "，".join(pieces)


def build_recommendation_greeting(resume: dict[str, Any], job_title: str, summary: str) -> str:
    name = str(resume.get("name") or resume.get("candidateName") or "").strip()
    salutation = f"{name}您好" if name else "您好"
    return (
        f"{salutation}，看到您的经历和 {job_title} 方向比较匹配。"
        "这边想和您进一步沟通一下岗位情况，方便的话我们可以先简单聊聊。"
    )


def _find_job_doc(job_title: str, knowledge_dir: Path | None) -> dict[str, Any]:
    if not knowledge_dir or not knowledge_dir.exists() or not job_title:
        return {}
    knowledge = load_knowledge_base(knowledge_dir)
    normalized_target = normalize_text(job_title)
    for name, doc in knowledge.get("jobs", {}).items():
        normalized_name = normalize_text(name)
        if normalized_target == normalized_name or normalized_name in normalized_target or normalized_target in normalized_name:
            return doc
    return {}


def _extract_job_city(sections: dict[str, Any]) -> str:
    for key in ("工作地点", "城市", "办公地点"):
        value = str(sections.get(key) or "").strip()
        if value:
            return value.splitlines()[0].strip(" -0123456789、.；;")
    return ""


def _resume_text(resume: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "name",
        "candidateName",
        "expectedTitle",
        "expectedCity",
        "yearsOfExperience",
        "experience",
        "education",
        "currentTitle",
        "skills",
        "detailTextPreview",
        "rawTextPreview",
    ):
        value = resume.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    values.extend(str(item) for item in resume.get("tags", []) if item)
    values.extend(str(item) for item in resume.get("workHighlights", []) if item)
    return "\n".join(values)


def _keywords_from_text(text: str) -> list[str]:
    raw = re.split(r"[，。？！、；：,.!?/\s（）()【】\[\]\-]+", text)
    return [normalize_text(token) for token in raw if len(normalize_text(token)) >= 2]


def _keyword_overlap(keywords: list[str], target: str) -> int:
    return len({keyword for keyword in keywords if keyword and keyword in target})

def _scoring_dimensions(task: RecruitmentTask, configured: list[ScoreDimension]) -> list[ScoreDimension]:
    def signature(condition) -> str:
        data = asdict(condition)

        def without_source(node: dict) -> dict:
            if "conditions" in node:
                return {**node, "conditions": [without_source(child) for child in node["conditions"]]}
            return {key: value for key, value in node.items() if key != "source"}

        return json.dumps(without_source(data), sort_keys=True, ensure_ascii=False)

    dimensions = list(configured)
    known = {signature(item.condition) for item in dimensions}
    names = {item.name for item in dimensions}
    fallback_weight = sum(item.weight for item in dimensions) / len(dimensions) if dimensions else 1
    for index, condition in enumerate(task.soft_preferences, 1):
        key = signature(condition)
        if key in known:
            continue
        name = f"preference_{index}"
        while name in names:
            name += "_request"
        dimensions.append(ScoreDimension(name, condition, fallback_weight))
        names.add(name)
        known.add(key)
    return dimensions


def prepare_recruitment_preview(
    task: RecruitmentTask, profile: JobProfile | None = None, template: str | None = None,
) -> tuple[RecruitmentTask, list[ScoreDimension]]:
    """Validate the merged requirements before any candidate collection."""
    if template is not None and profile is None:
        raise ValueError("template_requires_job_profile")
    selected = profile.select(template) if profile else None
    if profile:
        task = apply_job_profile(task, profile, template)
    if selected:
        for dimension in selected.scoring:
            # Dataclass callers get the same sensitive-field check as JSON callers.
            if any(is_sensitive(rule) for rule in iter_criteria(dimension.condition)):
                raise ValueError("unsupported_sensitive_recruitment_condition")
    blockers = [item for item in task_blockers(task) if item != "target_success_count_exceeds_max_checks"]
    if selected and selected.scoring:
        blockers = [item for item in blockers if item != "criteria_required"]
    if blockers:
        raise RecruitmentTaskBlocked(blockers)
    return task, selected.scoring if selected else []


def _finding_has_source_support(finding, sources: dict[str, str]) -> bool:
    if finding.status == "UNKNOWN":
        return False
    if finding.children:
        decisive = [child for child in finding.children if child.status == finding.status]
        if finding.field == "NOT":
            return _finding_has_source_support(finding.children[0], sources)
        if finding.field == "OR" and finding.status == "PASS" or finding.field == "AND" and finding.status == "FAIL":
            return any(_finding_has_source_support(child, sources) for child in decisive)
        return len(decisive) == len(finding.children) and all(
            _finding_has_source_support(child, sources) for child in decisive
        )
    return bool(finding.evidence) and all(
        not item.conflict and item.quote.strip() and item.source in sources
        and 0 <= item.start < item.end <= len(sources[item.source])
        and sources[item.source][item.start:item.end] == item.quote
        for item in finding.evidence
    )


def decide_verdict(mode: str, core_pass: int, core_unknown: int, core_total: int,
                   bonus_pass: int, bonus_unknown: int, bonus_total: int,
                   min_core: int, min_bonus: int) -> str:
    """按岗位规则给出三态结论；strict_all 由调用方用 combine_status 处理。"""
    if mode == "strict_core":
        if core_total == 0:
            return "PASS" if bonus_pass >= min_bonus else ("UNKNOWN" if bonus_unknown else "FAIL")
        if core_pass == core_total:
            return "PASS"
        return "UNKNOWN" if core_unknown else "FAIL"
    if mode == "weighted":
        enough_core = core_total == 0 or core_pass * 100 >= min_core * core_total
        enough_bonus = bonus_pass >= min_bonus
        possible = ((core_total == 0 or (core_pass + core_unknown) * 100 >= min_core * core_total)
                    and (bonus_pass + bonus_unknown) >= min_bonus)
        return "PASS" if enough_core and enough_bonus else ("UNKNOWN" if possible else "FAIL")
    return "UNKNOWN"


def rank_recruitment_preview(
    task: RecruitmentTask,
    candidates: list[dict[str, Any]],
    *,
    profile: JobProfile | None = None,
    template: str | None = None,
    judge: SemanticJudge | None = None,
    default_max_checks: int = 200,
    as_of: date | None = None,
) -> dict:
    """Evaluate a bounded candidate list without involving a browser or sending."""
    task, configured_dimensions = prepare_recruitment_preview(task, profile, template)
    if type(default_max_checks) is not int or default_max_checks < 1 or type(candidates) is not list:
        raise ValueError("invalid_preview_input")
    dimensions = _scoring_dimensions(task, configured_dimensions)
    total_weight = sum(item.weight for item in dimensions)
    qualified, rejected, review, capture_failed = [], [], [], []
    seen = set()
    budget = task.max_checks or default_max_checks
    as_of = as_of or date.today()
    for candidate in candidates:
        if len(seen) >= budget:
            break
        if type(candidate) is not dict:
            raise ValueError("invalid_candidate")
        candidate_id, sources = candidate.get("candidate_id"), candidate.get("sources")
        if type(candidate_id) is not str or not candidate_id.strip() or candidate_id.strip() != candidate_id:
            raise ValueError("candidate_id_required")
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        capture_status = candidate.get("capture_status")
        if capture_status in {"failed", "CAPTURE_FAILED"}:
            capture_failed.append({"candidate_id": candidate_id, "capture_status": "failed",
                                   "eligibility_status": "CAPTURE_FAILED",
                                   "reason": candidate.get("reason", "resume_capture_failed")})
            continue
        if type(sources) is not dict or any(type(key) is not str or type(value) is not str for key, value in sources.items()):
            raise ValueError("invalid_resume_sources")
        resume = candidate.get("structured_evidence")
        if not isinstance(resume, ResumeEvidence):
            resume = extract_resume_evidence(sources, as_of=as_of, candidate_id=candidate_id,
                                             capture_status=capture_status or "unverified")
        else:
            resume = replace(resume, as_of=as_of)
        model_findings = candidate.get("model_findings")
        if model_findings is None:
            resume = scope_education_evidence(resume, task.hard_filters)
        findings = [evaluate_condition(rule, resume, judge, model_findings=model_findings) for rule in task.hard_filters]
        status = combine_status("AND", [finding.status for finding in findings])
        preferences = [evaluate_condition(rule, resume, judge, model_findings=model_findings)
                       for rule in task.soft_preferences]
        mode = getattr(task, "decision_mode", "strict_all")
        min_core = getattr(task, "min_core_percent", 100)
        min_bonus = getattr(task, "min_bonus_count", 0)
        core_pass = sum(1 for item in findings if item.status == "PASS")
        core_unknown = sum(1 for item in findings if item.status == "UNKNOWN")
        core_total = len(findings)
        bonus_pass = sum(1 for item in preferences if item.status == "PASS")
        bonus_unknown = sum(1 for item in preferences if item.status == "UNKNOWN")
        bonus_total = len(preferences)
        if mode == "strict_all":
            verdict = combine_status("AND", [status, *[item.status for item in preferences]]) if preferences else status
        else:
            verdict = decide_verdict(mode, core_pass, core_unknown, core_total,
                                     bonus_pass, bonus_unknown, bonus_total, min_core, min_bonus)
        conditions = [*findings, *preferences]
        matched = sum(1 for item in conditions if item.status == "PASS")
        total = len(conditions)
        match_percent = round(100 * matched / total) if total else 100
        supported = bool(findings) and all(_finding_has_source_support(item, sources) for item in findings)
        unsupported_pass = verdict == "PASS" and bool(findings) and not supported
        row = {"candidate_id": candidate_id, "hard_filter_status": status,
               "eligibility_status": "UNKNOWN" if verdict == "PASS" and (capture_status == "partial" or unsupported_pass) else verdict,
               "contact_evidence_verified": verdict == "PASS" and supported and capture_status != "partial",
               "hard_filters": [finding.to_dict() for finding in findings],
               "match_percent": match_percent, "matched_conditions": matched, "total_conditions": total,
               "decision_mode": mode, "core_pass": core_pass, "core_total": core_total,
               "bonus_pass": bonus_pass, "bonus_total": bonus_total,
               "score": None, "score_breakdown": {}}
        if "capture_status" in candidate:
            row["capture_status"] = candidate["capture_status"]
            row["capture_warnings"] = candidate.get("capture_warnings", [])
        if model_findings is not None:
            for dimension in dimensions:
                finding = evaluate_condition(dimension.condition, resume, judge, model_findings=model_findings)
                row["score_breakdown"][dimension.name] = {
                    "finding": finding.to_dict(),
                    "points": round(100 * dimension.weight / total_weight, 2) if finding.status == "PASS" else 0,
                }
        if verdict == "FAIL":
            rejected.append(row)
        elif verdict == "UNKNOWN" or capture_status == "partial" or unsupported_pass:
            if candidate.get("capture_status") == "partial":
                row["review_reason"] = "capture_incomplete"
            elif unsupported_pass:
                row["review_reason"] = "hard_condition_citation_unverified"
            review.append(row)
        else:
            earned_weight = 0
            for dimension in dimensions:
                if model_findings is not None:
                    if row["score_breakdown"][dimension.name]["finding"]["status"] == "PASS":
                        earned_weight += dimension.weight
                    continue
                finding = evaluate_condition(dimension.condition, resume, judge)
                earned = dimension.weight if finding.status == "PASS" else 0
                earned_weight += earned
                row["score_breakdown"][dimension.name] = {
                    "points": round(100 * earned / total_weight, 2),
                    "finding": finding.to_dict(),
                }
            row["score"] = round(100 * earned_weight / total_weight, 2) if total_weight else 0
            qualified.append(row)
    qualified.sort(key=lambda row: (-row["score"], row["candidate_id"]))
    return {
        "task": task.to_dict(),
        "strict_all_conditions": task.decision_mode == "strict_all",
        "decision_mode": task.decision_mode,
        "min_core_percent": task.min_core_percent,
        "min_bonus_count": task.min_bonus_count,
        "template": template,
        "execution_mode": "dry_run",
        "as_of": as_of.isoformat(),
        "summary": {
            "checked": len(seen), "qualified": len(qualified), "unqualified": len(rejected),
            "needs_review": len(review), "target_contact_count": task.target_success_count,
        },
        "ranked_candidates": qualified,
        "rejected_candidates": rejected,
        "review_candidates": review,
        "capture_failed_candidates": capture_failed,
        "contact_preview": [row["candidate_id"] for row in qualified if row["contact_evidence_verified"]][:task.target_success_count]
        if task.action == "greet" else [],
    }
