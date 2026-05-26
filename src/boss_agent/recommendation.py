from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from boss_agent.knowledge_base import load_knowledge_base
from boss_agent.knowledge_base import normalize_text


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
