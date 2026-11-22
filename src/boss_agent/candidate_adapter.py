"""Adapt verified recommendation details to the common evidence input."""

import re
from dataclasses import asdict, dataclass
from typing import Any

from boss_agent.resume_structure import split_resume_sources
from boss_agent.rule_engine import ResumeEvidence, extract_resume_evidence


PRIVATE_LINE = re.compile(
    r"(?im)^\s*(?:姓名|性别|年龄|婚姻(?:状况)?|婚育(?:状况)?|民族|宗教|户籍|籍贯|住址|家庭地址|"
    r"联系方式|联系电话|手机(?:号)?|电话|微信|邮[箱件]|出生日期|身份证(?:号)?|gender|age|address|phone|email)\s*[:：][^\r\n]*"
)
PRIVATE_TOKEN = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dX])(?!\d)", re.I
)
SECTION = re.compile(r"(?m)^\s*(教育经历|工作经历|项目经历)\s*[:：]?\s*$")


@dataclass(frozen=True)
class StandardCandidate:
    candidate_id: str
    platform: str
    job_id: str | None
    card_data: dict[str, Any]
    education: list[dict[str, Any]]
    work_experiences: list[dict[str, Any]]
    project_experiences: list[dict[str, Any]]
    resume_text: str
    evidence_sources: dict[str, str]
    capture_status: str
    capture_warnings: list[str]
    skills: list[dict[str, Any]]
    certifications: list[dict[str, Any]]
    achievements: list[dict[str, Any]]
    raw_evidence: list[dict[str, Any]]
    evidence_blocks: list[dict[str, Any]]
    structured_evidence: ResumeEvidence

    def to_ranking_input(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "candidate_name": self.card_data.get("candidate_name"),
            "sources": self.evidence_sources,
            "capture_status": self.capture_status,
            "capture_warnings": self.capture_warnings,
            "structured_evidence": self.structured_evidence,
        }


def stable_candidate_id(card: dict) -> str | None:
    value = card.get("candidateId")
    return value if isinstance(value, str) and value.strip() and value == value.strip() else None


def _minimize(text: str, name: str) -> str:
    def mask(match: re.Match) -> str:
        return re.sub(r"[^\r\n]", "█", match[0])

    text = PRIVATE_TOKEN.sub(mask, PRIVATE_LINE.sub(mask, text))
    return text.replace(name, "█" * len(name)) if len(name) >= 2 else text


def _sections(sources: dict[str, str]) -> dict[str, list[dict]]:
    sections = {"教育经历": [], "工作经历": [], "项目经历": []}
    for source, text in sources.items():
        headings = list(SECTION.finditer(text))
        for index, heading in enumerate(headings):
            start = heading.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            sections[heading[1]].append({"source": source, "start": start, "end": end, "text": text[start:end]})
    return sections


def adapt_recommend_candidate(
    card: dict, opened: dict, detail: dict, *, job_id: str | None = None,
) -> StandardCandidate:
    candidate_id = stable_candidate_id(card)
    if candidate_id is None:
        raise ValueError("candidate_id_required")
    if not opened.get("opened") or opened.get("candidateId") != candidate_id:
        raise ValueError("card_open_failed_or_identity_mismatch")
    if detail.get("captureStatus") == "detail_changed_during_ocr":
        raise ValueError("detail_changed_during_ocr")
    if detail.get("captureStatus") == "detail_missing":
        raise ValueError("detail_missing")
    if detail.get("detailCandidateId") != candidate_id:
        raise ValueError("detail_identity_mismatch_or_unverified")
    if detail.get("detailOpen") is not True or detail.get("captureStatus") != "ready":
        raise ValueError("detail_not_ready")

    warnings = []
    dom = str(detail.get("fullDomText") or "")
    if not dom.strip():
        warnings.append("dom_text_missing")
        dom = str(detail.get("rawTextPreview") or "") if not detail.get("ocrText") else ""
        if dom:
            warnings.append("dom_preview_only")
    if detail.get("domTruncated"):
        warnings.append("dom_text_truncated")

    ocr = str(detail.get("ocrText") or "")
    ocr_status = detail.get("ocrStatus")
    if detail.get("canvasExpected") or ocr_status or ocr:
        useful = re.findall(r"[a-zA-Z0-9\u3400-\u9fff]", ocr)
        if not ocr.strip() or len(useful) < 8 or len(useful) / max(1, len(ocr.strip())) < 0.35:
            warnings.append("ocr_missing_or_low_quality")
            ocr = ""
        elif ocr_status != "ok":
            warnings.append("ocr_partial_or_failed")
        if detail.get("ocrTruncated"):
            warnings.append("ocr_capture_truncated")

    name = str(card.get("candidateName") or "").strip()
    sources = {key: _minimize(text, name) for key, text in {"dom": dom, "ocr": ocr}.items() if text.strip()}
    if not any(re.search(r"[\w\u3400-\u9fff]", text) for text in sources.values()):
        raise ValueError("resume_evidence_missing")
    sections = _sections(sources)
    capture_status = "partial" if warnings else "complete"
    structured = extract_resume_evidence(sources, candidate_id=candidate_id, capture_status=capture_status)
    rows = [item.to_dict() for item in structured.observations]

    def matching(*fields: str) -> list[dict]:
        return [row for row in rows if row["field"] in fields]
    return StandardCandidate(
        candidate_id=candidate_id, platform="boss_zhipin", job_id=job_id,
        card_data={"candidate_id": candidate_id, "candidate_name": name},
        education=sections["教育经历"] + matching("education_level", "education_mode", "major", "graduation_year"),
        work_experiences=sections["工作经历"] + [asdict(item) for item in structured.work_records],
        project_experiences=sections["项目经历"] + matching("project_record"),
        skills=matching("skills", "tools", "hands_on_tools"), certifications=matching("certificates"),
        achievements=matching("achievement"), raw_evidence=rows,
        evidence_blocks=[asdict(block) for block in split_resume_sources(sources)], resume_text="\n\n".join(sources.values()),
        evidence_sources=sources, capture_status=capture_status,
        capture_warnings=warnings, structured_evidence=structured,
    )
