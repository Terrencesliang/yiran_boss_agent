from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KNOWLEDGE_INTENTS = ("company", "job", "compensation", "department")
JOB_CATEGORY_DIRS = {
    "company": ("company", "companies"),
    "job": ("job", "jobs", "position"),
    "compensation": ("compensation", "salary", "pay"),
    "department": ("department", "departments", "org"),
}


def load_knowledge_base(base_dir: Path) -> dict[str, Any]:
    jobs_dir = base_dir / "jobs"
    legacy_company_dir = base_dir / "company"
    global_company_dir = base_dir / "global" / "company"
    jobs: dict[str, dict[str, Any]] = {}
    job_folders: dict[str, dict[str, Any]] = {}
    company = empty_doc()
    global_company = empty_doc()

    if jobs_dir.exists():
        for path in sorted(jobs_dir.iterdir()):
            if path.is_file() and path.suffix.lower() == ".txt":
                parsed = parse_knowledge_txt(path.read_text(encoding="utf-8"))
                job_name = parsed["sections"].get("岗位名称") or path.stem
                jobs[job_name] = parsed
                folder_doc = new_job_folder_doc(path.stem)
                folder_doc["job"] = parsed
                folder_doc["aliases"] = unique_strings([path.stem, job_name])
                job_folders[path.stem] = folder_doc
            elif path.is_dir():
                folder_doc = load_job_folder(path)
                job_folders[path.name] = folder_doc
                job_doc = folder_doc.get("job") or empty_doc()
                primary_name = first_non_empty(
                    str(job_doc.get("sections", {}).get("岗位名称") or ""),
                    path.name,
                )
                if job_doc.get("sections") or job_doc.get("faq"):
                    jobs[primary_name] = job_doc

    if legacy_company_dir.exists():
        company = load_knowledge_dir(legacy_company_dir)
    if global_company_dir.exists():
        global_company = load_knowledge_dir(global_company_dir)

    return {
        "jobs": jobs,
        "jobFolders": job_folders,
        "company": company,
        "global": {
            "company": global_company,
        },
    }


def empty_doc() -> dict[str, Any]:
    return {"sections": {}, "faq": []}


def new_job_folder_doc(folder_name: str) -> dict[str, Any]:
    doc = {intent: empty_doc() for intent in KNOWLEDGE_INTENTS}
    doc["folderName"] = folder_name
    doc["aliases"] = [folder_name]
    return doc


def load_job_folder(path: Path) -> dict[str, Any]:
    folder_doc = new_job_folder_doc(path.name)
    for intent, dir_names in JOB_CATEGORY_DIRS.items():
        merged = empty_doc()
        for dirname in dir_names:
            category_dir = path / dirname
            if category_dir.exists():
                merged = merge_docs(merged, load_knowledge_dir(category_dir))
        folder_doc[intent] = merged

    aliases = [path.name]
    for category in KNOWLEDGE_INTENTS:
        sections = folder_doc.get(category, {}).get("sections", {})
        aliases.append(str(sections.get("岗位名称") or ""))
        aliases.append(str(sections.get("职位名称") or ""))
    folder_doc["aliases"] = unique_strings(aliases)
    return folder_doc


def load_knowledge_dir(path: Path) -> dict[str, Any]:
    merged = empty_doc()
    for txt_file in sorted(path.glob("*.txt")):
        merged = merge_docs(merged, parse_knowledge_txt(txt_file.read_text(encoding="utf-8")))
    return merged


def merge_docs(*docs: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, str] = {}
    faq: list[dict[str, str]] = []
    for doc in docs:
        sections.update(doc.get("sections", {}))
        faq.extend(doc.get("faq", []))
    return {"sections": sections, "faq": faq}


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def first_non_empty(*values: str) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


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
    result = answer_question_from_knowledge_detailed(conversation, knowledge)
    return result.get("answer") or None


def answer_question_from_knowledge_detailed(conversation: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    messages = conversation.get("messages", [])
    if not messages:
        return knowledge_answer_payload(reason="no_messages")

    latest = messages[-1]
    if latest.get("direction") != "inbound":
        return knowledge_answer_payload(reason="latest_message_not_inbound")

    raw_query = latest.get("text", "")
    query = normalize_text(raw_query)
    if not query:
        return knowledge_answer_payload(reason="empty_query")
    if not is_question_like(raw_query):
        return knowledge_answer_payload(reason="not_question")

    intent_result = classify_knowledge_intent(conversation, raw_query)
    intents = [intent for intent in intent_result.get("intents", []) if intent in KNOWLEDGE_INTENTS][:2]
    if not intents:
        return knowledge_answer_payload(intents=intent_result.get("intents", []), reason="intent_not_matched")

    job_match = resolve_job_folder(conversation.get("jobTitle", ""), knowledge)
    matches: list[dict[str, str]] = []
    blocked_by_job = False
    for intent in intents:
        faq_items: list[dict[str, str]] = []
        if intent == "company":
            faq_items = company_faq_items(job_match.get("folder"), knowledge)
        elif job_match.get("status") == "matched":
            faq_items = job_match.get("folder", {}).get(intent, {}).get("faq", [])
        else:
            blocked_by_job = True
            continue
        matched = find_faq_match(query, faq_items)
        if matched:
            matches.append({"intent": intent, **matched})

    if not matches:
        reason = "job_not_matched" if blocked_by_job else "faq_not_matched"
        return knowledge_answer_payload(
            intents=intents,
            job_folder=job_match.get("folderName"),
            reason=reason,
        )

    answer = compose_knowledge_answer(matches)
    return knowledge_answer_payload(
        answer=answer,
        matches=matches,
        intents=intents,
        job_folder=job_match.get("folderName"),
        reason="matched",
    )


def knowledge_answer_payload(
    answer: str | None = None,
    matches: list[dict[str, str]] | None = None,
    intents: list[str] | None = None,
    job_folder: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "answer": answer,
        "matches": matches or [],
        "intents": intents or [],
        "jobFolder": job_folder,
        "reason": reason,
    }


def compose_knowledge_answer(matches: list[dict[str, str]]) -> str:
    answers = [match["answer"] for match in matches if match.get("answer")]
    if not answers:
        return ""
    if len(answers) == 1:
        return answers[0]
    return f"{answers[0]} 另外，{answers[1]}"


def classify_knowledge_intent(conversation: dict[str, Any], question: str) -> dict[str, Any]:
    text = normalize_text(question)
    scored: list[tuple[str, int]] = []
    keyword_groups = (
        ("compensation", ("薪资", "薪酬", "工资", "待遇", "底薪", "提成", "奖金", "年终奖", "几薪", "社保", "五险", "公积金", "调薪")),
        ("department", ("部门", "团队", "架构", "汇报", "上级", "下属", "负责人", "组织", "几个人", "多少人", "管理层")),
        ("company", ("公司", "业务", "规模", "地址", "在哪里", "地点", "行业", "产品", "品牌", "氛围", "加班")),
        ("job", ("岗位", "职位", "招聘", "还招", "还在招", "职责", "要求", "工作内容", "上班时间", "双休", "出差", "品类", "平台", "简历", "投递")),
    )
    for intent, keywords in keyword_groups:
        score = sum(1 for keyword in keywords if normalize_text(keyword) in text)
        if intent == "job" and any(marker in text for marker in ("岗位", "职位")):
            score += 2
        if score:
            scored.append((intent, score))

    if not scored:
        return {"intents": ["unknown"], "confidence": 0.0, "reason": "keyword_not_matched"}

    if any(marker in text for marker in ("岗位", "职位")) and "公司" not in text:
        scored = [(intent, score) for intent, score in scored if intent != "company"]
    if any(intent in {"compensation", "department"} for intent, _score in scored):
        job_specific_terms = ("招聘", "还招", "还在招", "职责", "要求", "工作内容", "上班时间", "双休", "出差", "品类", "平台", "简历", "投递")
        if not any(term in text for term in job_specific_terms):
            scored = [(intent, score) for intent, score in scored if intent != "job"]
    scored.sort(key=lambda item: (-item[1], list(KNOWLEDGE_INTENTS).index(item[0])))
    intents = [intent for intent, _score in scored[:2]]
    return {"intents": intents, "confidence": min(1.0, scored[0][1] / 3), "reason": "keyword_rule"}


def is_question_like(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        marker in normalized
        for marker in (
            "?",
            "？",
            "吗",
            "嘛",
            "么",
            "什么",
            "多少",
            "哪里",
            "哪",
            "怎样",
            "怎么",
            "是否",
            "能否",
            "可以",
            "还在",
            "还招",
            "薪资",
            "工资",
            "待遇",
            "部门架构",
        )
    )


def summarize_job_from_knowledge(conversation: dict[str, Any], knowledge: dict[str, Any]) -> str | None:
    job_doc = find_job_doc(conversation.get("jobTitle", ""), knowledge)
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
    job_doc = find_job_doc(conversation.get("jobTitle", ""), knowledge)
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


def find_job_doc(job_title: str, jobs_or_knowledge: dict[str, Any]) -> dict[str, Any] | None:
    if not job_title:
        return None
    if "jobFolders" in jobs_or_knowledge:
        job_match = resolve_job_folder(job_title, jobs_or_knowledge)
        if job_match.get("status") == "matched":
            return job_match.get("folder", {}).get("job") or None
        jobs = jobs_or_knowledge.get("jobs", {})
    else:
        jobs = jobs_or_knowledge
    normalized_target = normalize_text(job_title)
    for name, doc in jobs.items():
        normalized_name = normalize_text(name)
        if normalized_target == normalized_name:
            return doc
    return None


def resolve_job_folder(job_title: str, knowledge: dict[str, Any]) -> dict[str, Any]:
    if not job_title:
        return {"status": "not_found", "folder": None, "folderName": None}

    target = normalize_text(job_title)
    folders = knowledge.get("jobFolders", {})
    folder_name_matches = [
        (folder_name, folder)
        for folder_name, folder in folders.items()
        if normalize_text(folder_name) == target
    ]
    if len(folder_name_matches) == 1:
        folder_name, folder = folder_name_matches[0]
        return {"status": "matched", "folder": folder, "folderName": folder_name}
    if len(folder_name_matches) > 1:
        return {"status": "ambiguous", "folder": None, "folderName": None}

    exact_matches = []
    fuzzy_matches = []
    for folder_name, folder in folders.items():
        aliases = folder.get("aliases") or [folder_name]
        normalized_aliases = [normalize_text(alias) for alias in aliases if normalize_text(alias)]
        if target in normalized_aliases:
            exact_matches.append((folder_name, folder))
            continue
        if any(target in alias or alias in target for alias in normalized_aliases):
            fuzzy_matches.append((folder_name, folder))

    if len(exact_matches) == 1:
        folder_name, folder = exact_matches[0]
        return {"status": "matched", "folder": folder, "folderName": folder_name}
    if len(exact_matches) > 1:
        return {"status": "ambiguous", "folder": None, "folderName": None}
    if len(fuzzy_matches) == 1:
        folder_name, folder = fuzzy_matches[0]
        return {"status": "matched", "folder": folder, "folderName": folder_name}
    if len(fuzzy_matches) > 1:
        return {"status": "ambiguous", "folder": None, "folderName": None}
    return {"status": "not_found", "folder": None, "folderName": None}


def company_faq_items(job_folder: dict[str, Any] | None, knowledge: dict[str, Any]) -> list[dict[str, str]]:
    faq: list[dict[str, str]] = []
    if job_folder:
        faq.extend(job_folder.get("company", {}).get("faq", []))
    faq.extend(knowledge.get("global", {}).get("company", {}).get("faq", []))
    faq.extend(knowledge.get("company", {}).get("faq", []))
    return faq


def get_latest_inbound_message(conversation: dict[str, Any]) -> str:
    messages = conversation.get("messages", [])
    for message in reversed(messages):
        if message.get("direction") == "inbound":
            return message.get("text", "")
    return ""


def find_faq_answer(query: str, faq_items: list[dict[str, str]]) -> str | None:
    match = find_faq_match(query, faq_items)
    return match.get("answer") if match else None


def find_faq_match(query: str, faq_items: list[dict[str, str]]) -> dict[str, str] | None:
    for item in faq_items:
        question = normalize_text(item.get("question", ""))
        if question and (question in query or query in question):
            return {"question": item.get("question", ""), "answer": item.get("answer", "")}

    query_tokens = tokenize(query)
    best_score = 0
    best_item: dict[str, str] | None = None
    for item in faq_items:
        question_tokens = tokenize(normalize_text(item.get("question", "")))
        if not question_tokens:
            continue
        overlap = len(query_tokens & question_tokens)
        if overlap > best_score and overlap >= 2:
            best_score = overlap
            best_item = item
    if best_item:
        return {"question": best_item.get("question", ""), "answer": best_item.get("answer", "")}
    return None


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
    tokens = {token for token in raw if token}
    for token in list(tokens):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.update(token[index:index + 2] for index in range(max(0, len(token) - 1)))
    return tokens
