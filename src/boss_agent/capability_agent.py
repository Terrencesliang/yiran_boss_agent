from __future__ import annotations

from typing import Any


CAPABILITY_GROUPS: dict[str, list[str]] = {
    "independent_delivery": ["独立", "主导", "牵头", "统筹", "负责", "推进", "从0到1", "自主"],
    "cross_function_coordination": ["跨部门", "协调", "沟通", "对接", "协同", "供应商", "部门", "团队", "客户"],
    "cost_efficiency": ["降本", "增效", "提效", "节省", "节约", "成本", "预算", "费用", "比价", "议价", "库存", "损耗", "周转"],
    "process_optimization": ["流程", "优化", "制度", "SOP", "标准化", "规范", "台账", "表单", "梳理", "改进"],
    "event_landing": ["年会", "团建", "活动", "会务", "会议", "接待", "培训", "展会", "生日会", "落地", "组织"],
}

PROJECT_KEYWORDS = [
    "年会",
    "团建",
    "活动",
    "会务",
    "采购",
    "库存",
    "供应商",
    "物料",
    "仓库",
    "资产",
]


def analyze_operations_capability(
    resume_text: str,
    candidate_name: str = "",
    criteria: str = "",
) -> dict[str, Any]:
    lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
    evidence: dict[str, list[str]] = {}
    matched_capabilities: list[str] = []

    for capability, keywords in CAPABILITY_GROUPS.items():
        snippets = _matching_lines(lines, keywords)
        evidence[capability] = snippets
        if snippets:
            matched_capabilities.append(capability)

    project_evidence = _matching_lines(lines, PROJECT_KEYWORDS)
    capability_count = len(matched_capabilities)
    has_project = bool(project_evidence)
    meets = capability_count >= 4 and has_project

    reasons: list[str] = []
    if meets:
        reasons.append(f"匹配 {capability_count}/5 项核心能力，并有项目/场景证据")
    elif capability_count >= 3 and has_project:
        reasons.append(f"接近匹配：有 {capability_count}/5 项能力和项目证据，但能力项不够完整")
    else:
        reasons.append(f"能力证据不足：仅匹配 {capability_count}/5 项核心能力")

    for capability in matched_capabilities:
        label = _capability_label(capability)
        reasons.append(f"{label}：{evidence[capability][0]}")
    if project_evidence:
        reasons.append(f"项目证据：{project_evidence[0]}")

    risks = [
        f"缺少{_capability_label(capability)}证据"
        for capability in CAPABILITY_GROUPS
        if capability not in matched_capabilities
    ]
    if not project_evidence:
        risks.append("缺少年会/团建/采购/库存等项目型证据")

    return {
        "agent": "operations_capability_rules",
        "name": candidate_name or _guess_name(lines),
        "candidateSummary": _candidate_summary(lines),
        "criteria": criteria,
        "meetsCriteria": meets,
        "capabilityCount": capability_count,
        "matchedCapabilities": matched_capabilities,
        "projectEvidence": project_evidence[:3],
        "evidence": evidence,
        "reasons": reasons,
        "risks": risks,
    }


def _matching_lines(lines: list[str], keywords: list[str]) -> list[str]:
    matches: list[str] = []
    for line in lines:
        if any(keyword in line for keyword in keywords):
            normalized = " ".join(line.split())
            if normalized not in matches:
                matches.append(normalized[:160])
        if len(matches) >= 3:
            break
    return matches


def _capability_label(capability: str) -> str:
    labels = {
        "independent_delivery": "独立推进",
        "cross_function_coordination": "跨部门协调",
        "cost_efficiency": "降本增效",
        "process_optimization": "流程优化",
        "event_landing": "活动落地",
    }
    return labels.get(capability, capability)


def _guess_name(lines: list[str]) -> str:
    if not lines:
        return ""
    for idx, line in enumerate(lines[1:], start=1):
        previous = lines[idx - 1]
        if ("K" in previous or "面议" in previous) and len(line) <= 12:
            return line
    return lines[0][:20]


def _candidate_summary(lines: list[str]) -> str:
    useful = [
        line for line in lines
        if any(keyword in line for keyword in ["岁", "年", "本科", "大专", "行政", "人事", "前台", "助理"])
    ]
    return "；".join(useful[:2])[:120]
