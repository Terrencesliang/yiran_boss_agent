from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.request import urlopen


KEYWORD_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("天猫", "淘宝", "京东", "抖音", "小红书", "拼多多", "电商", "平台电商", "线上店铺"),
    ("店铺运营", "店长", "平台运营", "类目运营", "商品运营", "电商运营", "运营管理"),
    ("投放", "推广", "千川", "直通车", "钻展", "信息流", "广告投放", "付费推广"),
    ("美妆", "护肤", "彩妆", "个护", "化妆品", "美容"),
    ("招聘", "猎头", "人事", "hr", "hrbp", "人力资源", "人才"),
    ("行政", "前台", "文员", "会务", "助理", "行政专员"),
)

ECOMMERCE_TERMS = ("天猫", "淘宝", "京东", "抖音", "小红书", "拼多多", "电商", "平台电商", "线上店铺")
ECOMMERCE_ROLE_TERMS = (
    "店铺运营", "店长", "平台运营", "类目运营", "商品运营", "电商运营", "运营管理",
    "运营", "活动策划", "投放", "推广", "gmv", "GMV", "商品上下架", "数据分析",
    "电商经验", "平台经验", "天猫经验", "淘宝经验", "店铺经验",
)


def analyze_resume_against_criteria(
    resume_text: str,
    criteria: str,
    model: str | None = None,
) -> dict[str, Any]:
    resume_text = _strip_non_resume_sections(resume_text)
    if _has_llm_config():
        try:
            result = _analyze_with_openai_compatible_api(
                resume_text=resume_text,
                criteria=criteria,
                model=model or os.environ.get("BOSS_AGENT_LLM_MODEL") or "gpt-4.1-mini",
            )
            return _apply_keyword_evidence_check(result, resume_text=resume_text, criteria=criteria, implicit=True)
        except Exception as exc:
            fallback = _analyze_with_rules(resume_text=resume_text, criteria=criteria)
            fallback["agent"] = "rules_fallback_after_llm_error"
            fallback["llmError"] = str(exc)
            return _apply_keyword_evidence_check(fallback, resume_text=resume_text, criteria=criteria, implicit=False)

    fallback = _analyze_with_rules(resume_text=resume_text, criteria=criteria)
    fallback["agent"] = "rules_fallback_no_llm_config"
    return _apply_keyword_evidence_check(fallback, resume_text=resume_text, criteria=criteria, implicit=False)


def _strip_non_resume_sections(resume_text: str) -> str:
    text = str(resume_text or "")
    cut_markers = [
        "\u5408\u4f5c\u4e13\u4eab",
        "\u540c\u4e8b\u6c9f\u901a\u8fdb\u5ea6",
        "\u6211\u7684\u6c9f\u901a\u8fdb\u5ea6",
        "\u5411Ta\u53d1\u8d77\u6c9f\u901a",
        "\u5411ta\u53d1\u8d77\u6c9f\u901a",
        "\u5411\u60a8\u53d1\u8d77\u6c9f\u901a",
    ]
    positions = [text.find(marker) for marker in cut_markers if marker in text]
    if positions:
        text = text[: min(positions)]
    return text.strip()


def _has_llm_config() -> bool:
    return bool(os.environ.get("BOSS_AGENT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _analyze_with_openai_compatible_api(resume_text: str, criteria: str, model: str) -> dict[str, Any]:
    api_key = os.environ.get("BOSS_AGENT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    base_url = (os.environ.get("BOSS_AGENT_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    prompt = _build_prompt(resume_text=resume_text, criteria=criteria)
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "你是招聘简历筛选助手。只输出 JSON，不要输出 Markdown。",
            },
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))

    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return _normalize_agent_result(parsed, agent="llm")


def _build_prompt(resume_text: str, criteria: str) -> str:
    return (
        "请阅读下面的候选人简历文本，提取结构化信息，并判断是否满足筛选条件。\n"
        "要求：\n"
        "1. 不要只做字符串包含判断，要理解摘要、教育经历、工作经历和时间线。\n"
        "2. 学历以最高已完成或明确在读/毕业学历为准；如果只是培训、专修、继续教育，请在 reasons 里说明。\n"
        "3. 工作经验以页面明确给出的年限和简历工作经历综合判断；应届生不要误判为多年工作经验。\n"
        "4. 生成一句 30 字以内的候选人概括，放到 candidateSummary。\n"
        "5. 严格输出 JSON，字段为：name, candidateSummary, educationLevel, workYears, meetsCriteria, reasons, risks。\n\n"
        f"筛选条件：{criteria}\n\n"
        f"简历文本：\n{resume_text[:6000]}"
    )


def _normalize_agent_result(payload: dict[str, Any], agent: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "name": str(payload.get("name") or "").strip(),
        "candidateSummary": str(payload.get("candidateSummary") or payload.get("summary") or "").strip(),
        "age": payload.get("age"),
        "educationLevel": str(payload.get("educationLevel") or "").strip(),
        "workYears": payload.get("workYears"),
        "meetsCriteria": bool(payload.get("meetsCriteria")),
        "reasons": _string_list(payload.get("reasons")),
        "risks": _string_list(payload.get("risks")),
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _apply_explicit_keyword_check(result: dict[str, Any], resume_text: str, criteria: str) -> dict[str, Any]:
    return _apply_keyword_evidence_check(result, resume_text=resume_text, criteria=criteria, implicit=False)


def _apply_keyword_evidence_check(
    result: dict[str, Any],
    resume_text: str,
    criteria: str,
    implicit: bool = False,
) -> dict[str, Any]:
    requirement = _extract_explicit_keyword_requirement(criteria)
    if implicit and not requirement["keywords"]:
        requirement = _extract_implicit_keyword_requirement(criteria)
    keywords = requirement["keywords"]
    if not keywords:
        if result.get("agent") == "llm":
            return _apply_llm_confidence_defaults(result, keyword_match=None)
        return result

    keyword_match = _match_keyword_groups(
        resume_text=resume_text,
        keywords=keywords,
        mode=str(requirement["mode"]),
    )
    updated = {**result}
    reasons = _string_list(updated.get("reasons"))
    risks = _string_list(updated.get("risks"))
    if keyword_match["matched"]:
        reasons.append(
            "关键词/同义词匹配："
            + "；".join(
                f"{item['keyword']} -> {item['matchedTerm']}"
                for item in keyword_match["matches"]
            )
        )
    else:
        missing = keyword_match.get("missing") or keywords
        risks.append("未识别到关键词或同义词：" + "、".join(missing))

    updated["meetsCriteria"] = bool(updated.get("meetsCriteria")) and bool(keyword_match["matched"])
    updated["reasons"] = reasons
    updated["risks"] = risks
    updated["keywordMatch"] = keyword_match
    if updated.get("agent") == "llm":
        updated = _apply_llm_confidence_defaults(updated, keyword_match=keyword_match)
    return updated


def _apply_llm_confidence_defaults(
    result: dict[str, Any],
    keyword_match: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = {**result}
    meets = bool(updated.get("meetsCriteria"))
    if keyword_match and not keyword_match.get("matched"):
        updated["confidenceScore"] = 35
        updated["confidenceLevel"] = "low"
        updated["confidenceReason"] = "大模型判断后，关键词/同义词证据不足"
    elif meets:
        updated["confidenceScore"] = int(updated.get("confidenceScore") or 90)
        updated["confidenceLevel"] = str(updated.get("confidenceLevel") or "high")
        updated["confidenceReason"] = str(updated.get("confidenceReason") or "大模型判断满足要求，且关键词/同义词证据通过")
    else:
        updated["confidenceScore"] = int(updated.get("confidenceScore") or 45)
        updated["confidenceLevel"] = str(updated.get("confidenceLevel") or "low")
        updated["confidenceReason"] = str(updated.get("confidenceReason") or "大模型判断不满足要求")
    updated.setdefault("experienceEvidence", [])
    updated.setdefault("rejectedEvidence", [])
    return updated


def _apply_related_experience_check(result: dict[str, Any], resume_text: str, criteria: str) -> dict[str, Any]:
    if "relatedExperience" in result:
        return result
    related_experience = _match_related_experience(criteria=criteria, resume_text=resume_text)
    weak_company_evidence = _match_weak_company_evidence(resume_text)
    if not related_experience.get("required") and not weak_company_evidence:
        return result

    updated = {**result}
    reasons = _string_list(updated.get("reasons"))
    risks = _string_list(updated.get("risks"))
    if related_experience.get("required"):
        updated["meetsCriteria"] = bool(updated.get("meetsCriteria")) and bool(related_experience.get("matched"))
        if related_experience.get("matched"):
            reasons.append(str(related_experience.get("reason") or "识别到相关经验"))
        else:
            risks.append(str(related_experience.get("reason") or "未识别到足够相关经验"))
    if weak_company_evidence:
        risks.append("公司业务弱证据：" + "；".join(item.get("summary", "") for item in weak_company_evidence if item.get("summary")))
    updated["reasons"] = reasons
    updated["risks"] = risks
    updated["relatedExperience"] = related_experience
    updated["confidenceScore"] = related_experience.get("confidenceScore", updated.get("confidenceScore", 100 if updated.get("meetsCriteria") else 50))
    updated["confidenceLevel"] = related_experience.get("confidenceLevel", updated.get("confidenceLevel", "high" if updated.get("meetsCriteria") else "medium"))
    updated["confidenceReason"] = related_experience.get("confidenceReason", updated.get("confidenceReason", ""))
    updated["experienceEvidence"] = related_experience.get("experienceEvidence", related_experience.get("evidence", updated.get("experienceEvidence", [])))
    updated["rejectedEvidence"] = related_experience.get("rejectedEvidence", updated.get("rejectedEvidence", []))
    updated["weakCompanyEvidence"] = weak_company_evidence
    return updated


def _extract_explicit_keywords(criteria: str) -> list[str]:
    return list(_extract_explicit_keyword_requirement(criteria)["keywords"])


def _extract_implicit_keyword_requirement(criteria: str) -> dict[str, Any]:
    text = str(criteria or "")
    normalized_text = _normalize_keyword_text(text)
    keywords: list[str] = []
    seen_groups: set[int] = set()
    seen_terms: set[str] = set()
    for group_index, group in enumerate(KEYWORD_SYNONYM_GROUPS):
        matches = [
            term
            for term in group
            if _normalize_keyword_text(term) and _normalize_keyword_text(term) in normalized_text
        ]
        if not matches or group_index in seen_groups:
            continue
        keyword = max(matches, key=lambda item: len(_normalize_keyword_text(item)))
        normalized_keyword = _normalize_keyword_text(keyword)
        if normalized_keyword and normalized_keyword not in seen_terms:
            keywords.append(keyword)
            seen_terms.add(normalized_keyword)
            seen_groups.add(group_index)
    return {"mode": "all", "keywords": keywords}


def _extract_explicit_keyword_requirement(criteria: str) -> dict[str, Any]:
    match = re.search(r"(关键词任一|关键字任一|关键词必须|关键字必须|关键词|关键字)\s*[:：]\s*(.+)", str(criteria or ""), flags=re.IGNORECASE)
    if not match:
        return {"mode": "all", "keywords": []}
    label = match.group(1)
    raw = match.group(2).splitlines()[0]
    tokens = re.split(r"[、,，;；\s]+", raw)
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        keyword = token.strip(" ：:。.!！?？()（）[]【】")
        if not keyword or _looks_like_non_keyword_requirement(keyword):
            continue
        normalized = _normalize_keyword_text(keyword)
        if normalized and normalized not in seen:
            seen.add(normalized)
            keywords.append(keyword)
    return {"mode": "any" if "任一" in label else "all", "keywords": keywords}


def _looks_like_non_keyword_requirement(value: str) -> bool:
    return bool(
        re.fullmatch(r"\d{1,2}\s*[-到至~～]\s*\d{1,2}\s*岁?", value)
        or re.fullmatch(r"\d{1,2}\s*[-到至~～]\s*\d{1,2}\s*年", value)
        or re.fullmatch(r"[一二两三四五六七八九十\d]+\s*年(?:以上|工作|经验)?", value)
        or value in {"本科", "大专", "专科", "硕士", "研究生", "博士", "中专"}
    )


def _expand_keyword_synonyms(keyword: str) -> list[str]:
    normalized_keyword = _normalize_keyword_text(keyword)
    synonyms: list[str] = [keyword]
    seen = {normalized_keyword}
    for group in KEYWORD_SYNONYM_GROUPS:
        normalized_group = [_normalize_keyword_text(item) for item in group]
        if any(
            normalized_keyword == item
            or normalized_keyword in item
            or item in normalized_keyword
            for item in normalized_group
        ):
            for term in group:
                normalized_term = _normalize_keyword_text(term)
                if normalized_term and normalized_term not in seen:
                    seen.add(normalized_term)
                    synonyms.append(term)
    return synonyms


def _match_keyword_groups(resume_text: str, keywords: list[str], mode: str = "all") -> dict[str, Any]:
    normalized_resume = _normalize_keyword_text(resume_text)
    matches: list[dict[str, str]] = []
    expanded: dict[str, list[str]] = {}
    for keyword in keywords:
        synonyms = _expand_keyword_synonyms(keyword)
        expanded[keyword] = synonyms
        matched_term = next(
            (term for term in synonyms if _normalize_keyword_text(term) in normalized_resume),
            "",
        )
        if matched_term:
            evidence_text = _find_keyword_evidence(resume_text=resume_text, terms=synonyms)
            matches.append(
                {
                    "keyword": keyword,
                    "matchedTerm": matched_term,
                    "evidenceText": evidence_text,
                    "evidenceSummary": _summarize_keyword_evidence(
                        keyword=keyword,
                        matched_term=matched_term,
                        evidence_text=evidence_text,
                    ),
                }
            )
    missing = [keyword for keyword in keywords if not any(match["keyword"] == keyword for match in matches)]
    matched = bool(matches) if mode == "any" else not missing
    return {
        "required": True,
        "mode": mode,
        "matched": matched,
        "keywords": keywords,
        "expanded": expanded,
        "matches": matches,
        "missing": missing,
    }


def _find_keyword_evidence(resume_text: str, terms: list[str], max_length: int = 160) -> str:
    text = str(resume_text or "")
    normalized_terms = [
        _normalize_keyword_text(term)
        for term in terms
        if _normalize_keyword_text(term)
    ]
    paragraphs = [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"[\r\n]+", text)
        if part.strip()
    ]
    for paragraph in paragraphs:
        normalized_paragraph = _normalize_keyword_text(paragraph)
        if any(term in normalized_paragraph for term in normalized_terms):
            return _trim_evidence_text(paragraph, max_length=max_length)

    compact_text = re.sub(r"\s+", " ", text).strip()
    normalized_text = _normalize_keyword_text(compact_text)
    positions = [
        normalized_text.find(term)
        for term in normalized_terms
        if normalized_text.find(term) >= 0
    ]
    if not positions:
        return ""
    start = max(0, min(positions) - max_length // 3)
    end = min(len(compact_text), start + max_length)
    return _trim_evidence_text(compact_text[start:end], max_length=max_length)


def _trim_evidence_text(value: str, max_length: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _summarize_keyword_evidence(keyword: str, matched_term: str, evidence_text: str) -> str:
    if not evidence_text:
        return f"命中{matched_term}，与{keyword}要求相关"
    sentence = _trim_evidence_text(evidence_text, max_length=72)
    return f"命中{matched_term}：{sentence}"


def _normalize_keyword_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _analyze_with_rules(resume_text: str, criteria: str) -> dict[str, Any]:
    lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
    name = _extract_name(lines)
    summary = next(
        (
            line
            for line in lines
            if "岁" in line and any(degree in line for degree in ("本科", "大专", "硕士", "博士", "研究生"))
        ),
        "",
    )
    education_level = _extract_education_level(resume_text)
    age = _extract_age(summary, resume_text)
    work_years = _extract_work_years(summary, resume_text)
    parsed_criteria = _parse_criteria(criteria)
    explicit_keywords = _extract_explicit_keywords(criteria)
    related_experience = _match_related_experience(criteria=criteria, resume_text=resume_text)
    weak_company_evidence = _match_weak_company_evidence(resume_text)
    meets = True
    reasons: list[str] = []
    risks: list[str] = []

    age_range = parsed_criteria.get("ageRange")
    if age_range:
        min_age, max_age = age_range
        has_age = age is not None and min_age <= age <= max_age
        meets = meets and has_age
        if has_age:
            reasons.append(f"年龄识别为 {age} 岁，符合 {min_age}-{max_age} 岁")
        else:
            risks.append(f"年龄不满足 {min_age}-{max_age} 岁要求，识别为 {age if age is not None else '未知'}")

    min_education = parsed_criteria.get("minEducation")
    if min_education:
        has_education = _education_rank(education_level) >= _education_rank(str(min_education))
        meets = meets and has_education
        if has_education:
            reasons.append(f"学历识别为 {education_level}")
        else:
            risks.append(f"学历不满足 {min_education} 要求，识别为 {education_level or '未知'}")

    work_years_range = parsed_criteria.get("workYearsRange")
    if work_years_range:
        min_years, max_years = work_years_range
        has_work_years = work_years is not None and min_years <= work_years <= max_years and "应届生" not in summary
        meets = meets and has_work_years
        if has_work_years:
            reasons.append(f"工作经验识别为 {work_years} 年，符合 {min_years}-{max_years} 年")
        else:
            risks.append(f"工作经验不满足 {min_years}-{max_years} 年要求，识别为 {work_years if work_years is not None else '未知'}")

    min_work_years = None if related_experience.get("required") else parsed_criteria.get("minWorkYears")
    if min_work_years is not None and not work_years_range:
        has_work_years = work_years is not None and work_years >= int(min_work_years) and "应届生" not in summary
        meets = meets and has_work_years
        if has_work_years:
            reasons.append(f"工作经验识别为 {work_years} 年")
        else:
            risks.append(f"工作经验不满足 {min_work_years} 年要求，识别为 {work_years if work_years is not None else '未知'}")

    composite_match = {"required": False} if explicit_keywords else _match_composite_experience(criteria=criteria, resume_text=resume_text)
    if composite_match.get("required"):
        meets = meets and bool(composite_match.get("matched"))
        if composite_match.get("matched"):
            reasons.append(str(composite_match.get("reason") or "详情页识别到组合经验"))
        else:
            risks.append(str(composite_match.get("reason") or "详情页未识别到组合经验"))

    if related_experience.get("required"):
        meets = meets and bool(related_experience.get("matched"))
        if related_experience.get("matched"):
            reasons.append(str(related_experience.get("reason") or "识别到相关经验"))
        else:
            risks.append(str(related_experience.get("reason") or "未识别到足够相关经验"))

    if weak_company_evidence:
        risks.append("公司业务弱证据：" + "；".join(item.get("summary", "") for item in weak_company_evidence if item.get("summary")))

    keywords = [] if explicit_keywords or related_experience.get("required") else (parsed_criteria.get("experienceKeywords") or [])
    if keywords and not composite_match.get("required"):
        normalized_resume = resume_text.replace("\n", " ")
        matched_keywords = [keyword for keyword in keywords if keyword in normalized_resume]
        has_keyword = bool(matched_keywords)
        meets = meets and has_keyword
        if has_keyword:
            reasons.append("经验关键词匹配：" + "、".join(matched_keywords))
        else:
            risks.append("未识别到经验关键词：" + "、".join(keywords))

    if summary:
        reasons.append(f"页面摘要：{summary}")
    candidate_summary = _build_candidate_summary(
        name=name,
        education_level=education_level,
        work_years=work_years,
        summary=summary,
        resume_text=resume_text,
    )

    return {
        "agent": "rules",
        "name": name,
        "candidateSummary": candidate_summary,
        "age": age,
        "educationLevel": education_level,
        "workYears": work_years,
        "meetsCriteria": meets,
        "reasons": reasons,
        "risks": risks,
        "relatedExperience": related_experience,
        "confidenceScore": related_experience.get("confidenceScore", 100 if meets else 50),
        "confidenceLevel": related_experience.get("confidenceLevel", "high" if meets else "medium"),
        "confidenceReason": related_experience.get("confidenceReason", ""),
        "experienceEvidence": related_experience.get("experienceEvidence", related_experience.get("evidence", [])),
        "rejectedEvidence": related_experience.get("rejectedEvidence", []),
        "weakCompanyEvidence": weak_company_evidence,
    }


def _extract_name(lines: list[str]) -> str:
    for idx, line in enumerate(lines[1:], start=1):
        previous = lines[idx - 1]
        if ("K" in previous or "k" in previous or "面议" in previous) and not _looks_like_section_title(line):
            return line
    if len(lines) >= 2 and ("K" in lines[0] or "k" in lines[0] or "面议" in lines[0]):
        return lines[1]
    return lines[0] if lines else ""


def _looks_like_section_title(line: str) -> bool:
    return any(keyword in line for keyword in ("为你推荐", "当前职位", "牛人"))


def _extract_education_level(text: str) -> str:
    for level in ("博士", "硕士", "研究生", "本科", "大专", "专科", "中专"):
        if level in text:
            return "硕士" if level == "研究生" else "大专" if level == "专科" else level
    for level in ("博士", "硕士", "研究生", "本科", "大专"):
        if level in text:
            return level
    return ""


def _education_rank(level: str) -> int:
    ranks = {
        "": 0,
        "中专": 1,
        "大专": 2,
        "本科": 3,
        "研究生": 4,
        "硕士": 4,
        "博士": 5,
        "中专": 1,
        "大专": 2,
        "本科": 3,
        "研究生": 4,
        "硕士": 4,
        "博士": 5,
    }
    return ranks.get(level, 0)


def _extract_age(summary: str, text: str) -> int | None:
    source = summary or text
    real_match = re.search(r"(\d{1,2})\s*岁", source)
    if real_match:
        return int(real_match.group(1))
    match = re.search(r"(\d{1,2})\s*岁", source)
    if match:
        return int(match.group(1))
    return None


def _extract_work_years(summary: str, text: str) -> int | None:
    source = summary or text
    real_match = re.search(r"(?:^|[^\d])(\d{1,2})\s*年", source)
    if real_match:
        return int(real_match.group(1))
    match = re.search(r"(?:^|[^\d])(\d{1,2})\s*年", source)
    if match:
        return int(match.group(1))
    if re.search(r"(?:^|[^\d])三\s*年", source):
        return 3
    return None


def _build_candidate_summary(
    name: str,
    education_level: str,
    work_years: int | None,
    summary: str,
    resume_text: str,
) -> str:
    pieces = [name] if name else []
    if education_level:
        pieces.append(education_level)
    if work_years is not None:
        pieces.append(f"{work_years}年经验")
    direction = _extract_expected_direction(resume_text)
    if direction:
        pieces.append(direction)
    if not pieces and summary:
        return summary[:30]
    return "，".join(pieces)[:30]


def _extract_expected_direction(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if line == "期望" and idx + 1 < len(lines):
            return lines[idx + 1]
    return ""


def _match_related_experience(criteria: str, resume_text: str) -> dict[str, Any]:
    normalized_criteria = _normalize_keyword_text(criteria)
    needs_ecommerce = any(_normalize_keyword_text(term) in normalized_criteria for term in ECOMMERCE_TERMS)
    has_experience_word = "经验" in str(criteria or "")
    min_years = _parse_min_years(criteria)
    if not (needs_ecommerce and has_experience_word and min_years):
        return {"required": False}

    platform_terms = _criteria_platform_terms(criteria)
    structured = _match_structured_related_experience(
        criteria=criteria,
        resume_text=resume_text,
        platform_terms=_strict_platform_terms(platform_terms),
        min_years=min_years,
    )
    if structured.get("experienceEvidence") or structured.get("rejectedEvidence"):
        return structured

    strong_segments: list[dict[str, Any]] = []
    weak_segments: list[dict[str, Any]] = []
    for segment in _resume_evidence_segments(resume_text):
        matched_platform = _first_matched_term(segment, platform_terms)
        matched_roles = [term for term in ECOMMERCE_ROLE_TERMS if _normalize_keyword_text(term) in _normalize_keyword_text(segment)]
        years = _extract_segment_years(segment)
        if matched_platform and matched_roles:
            strong_segments.append(
                {
                    "platformTerm": matched_platform,
                    "roleTerms": matched_roles[:5],
                    "years": years,
                    "evidenceText": _trim_evidence_text(segment, max_length=220),
                    "weak": False,
                }
            )
        elif matched_platform:
            weak_segments.append(
                {
                    "platformTerm": matched_platform,
                    "roleTerms": [],
                    "years": years,
                    "evidenceText": _trim_evidence_text(segment, max_length=220),
                    "weak": True,
                    "reason": "缺少运营/店铺/活动/投放等职责证据",
                }
            )

    weak_company_evidence = _match_weak_company_evidence(resume_text)
    for evidence in weak_company_evidence:
        if evidence.get("hasRoleEvidence") and evidence.get("platformTags"):
            years = _extract_segment_years(str(evidence.get("evidenceText") or ""))
            strong_segments.append(
                {
                    "platformTerm": str(evidence["platformTags"][0]),
                    "roleTerms": evidence.get("roleTerms", [])[:5],
                    "years": years,
                    "evidenceText": str(evidence.get("evidenceText") or ""),
                    "weak": True,
                    "source": "company_business_alias",
                }
            )

    passing = [
        item for item in strong_segments
        if item.get("years") is not None and int(item["years"]) >= min_years
    ]
    if passing:
        evidence = passing[0]
        source = "公司业务弱证据辅助" if evidence.get("weak") else "简历明文"
        return {
            "required": True,
            "matched": True,
            "minYears": min_years,
            "platformTerms": platform_terms,
            "evidence": strong_segments + weak_segments,
            "weakCompanyEvidence": weak_company_evidence,
            "confidenceScore": 80 if evidence.get("weak") else 86,
            "confidenceLevel": "high",
            "confidenceReason": f"识别到{evidence.get('years')}年相关经验",
            "reason": f"{source}识别到{evidence.get('years')}年相关经验：{evidence.get('evidenceText')}",
        }

    if strong_segments:
        return {
            "required": True,
            "matched": False,
            "minYears": min_years,
            "platformTerms": platform_terms,
            "evidence": strong_segments + weak_segments,
            "weakCompanyEvidence": weak_company_evidence,
            "confidenceScore": 45,
            "confidenceLevel": "low",
            "confidenceReason": f"识别到相关平台/职责，但相关年限不足或无法确认，要求不少于{min_years}年",
            "reason": f"识别到相关平台/职责，但相关年限不足或无法确认，要求不少于{min_years}年",
        }

    return {
        "required": True,
        "matched": False,
        "minYears": min_years,
        "platformTerms": platform_terms,
        "evidence": weak_segments,
        "weakCompanyEvidence": weak_company_evidence,
        "confidenceScore": 20,
        "confidenceLevel": "low",
        "confidenceReason": "未在同一段经历中同时识别到平台/业务词和运营职责证据",
        "reason": "未在同一段经历中同时识别到平台/业务词和运营职责证据",
    }


def _match_structured_related_experience(
    criteria: str,
    resume_text: str,
    platform_terms: list[str],
    min_years: int,
) -> dict[str, Any]:
    del criteria
    min_months = int(min_years) * 12
    experiences = _extract_structured_work_experiences(resume_text)
    weak_company_evidence = _match_weak_company_evidence(resume_text)
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    explicit_evidence = _extract_explicit_related_year_evidence(resume_text, platform_terms)

    for item in experiences:
        text = str(item.get("text") or "")
        if _looks_like_communication_context(text):
            rejected.append({**item, "reason": "只出现在沟通职位或沟通记录中"})
            continue
        platform_matches = _matched_terms(text, platform_terms)
        role_matches = _matched_terms(text, _strong_ecommerce_role_terms())
        weak_alias = _matching_weak_company_evidence(item, weak_company_evidence)
        weak = False
        if not platform_matches and weak_alias and weak_alias.get("platformTags"):
            platform_matches = [str(term) for term in weak_alias.get("platformTags", []) if str(term).strip()]
            weak = True
        months = item.get("months")
        if platform_matches and role_matches and isinstance(months, int) and months > 0:
            evidence.append(
                {
                    "company": item.get("company", ""),
                    "title": item.get("title", ""),
                    "start": item.get("start", ""),
                    "end": item.get("end", ""),
                    "months": months,
                    "years": round(months / 12, 2),
                    "platformTerms": platform_matches[:6],
                    "roleTerms": role_matches[:8],
                    "evidenceText": _trim_evidence_text(text, max_length=260),
                    "weak": weak,
                    "source": "company_business_alias" if weak else "structured_work_experience",
                }
            )
        elif platform_matches or role_matches:
            reason = "时间无法解析" if not isinstance(months, int) else "缺少平台词或运营职责词"
            rejected.append(
                {
                    **item,
                    "platformTerms": platform_matches[:6],
                    "roleTerms": role_matches[:8],
                    "reason": reason,
                }
            )
        else:
            rejected.append({**item, "reason": "缺少平台词和运营职责词"})

    for item in explicit_evidence:
        evidence.append(item)

    total_months = sum(int(item.get("months") or 0) for item in evidence)
    has_dated_evidence = any(item.get("source") != "explicit_year_phrase" for item in evidence)
    has_explicit_evidence = any(item.get("source") == "explicit_year_phrase" for item in evidence)
    matched = total_months >= min_months and bool(evidence)
    if matched and (has_dated_evidence or has_explicit_evidence):
        confidence_level = "high"
        confidence_score = 92 if has_dated_evidence else 86
    elif evidence and total_months >= max(1, min_months - 6):
        confidence_level = "medium"
        confidence_score = 68
    elif evidence:
        confidence_level = "medium" if total_months >= 12 else "low"
        confidence_score = 55 if confidence_level == "medium" else 35
    elif rejected:
        confidence_level = "low"
        confidence_score = 25
    else:
        confidence_level = "low"
        confidence_score = 10

    if matched:
        reason = f"累计识别到{total_months}个月相关电商/天猫运营经验，达到{min_months}个月要求"
    elif evidence:
        reason = f"累计识别到{total_months}个月相关经验，未达到{min_months}个月要求"
    else:
        reason = "未识别到可验证年限的天猫/电商运营经历"

    return {
        "required": True,
        "matched": bool(matched and confidence_level == "high"),
        "minYears": min_years,
        "minMonths": min_months,
        "totalRelatedMonths": total_months,
        "platformTerms": platform_terms,
        "evidence": evidence,
        "experienceEvidence": evidence,
        "rejectedEvidence": rejected,
        "weakCompanyEvidence": weak_company_evidence,
        "confidenceScore": confidence_score,
        "confidenceLevel": confidence_level,
        "confidenceReason": reason,
        "reason": reason,
    }


def _criteria_platform_terms(criteria: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in ECOMMERCE_TERMS:
        if _normalize_keyword_text(term) in _normalize_keyword_text(criteria):
            for synonym in _expand_keyword_synonyms(term):
                normalized = _normalize_keyword_text(synonym)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    terms.append(synonym)
    if not terms and "电商" in criteria:
        terms = list(ECOMMERCE_TERMS)
    return terms or list(ECOMMERCE_TERMS)


def _strict_platform_terms(terms: list[str]) -> list[str]:
    allowed = set(ECOMMERCE_TERMS)
    strict: list[str] = []
    seen: set[str] = set()
    for term in terms or list(ECOMMERCE_TERMS):
        normalized = _normalize_keyword_text(term)
        if term in allowed and normalized not in seen:
            seen.add(normalized)
            strict.append(term)
    return strict or list(ECOMMERCE_TERMS)


def _extract_structured_work_experiences(resume_text: str) -> list[dict[str, Any]]:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(resume_text or "").splitlines()
        if line.strip()
    ]
    experiences: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        period = _find_year_month_period(line)
        if not period:
            continue
        context_lines = [line]
        for offset in range(1, 6):
            if index + offset >= len(lines):
                break
            next_line = lines[index + offset]
            if _find_year_month_period(next_line):
                break
            if _looks_like_resume_section_boundary(next_line):
                break
            context_lines.append(next_line)
        if _is_education_context(" ".join(context_lines)):
            continue
        company, title = _parse_company_title_from_period_line(line, period["raw"])
        text = " ".join(context_lines)
        experiences.append(
            {
                "company": company,
                "title": title,
                "start": period["start"],
                "end": period["end"],
                "months": period["months"],
                "text": text,
                "raw": line,
            }
        )
    return experiences


def _find_year_month_period(value: str) -> dict[str, Any] | None:
    text = str(value or "")
    pattern = re.compile(
        r"(?P<start>(?:19|20)\d{2}(?:[./年-]\d{1,2})?)\s*"
        r"(?:-|－|–|—|~|～|至|到)\s*"
        r"(?P<end>至今|现在|目前|今|在职|present|Present|(?:19|20)\d{2}(?:[./年-]\d{1,2})?)"
    )
    match = pattern.search(text)
    if not match:
        return None
    start = _parse_year_month_value(match.group("start"), default_month=1)
    end = _parse_period_end_value(match.group("end"))
    if not start or not end:
        return None
    months = _months_between_inclusive(start, end)
    if months <= 0:
        return None
    return {
        "raw": match.group(0),
        "start": f"{start[0]:04d}-{start[1]:02d}",
        "end": "至今" if _is_current_period_end_text(match.group("end")) else f"{end[0]:04d}-{end[1]:02d}",
        "months": months,
    }


def _parse_company_title_from_period_line(line: str, period_text: str) -> tuple[str, str]:
    cleaned = str(line or "").replace(period_text, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,|")
    if not cleaned:
        return "", ""
    parts = [part.strip() for part in re.split(r"[，,|]", cleaned) if part.strip()]
    first = parts[0] if parts else cleaned
    role_terms = _strong_ecommerce_role_terms() + tuple(ECOMMERCE_ROLE_TERMS)
    if "·" in first:
        company, title = [part.strip() for part in first.split("·", 1)]
        return company, title
    if " - " in first:
        title = first.split(" - ", 1)[0].strip()
        company = parts[0].replace(title, "").strip(" ，,|-")
        return company, title
    matched_role = next((term for term in role_terms if term and term in first), "")
    if matched_role:
        role_index = first.find(matched_role)
        return first[:role_index].strip(" ，,|-"), first[role_index:].strip(" ，,|-")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", first


def _extract_explicit_related_year_evidence(resume_text: str, platform_terms: list[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for segment in _resume_evidence_segments(resume_text):
        if _looks_like_communication_context(segment):
            continue
        platform_matches = _matched_terms(segment, platform_terms)
        role_matches = _matched_terms(segment, _strong_ecommerce_role_terms())
        if not platform_matches or not role_matches:
            continue
        years = _extract_explicit_years_near_related_terms(segment)
        if years is None:
            continue
        months = int(years * 12)
        evidence.append(
            {
                "company": "",
                "title": "",
                "start": "",
                "end": "",
                "months": months,
                "years": years,
                "platformTerms": platform_matches[:6],
                "roleTerms": role_matches[:8],
                "evidenceText": _trim_evidence_text(segment, max_length=260),
                "weak": False,
                "source": "explicit_year_phrase",
            }
        )
        break
    return evidence


def _matching_weak_company_evidence(
    experience: dict[str, Any],
    weak_company_evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    company = str(experience.get("company") or "")
    text = str(experience.get("text") or "")
    for item in weak_company_evidence:
        names = [
            str(item.get("company") or ""),
            str(item.get("matchedName") or ""),
        ]
        if any(name and (name in company or name in text) for name in names):
            return item
    return None


def _extract_explicit_years_near_related_terms(segment: str) -> int | None:
    text = str(segment or "")
    patterns = [
        r"(\d{1,2})\s*年\s*(?:\+|以上)?[^。；;，,\n]{0,24}(?:天猫|淘宝|电商|店铺|平台|运营|操盘|店长)",
        r"(?:天猫|淘宝|电商|店铺|平台|运营|操盘|店长)[^。；;，,\n]{0,24}(\d{1,2})\s*年\s*(?:\+|以上)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _matched_terms(text: str, terms: tuple[str, ...] | list[str]) -> list[str]:
    normalized = _normalize_keyword_text(text)
    matched: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized_term = _normalize_keyword_text(str(term))
        if normalized_term and normalized_term in normalized and normalized_term not in seen:
            seen.add(normalized_term)
            matched.append(str(term))
    return matched


def _strong_ecommerce_role_terms() -> tuple[str, ...]:
    return (
        "天猫运营", "淘宝运营", "电商运营", "店铺运营", "平台运营", "商品运营", "类目运营", "店长",
        "运营", "推广", "投放", "直通车", "万相台", "引力魔方", "生意参谋", "品销宝",
        "活动策划", "大促", "爆款", "选品", "上新", "标题优化", "主图", "详情页", "转化率", "GMV",
    )


def _looks_like_communication_context(text: str) -> bool:
    markers = ("沟通职位", "发起沟通", "未处理", "继续沟通", "同事沟通进度", "我的沟通进度", "求简历")
    return any(marker in str(text or "") for marker in markers)


def _looks_like_resume_section_boundary(line: str) -> bool:
    return any(marker in str(line or "") for marker in ("教育经历", "项目经历", "资格证书", "牛人分析器", "期望职位"))


def _is_education_context(text: str) -> bool:
    markers = ("大学", "学院", "学校", "本科", "大专", "专科", "硕士", "博士", "中专", "中技", "教育")
    return any(marker in str(text or "") for marker in markers) and not any(
        marker in str(text or "") for marker in ("运营", "店长", "推广", "投放")
    )


def _parse_year_month_value(value: str, default_month: int) -> tuple[int, int] | None:
    match = re.search(r"(?P<year>(?:19|20)\d{2})(?:[./年-](?P<month>\d{1,2}))?", str(value or ""))
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month") or default_month)
    return year, max(1, min(12, month))


def _parse_period_end_value(value: str) -> tuple[int, int] | None:
    if _is_current_period_end_text(value):
        today = date.today()
        return today.year, today.month
    return _parse_year_month_value(value, default_month=12)


def _is_current_period_end_text(value: str) -> bool:
    return str(value or "").strip().lower() in {"至今", "现在", "目前", "今", "在职", "present"}


def _months_between_inclusive(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


def _resume_evidence_segments(resume_text: str) -> list[str]:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(resume_text or "").splitlines()
        if line.strip()
    ]
    segments = list(lines)
    for index in range(len(lines) - 1):
        segments.append(lines[index] + " " + lines[index + 1])
    for index in range(len(lines) - 2):
        segments.append(lines[index] + " " + lines[index + 1] + " " + lines[index + 2])
    compact = re.sub(r"\s+", " ", str(resume_text or "")).strip()
    if compact:
        segments.append(compact)
    seen: set[str] = set()
    unique_segments: list[str] = []
    for segment in segments:
        normalized = _normalize_keyword_text(segment)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_segments.append(segment)
    return unique_segments


def _first_matched_term(text: str, terms: list[str]) -> str:
    normalized_text = _normalize_keyword_text(text)
    return next((term for term in terms if _normalize_keyword_text(term) in normalized_text), "")


def _extract_segment_years(segment: str) -> int | None:
    numeric_patterns = [
        r"(\d{1,2})\s*年\s*(?:以上)?\s*(?:天猫|淘宝|京东|抖音|小红书|拼多多|电商|店铺|平台|运营|经验)",
        r"(?:天猫|淘宝|京东|抖音|小红书|拼多多|电商|店铺|平台|运营|经验)[^\d]{0,12}(\d{1,2})\s*年",
    ]
    for pattern in numeric_patterns:
        match = re.search(pattern, segment, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    chinese_patterns = [
        r"([一二两三四五六七八九十])\s*年\s*(?:以上)?\s*(?:天猫|淘宝|京东|抖音|小红书|拼多多|电商|店铺|平台|运营|经验)",
        r"(?:天猫|淘宝|京东|抖音|小红书|拼多多|电商|店铺|平台|运营|经验)[^一二两三四五六七八九十]{0,12}([一二两三四五六七八九十])\s*年",
    ]
    for pattern in chinese_patterns:
        match = re.search(pattern, segment, flags=re.IGNORECASE)
        if match:
            return _parse_chinese_or_digit_number(match.group(1))

    date_ranges = re.findall(
        r"((?:19|20)\d{2})(?:\.\d{1,2})?\s*(?:-|至|到|~|～)\s*((?:19|20)\d{2}|至今)",
        segment,
    )
    years = []
    for start, end in date_ranges:
        end_year = 2026 if end == "至今" else int(end)
        if end_year >= int(start):
            years.append(end_year - int(start))
    return max(years) if years else None


def _parse_min_years(criteria: str) -> int | None:
    range_match = re.search(
        r"(\d{1,2}|[一二两三四五六七八九十])\s*[-到至~～]\s*(\d{1,2}|[一二两三四五六七八九十])\s*年",
        criteria,
    )
    if range_match:
        return _parse_chinese_or_digit_number(range_match.group(1))
    year_match = re.search(r"(\d{1,2}|[一二两三四五六七八九十])\s*年\s*(?:以上|工作|经验)?", criteria)
    if year_match:
        return _parse_chinese_or_digit_number(year_match.group(1))
    return None


def _match_weak_company_evidence(resume_text: str) -> list[dict[str, Any]]:
    aliases = _load_company_business_aliases()
    if not aliases:
        return []
    evidence: list[dict[str, Any]] = []
    segments = _resume_evidence_segments(resume_text)
    for alias in aliases:
        names = [str(alias.get("company") or ""), *[str(item) for item in alias.get("aliases", [])]]
        names = [name for name in names if name.strip()]
        for segment in segments:
            matched_name = next((name for name in names if name and name in segment), "")
            if not matched_name:
                continue
            role_terms = [
                term for term in ECOMMERCE_ROLE_TERMS
                if _normalize_keyword_text(term) in _normalize_keyword_text(segment)
            ]
            platform_tags = [str(item) for item in alias.get("platformTags", []) if str(item).strip()]
            business_tags = [str(item) for item in alias.get("businessTags", []) if str(item).strip()]
            summary = (
                f"{matched_name} 映射到平台：{','.join(platform_tags) or '未知'}"
                f"；业务：{','.join(business_tags) or '未知'}"
            )
            evidence.append(
                {
                    "company": str(alias.get("company") or matched_name),
                    "matchedName": matched_name,
                    "platformTags": platform_tags,
                    "businessTags": business_tags,
                    "description": str(alias.get("description") or ""),
                    "evidenceText": _trim_evidence_text(segment, max_length=220),
                    "roleTerms": role_terms,
                    "hasRoleEvidence": bool(role_terms),
                    "summary": summary,
                }
            )
            break
    return evidence


def _load_company_business_aliases() -> list[dict[str, Any]]:
    path = Path.cwd() / "knowledge" / "company_business_aliases.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("companies"), list):
        return [item for item in payload["companies"] if isinstance(item, dict)]
    return []


def _match_composite_experience(criteria: str, resume_text: str) -> dict[str, Any]:
    normalized_criteria = str(criteria or "").lower()
    normalized_resume = str(resume_text or "").lower().replace("\n", " ")
    needs_ecommerce = any(
        keyword in normalized_criteria
        for keyword in (
            "\u7535\u5546",
            "\u8de8\u5883",
            "\u5929\u732b",
            "\u6dd8\u5b9d",
            "\u4e9a\u9a6c\u900a",
            "amazon",
            "tiktok",
            "\u72ec\u7acb\u7ad9",
            "\u5916\u8d38",
        )
    )
    needs_recruiting = any(
        keyword in normalized_criteria
        for keyword in ("\u62db\u8058", "\u730e\u5934", "hrbp", "\u4eba\u624d", "\u4eba\u529b")
    )
    if not (needs_ecommerce and needs_recruiting):
        return {"required": False}

    ecommerce_terms = [
        "\u7535\u5546",
        "\u8de8\u5883\u7535\u5546",
        "\u8de8\u5883",
        "\u5929\u732b",
        "\u6dd8\u5b9d",
        "\u4e9a\u9a6c\u900a",
        "amazon",
        "tiktok",
        "\u72ec\u7acb\u7ad9",
        "\u5916\u8d38",
        "\u7ebf\u4e0a",
        "\u96f6\u552e",
        "\u5feb\u6d88",
        "\u7f8e\u5986",
        "3c",
    ]
    recruiting_terms = [
        "\u62db\u8058",
        "\u730e\u5934",
        "hrbp",
        "\u4eba\u529b\u8d44\u6e90",
        "\u4eba\u624d",
        "\u56e2\u961f\u642d\u5efa",
        "\u5c97\u4f4d",
        "\u914d\u7f6e",
        "\u9009\u7528\u7559",
        "\u4eba\u4e8b",
    ]
    matched_ecommerce = [term for term in ecommerce_terms if term in normalized_resume]
    matched_recruiting = [term for term in recruiting_terms if term in normalized_resume]
    matched = bool(matched_ecommerce and matched_recruiting)
    if matched:
        return {
            "required": True,
            "matched": True,
            "reason": "详情页同时识别到电商相关经验（"
            + "、".join(matched_ecommerce[:4])
            + "）和招聘/HR相关经验（"
            + "、".join(matched_recruiting[:4])
            + "）",
        }
    missing = []
    if not matched_ecommerce:
        missing.append("电商相关经验")
    if not matched_recruiting:
        missing.append("招聘/HR相关经验")
    return {
        "required": True,
        "matched": False,
        "reason": "详情页未同时识别到" + "、".join(missing),
    }


def _parse_criteria(criteria: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    real_age_match = re.search(r"(\d{1,2})\s*[-到至~～]\s*(\d{1,2})\s*岁?", criteria)
    if real_age_match:
        parsed["ageRange"] = (int(real_age_match.group(1)), int(real_age_match.group(2)))

    for level in ("博士", "硕士", "研究生", "本科", "大专", "专科", "中专"):
        if level in criteria:
            parsed["minEducation"] = "硕士" if level == "研究生" else "大专" if level == "专科" else level
            break

    real_years_range_match = re.search(
        r"(\d{1,2}|[一二两三四五六七八九十])\s*[-到至~～]\s*(\d{1,2}|[一二两三四五六七八九十])\s*年",
        criteria,
    )
    if real_years_range_match:
        parsed["workYearsRange"] = (
            _parse_chinese_or_digit_number(real_years_range_match.group(1)),
            _parse_chinese_or_digit_number(real_years_range_match.group(2)),
        )

    real_year_match = re.search(r"(\d{1,2}|[一二两三四五六七八九十])\s*年\s*(?:以上|工作|经验)?", criteria)
    if real_year_match and "workYearsRange" not in parsed:
        parsed["minWorkYears"] = _parse_chinese_or_digit_number(real_year_match.group(1))

    real_keywords = [
        "前台", "行政", "人事", "文员", "招聘", "客服", "会务",
        "天猫", "淘宝", "京东", "抖音", "小红书", "美妆", "个护",
        "投放", "推广", "活动", "增长", "分销", "渠道", "主播",
    ]
    matched_real_keywords = [keyword for keyword in real_keywords if keyword in criteria]
    if matched_real_keywords:
        parsed["experienceKeywords"] = matched_real_keywords
    age_match = re.search(r"(\d{1,2})\s*[-到至~～]\s*(\d{1,2})\s*岁?", criteria)
    if age_match:
        parsed["ageRange"] = (int(age_match.group(1)), int(age_match.group(2)))

    for level in ("博士", "硕士", "研究生", "本科", "大专", "专科"):
        if level in criteria:
            if level == "研究生":
                parsed["minEducation"] = "硕士"
            elif level == "专科":
                parsed["minEducation"] = "大专"
            else:
                parsed["minEducation"] = level
            break

    years_range_match = re.search(
        r"(\d{1,2}|[一二两三四五六七八九十])\s*[-到至~～]\s*(\d{1,2}|[一二两三四五六七八九十])\s*年",
        criteria,
    )
    if years_range_match:
        parsed["workYearsRange"] = (
            _parse_chinese_or_digit_number(years_range_match.group(1)),
            _parse_chinese_or_digit_number(years_range_match.group(2)),
        )

    year_match = re.search(r"(\d{1,2}|[一二两三四五六七八九十])\s*年(?:以上|前台|行政|工作|经验)?", criteria)
    if year_match:
        parsed["minWorkYears"] = _parse_chinese_or_digit_number(year_match.group(1))

    keywords: list[str] = []
    for keyword in ("前台", "行政", "人事", "文员", "招聘", "客服", "会务"):
        if keyword in criteria:
            keywords.append(keyword)
    if keywords:
        parsed["experienceKeywords"] = keywords
    return parsed


def _parse_chinese_or_digit_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return mapping.get(value, 0)
