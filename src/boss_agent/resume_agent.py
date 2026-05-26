from __future__ import annotations

import json
import os
import re
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
            return _apply_explicit_keyword_check(result, resume_text=resume_text, criteria=criteria)
        except Exception as exc:
            fallback = _analyze_with_rules(resume_text=resume_text, criteria=criteria)
            fallback["agent"] = "rules_fallback_after_llm_error"
            fallback["llmError"] = str(exc)
            return _apply_explicit_keyword_check(fallback, resume_text=resume_text, criteria=criteria)

    fallback = _analyze_with_rules(resume_text=resume_text, criteria=criteria)
    fallback["agent"] = "rules_fallback_no_llm_config"
    return _apply_explicit_keyword_check(fallback, resume_text=resume_text, criteria=criteria)


def _strip_non_resume_sections(resume_text: str) -> str:
    text = str(resume_text or "")
    cut_markers = [
        "\u5408\u4f5c\u4e13\u4eab",
        "\u540c\u4e8b\u6c9f\u901a\u8fdb\u5ea6",
        "\u6211\u7684\u6c9f\u901a\u8fdb\u5ea6",
        "\u5411Ta\u53d1\u8d77\u6c9f\u901a",
        "\u5411ta\u53d1\u8d77\u6c9f\u901a",
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
    keywords = _extract_explicit_keywords(criteria)
    if not keywords:
        return result

    keyword_match = _match_keyword_groups(resume_text=resume_text, keywords=keywords)
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
        risks.append("未识别到关键词或同义词：" + "、".join(keywords))

    updated["meetsCriteria"] = bool(updated.get("meetsCriteria")) and bool(keyword_match["matched"])
    updated["reasons"] = reasons
    updated["risks"] = risks
    updated["keywordMatch"] = keyword_match
    return updated


def _extract_explicit_keywords(criteria: str) -> list[str]:
    match = re.search(r"(?:关键词|关键字)\s*[:：]\s*(.+)", str(criteria or ""), flags=re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1).splitlines()[0]
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
    return keywords


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


def _match_keyword_groups(resume_text: str, keywords: list[str]) -> dict[str, Any]:
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
    return {
        "required": True,
        "matched": bool(matches),
        "keywords": keywords,
        "expanded": expanded,
        "matches": matches,
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

    min_work_years = parsed_criteria.get("minWorkYears")
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

    keywords = [] if explicit_keywords else (parsed_criteria.get("experienceKeywords") or [])
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
