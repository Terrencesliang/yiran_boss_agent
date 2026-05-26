from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def load_knowledge_base(base_dir: Path) -> dict[str, Any]:
    jobs_dir = base_dir / "jobs"
    company_dir = base_dir / "company"
    jobs: dict[str, dict[str, Any]] = {}
    company: dict[str, Any] = {"sections": {}, "faq": []}

    if jobs_dir.exists():
        for path in jobs_dir.glob("*.txt"):
            parsed = parse_knowledge_txt(path.read_text(encoding="utf-8"))
            job_name = parsed["sections"].get("岗位名称") or path.stem
            jobs[job_name] = parsed

    if company_dir.exists():
        txt_files = sorted(company_dir.glob("*.txt"))
        if txt_files:
            merged_sections: dict[str, str] = {}
            merged_faq: list[dict[str, str]] = []
            for path in txt_files:
                parsed = parse_knowledge_txt(path.read_text(encoding="utf-8"))
                merged_sections.update(parsed["sections"])
                merged_faq.extend(parsed["faq"])
            company = {"sections": merged_sections, "faq": merged_faq}

    return {
        "jobs": jobs,
        "company": company,
    }


def parse_knowledge_txt(content: str) -> dict[str, Any]:
    sections: dict[str, str] = {}
    faq: list[dict[str, str]] = []
    current_section: str | None = None
    current_lines: list[str] = []

    def flush_section() -> None:
        nonlocal current_section, current_lines
        if not current_section:
            return
        body = "\n".join(line for line in current_lines).strip()
        if current_section == "常见问答":
            faq.extend(parse_faq_block(body))
        else:
            sections[current_section] = body
        current_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_section()
            current_section = line[1:-1].strip()
            continue
        current_lines.append(line)

    flush_section()
    return {"sections": sections, "faq": faq}


def parse_faq_block(content: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    question = ""
    answer_lines: list[str] = []

    def flush_item() -> None:
        nonlocal question, answer_lines
        if question:
            items.append(
                {
                    "question": question,
                    "answer": "\n".join(answer_lines).strip(),
                }
            )
        question = ""
        answer_lines = []

    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"^[Qq][:：]", stripped):
            flush_item()
            question = re.sub(r"^[Qq][:：]\s*", "", stripped)
        elif re.match(r"^[Aa][:：]", stripped):
            answer_lines.append(re.sub(r"^[Aa][:：]\s*", "", stripped))
        else:
            answer_lines.append(stripped)

    flush_item()
    return [item for item in items if item["question"] and item["answer"]]


def answer_question_from_knowledge(conversation: dict[str, Any], knowledge: dict[str, Any]) -> str | None:
    messages = conversation.get("messages", [])
    if not messages:
        return None

    latest = messages[-1]
    if latest.get("direction") != "inbound":
        return None

    query = normalize_text(latest.get("text", ""))
    if not query:
        return None

    job_doc = find_job_doc(conversation.get("jobTitle", ""), knowledge.get("jobs", {}))
    if job_doc:
        answer = find_faq_answer(query, job_doc.get("faq", []))
        if answer:
            return answer

    company_doc = knowledge.get("company", {})
    if company_doc:
        answer = find_faq_answer(query, company_doc.get("faq", []))
        if answer:
            return answer

    return None


def summarize_job_from_knowledge(conversation: dict[str, Any], knowledge: dict[str, Any]) -> str | None:
    job_doc = find_job_doc(conversation.get("jobTitle", ""), knowledge.get("jobs", {}))
    if not job_doc:
        return None

    sections = job_doc.get("sections", {})
    for section_name in ("岗位职责", "招聘要求", "工作地点", "薪资范围"):
        content = (sections.get(section_name) or "").strip()
        snippet = first_meaningful_line(content)
        if snippet:
            return snippet
    return None


def suggest_follow_up_question(conversation: dict[str, Any], knowledge: dict[str, Any]) -> str | None:
    job_doc = find_job_doc(conversation.get("jobTitle", ""), knowledge.get("jobs", {}))
    latest_inbound = get_latest_inbound_message(conversation)
    if not job_doc:
        return default_follow_up_question(latest_inbound)

    job_text = normalize_text("\n".join(job_doc.get("sections", {}).values()))
    candidate_text = normalize_text(latest_inbound)

    for keywords, question in (
        (("分销", "客户", "社群", "团购", "koc", "渠道"), "方便了解一下，您之前主要做的是哪一类分销渠道或客户类型？"),
        (("天猫", "京东", "抖音", "小红书", "拼多多", "淘系"), "方便了解一下，您最近两年主要负责哪些平台，是偏天猫、京东还是抖音这类渠道？"),
        (("美妆", "个护", "面护", "彩妆", "快消"), "方便了解一下，您最近两年主要负责的是哪些类目，是否有美妆或个护相关经验？"),
        (("团队", "管理", "带领"), "方便了解一下，您之前是否带过团队，大概团队规模和分工是怎样的？"),
        (("数据", "推广", "投放", "gmv", "销售额", "增长"), "方便了解一下，您之前负责过的项目体量和结果大概怎样，比如 GMV、增长或投放效果？"),
        (("出差", "展会"), "这个岗位偶尔会涉及出差和展会，想确认一下您这边是否方便接受？"),
    ):
        if any(keyword in job_text for keyword in keywords) and not any(keyword in candidate_text for keyword in keywords):
            return question

    return default_follow_up_question(latest_inbound)


def find_job_doc(job_title: str, jobs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not job_title:
        return None
    normalized_target = normalize_text(job_title)
    for name, doc in jobs.items():
        normalized_name = normalize_text(name)
        if normalized_target == normalized_name:
            return doc
    return None


def get_latest_inbound_message(conversation: dict[str, Any]) -> str:
    messages = conversation.get("messages", [])
    for message in reversed(messages):
        if message.get("direction") == "inbound":
            return message.get("text", "")
    return ""


def find_faq_answer(query: str, faq_items: list[dict[str, str]]) -> str | None:
    for item in faq_items:
        question = normalize_text(item.get("question", ""))
        if question and (question in query or query in question):
            return item.get("answer", "")

    query_tokens = tokenize(query)
    best_score = 0
    best_answer: str | None = None
    for item in faq_items:
        question_tokens = tokenize(normalize_text(item.get("question", "")))
        if not question_tokens:
            continue
        overlap = len(query_tokens & question_tokens)
        if overlap > best_score and overlap >= 2:
            best_score = overlap
            best_answer = item.get("answer", "")
    return best_answer


def first_meaningful_line(content: str) -> str | None:
    lines = [line.strip(" -*0123456789、.；;") for line in content.splitlines()]
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        return None
    return lines[0]


def default_follow_up_question(latest_inbound: str) -> str:
    latest = normalize_text(latest_inbound)
    if any(keyword in latest for keyword in ("简历", "附件")):
        return "这边先看一下您发来的资料，后面如果方便我再和您确认一些项目细节。"
    return "方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def tokenize(text: str) -> set[str]:
    raw = re.split(r"[，。？！、；：,.!?/\\s]+", text)
    return {token for token in raw if token}
