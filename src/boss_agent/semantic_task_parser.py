"""Ground optional model proposals in independently checked recruitment text."""

from dataclasses import asdict, dataclass, replace
import json
from math import isfinite
import os
import re
import unicodedata
from urllib.error import URLError

from boss_agent.job_profile import load_condition
from boss_agent.major_catalog import find_major, known_major_group
from boss_agent.recommend_preview import load_preview_task
from boss_agent.resume_agent import _has_llm_config, _request_json_completion
from boss_agent.task_parser import ATTACHED_SENSITIVE_CLAUSE, DEGREES, NUMBER, _logical_parts, _number, parse_recruitment_task
from boss_agent.task_schema import Condition, ConditionGroup, Criterion, RecruitmentTask, iter_criteria
from boss_agent.task_validator import SENSITIVE, is_sensitive


@dataclass(frozen=True)
class Source:
    start: int
    end: int
    source_text: str


@dataclass
class ParsedCondition:
    category: str
    condition: Condition
    sources: list[Source]
    origin: str = "rule"
    confidence: float | None = None


@dataclass
class ParseResult:
    task: RecruitmentTask
    legacy_task: RecruitmentTask
    metadata: dict


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _signature(condition: Condition) -> tuple:
    if isinstance(condition, ConditionGroup):
        children = [_signature(child) for child in condition.conditions]
        return condition.operator, tuple(sorted(children, key=repr))
    value = condition.value
    if isinstance(value, list):
        value = tuple(sorted(value, key=str) if condition.operator in {"one_of", "has_any"} else value)
    return condition.field, condition.operator, value


def _reanchor(condition: Condition, source: str) -> Condition:
    if isinstance(condition, ConditionGroup):
        return replace(condition, conditions=[_reanchor(child, source) for child in condition.conditions])
    return replace(condition, source=source)


def _spans(raw: str) -> list[Source]:
    # NFKC is not necessarily length preserving. Split the unchanged input.
    parts = _logical_parts(raw.translate(str.maketrans("（）", "()")), rf"[,，;；。!！\n]|(?<=[)])、|{ATTACHED_SENSITIVE_CLAUSE}")
    if not parts:
        return [Source(0, len(raw), raw)]
    offset = 0
    spans = []
    translated = raw.translate(str.maketrans("（）", "()"))
    for part in parts:
        if part:
            start = translated.index(part, offset)
            end = start + len(part)
            spans.append(Source(start, end, raw[start:end]))
            offset = end
    return spans


HR = r"(?:相关\s*)?(?:人力资源|人力|人事|HR)\s*(?:相关\s*)?(?:工作\s*)?(?:经验|经历)"
TOOLS = {"workbuddy": "WorkBuddy", "agent": "Agent", "ai agent": "Agent"}
TOOL_NAME = r"[A-Za-z][A-Za-z0-9_.+#-]*(?:\s+[A-Za-z][A-Za-z0-9_.+#-]*)?"


def _tool_name(value: str) -> str | None:
    value = " ".join(value.split())
    return TOOLS.get(value.casefold(), value) if re.fullmatch(TOOL_NAME, value) else None


def _verified_clause(source: Source, major_context: bool) -> list[tuple[str, Condition]] | None:
    text = _normalize(source.source_text)
    hard, soft = "hard", "preference"

    def leaf(field, operator, value):
        return Criterion(field, operator, value, source.source_text)

    if re.fullmatch(r"(?:但\s*)?(?:非必须|不是必须)|(?:不要求|无需)\s*(?:有\s*)?(?:" + HR +
                    r"|(?:AI\s*)?Agent\s*(?:使用|实操|实践)?\s*(?:经验|经历))", text, re.I):
        return []
    exclusion = re.fullmatch(r"(?:不接受|排除)\s*(中专|大专|本科|硕士|博士)", text)
    if exclusion:
        return [(hard, ConditionGroup("NOT", [leaf("education_level", "equals", DEGREES[exclusion[1]])]))]

    degree = re.fullmatch(
        r"(?:招聘\s*)?(?:([\u3400-\u9fff]+?类)(?:专业)?\s*)?"
        r"(全日制|非全日制)?\s*(中专|大专|本科|硕士|博士)(生|及以上|以上)?"
        r"(?:\s*\(\s*最好是\s*([^()]+)\))?", text,
    )
    if degree:
        group, mode, level, bound, note = degree.groups()
        if group:
            group = group.removesuffix("大类") + "类" if group.endswith("大类") else group
            if not known_major_group(group):
                return None
        operator = "gte" if bound or (level == "本科" and (group or text.startswith("招聘"))) else "equals"
        results = [(hard, leaf("education_level", operator, DEGREES[level]))]
        if group:
            results.append((hard, leaf("major_group", "equals", group)))
        if mode:
            results.append((hard, leaf("education_mode", "equals", "full_time" if mode == "全日制" else "part_time")))
        if note:
            preferred = _preferred_majors(note)
            if not group or not preferred:
                return None
            results.append((soft, leaf("major", "one_of" if len(preferred) > 1 else "equals",
                                       preferred if len(preferred) > 1 else preferred[0])))
        return results
    preference = re.fullmatch(r"(?:最好是\s*(.+)|(.+?)\s*(?:优先(?:考虑)?|加分)(?:\s*\(非必须\))?)", text)
    if preference:
        body = next(value for value in preference.groups() if value is not None)
        preferred = _preferred_majors(body) if major_context or body.endswith("专业") else []
        if preferred:
            return [(soft, leaf("major", "one_of" if len(preferred) > 1 else "equals",
                                preferred if len(preferred) > 1 else preferred[0]))]
        if body == "有招聘经历":
            return [(soft, leaf("job_related_preference", "verify", body))]

    duration = re.fullmatch(
        rf"(?P<positive>(?:必须\s*)?有\s*)?{HR}\s*"
        rf"(?P<bound>且?\s*(?:不超过|不能超过|最多|至多|至少))?\s*(?P<n>{NUMBER})"
        rf"\s*(?P<unit>年|个?月)\s*(?P<suffix>以内|以下|以上)?", text, re.I,
    )
    leading = re.fullmatch(rf"(?:必须\s*)?有\s*(?P<n>{NUMBER})\s*(?P<unit>年|个?月)\s*以上\s*{HR}", text, re.I)
    if leading:
        months = _number(leading["n"]) * (12 if leading["unit"] == "年" else 1)
        return [(hard, leaf("hr_experience_months", "gte", months))]
    if re.fullmatch(rf"(?:必须\s*)?有\s*{HR}", text, re.I):
        return [(hard, leaf("hr_experience_months", "gt", 0))]
    if duration:
        bound = (duration["bound"] or "").strip().removeprefix("且").strip()
        suffix = duration["suffix"]
        months = _number(duration["n"]) * (12 if duration["unit"] == "年" else 1)
        if bound and suffix or not (bound or suffix):
            return None
        if bound == "至少" or suffix == "以上":
            results = [(hard, leaf("hr_experience_months", "gte", months))]
            if duration["positive"] and months == 0:
                results.insert(0, (hard, leaf("hr_experience_months", "gt", 0)))
            return results
        if duration["positive"]:
            return [(hard, leaf("hr_experience_months", "gt", 0)),
                    (hard, leaf("hr_experience_months", "lte", months))]
        return [(hard, leaf("hr_experience_months", "between", [0, months]))]

    tools = re.fullmatch(
        r"(?:有\s*)?(.+?)\s*(?:的\s*)?(?:实操|实践|实际使用)(?:经验|经历)(?:者)?\s*(?:\(\s*)?(?:优先|加分)\s*\)?",
        text, re.I,
    )
    if tools:
        names = [_tool_name(part) for part in re.split(r"或者|或|、|,", tools[1])]
        if all(names):
            separate = [leaf("hands_on_tools", "has_any", [name]) for name in dict.fromkeys(names)]
            return [(soft, ConditionGroup("OR", separate) if len(separate) > 1 else separate[0])]
    mandatory_tool = re.fullmatch(r"必须\s*有\s*(.+?)\s*(?:实操|实践|实际使用)(?:经验|经历)", text, re.I)
    if mandatory_tool and (name := _tool_name(mandatory_tool[1])):
        return [(hard, leaf("hands_on_tools", "has_any", [name]))]
    agent_delivery = re.fullmatch(r"必须\s*有\s*AI\s*Agent\s*项目交付经历", text, re.I)
    if agent_delivery:
        return [(hard, leaf("job_related_requirement", "verify", "有 AI Agent 项目交付经历"))]
    required_tools = re.fullmatch(r"(.+?)\s*(?:和|及|与)\s*(.+?)\s*(?:都|均)\s*必须\s*熟练", text, re.I)
    if required_tools:
        names = [_tool_name(part) for part in required_tools.groups()]
        if all(names):
            return [(hard, ConditionGroup("AND", [
                leaf("job_related_requirement", "verify", f"熟练使用 {name}") for name in names
            ]))]
    return None


def _preferred_majors(text: str) -> list[str]:
    names = [find_major(part.strip().removesuffix("专业")) for part in re.split(r"、|,|和|或者|或", text)]
    return list(dict.fromkeys(item.name for item in names)) if all(names) else []


def _merge(items: list[ParsedCondition]) -> list[ParsedCondition]:
    unique = {}
    for item in items:
        key = item.category, _signature(item.condition)
        previous = unique.setdefault(key, item)
        if previous is not item:
            previous.sources = list(dict.fromkeys(previous.sources + item.sources))
    return list(unique.values())


def _legacy_sources(condition: Condition, raw: str, spans: list[Source]) -> list[Source]:
    excerpts = {leaf.source for leaf in iter_criteria(condition)}
    sources = []
    for excerpt in excerpts:
        verified = [span for span in spans if _normalize(span.source_text) == _normalize(excerpt)]
        if not verified:
            verified = [Source(match.start(), match.end(), match[0])
                        for match in re.finditer(re.escape(excerpt), raw)]
        if not verified and "实操" in excerpt:
            for left, right in zip(spans, spans[1:]):
                combined = raw[left.start:right.end]
                if re.sub(r"\s*,\s*", "、", _normalize(combined)) == _normalize(excerpt):
                    verified.append(Source(left.start, right.end, combined))
        sources.extend(verified)
    return sorted(set(sources), key=lambda item: item.start)


def _rule_reference(raw: str, spans: list[Source]):
    """Compile recognized spans and preserve ambiguous or unsupported clauses."""
    major_context = bool(re.search(r"类(?:专业|\s*(?:本科|大专|硕士|博士))|专业\s*[:：]", raw))
    entries, unresolved = [], []
    index = 0
    while index < len(spans):
        span = spans[index]
        if major_context and _normalize(span.source_text).startswith("最好是"):
            while index+1 < len(spans):
                following = spans[index+1]
                between = raw[span.end:following.start]
                if not re.fullmatch(r"\s*[,，]\s*", between) or not find_major(
                    _normalize(following.source_text).removesuffix("专业")
                ):
                    break
                candidate = Source(span.start, following.end, raw[span.start:following.end])
                if not _verified_clause(candidate, major_context):
                    break
                span = candidate
                index += 1
        if re.fullmatch(rf"(?:有\s*)?{TOOL_NAME}", _normalize(span.source_text)) and index + 1 < len(spans):
            end = spans[index + 1].end
            combined = Source(span.start, end, raw[span.start:end])
            if _verified_clause(combined, major_context):
                span = combined
                index += 1
        index += 1
        checked = _verified_clause(span, major_context)
        if checked is None:
            local = parse_recruitment_task(span.source_text)
            if SENSITIVE.search(span.source_text):
                continue
            if (local.job is not None or local.max_checks is not None or local.action == "greet"
                    or re.fullmatch(r"(?:请\s*)?(?:不打招呼|不要打招呼|只预览|仅生成计划)", _normalize(span.source_text))):
                # The original parser owns job and execution directives.
                if not local.ambiguous_fields and not local.hard_filters and not local.soft_preferences:
                    continue
            if local.ambiguous_fields or not (local.hard_filters or local.soft_preferences):
                unresolved.append(span)
                continue
            if (re.search(r"不要求|无需|非必须", span.source_text)
                    or re.search(r"必须|硬性", span.source_text) and local.soft_preferences):
                unresolved.append(span)
                continue
            # A generic experience fallback can hide an unparsed duration, negation or qualifier.
            if any(leaf.field == "job_related_requirement" and re.search(r"经验|经历|不得|不要求|无需", leaf.source)
                   for condition in local.hard_filters for leaf in iter_criteria(condition)):
                unresolved.append(span)
                continue
            checked = [(category, _reanchor(condition, span.source_text))
                       for category, conditions in (("hard", local.hard_filters), ("preference", local.soft_preferences))
                       for condition in conditions]
        entries.extend(ParsedCondition(category, condition, [span]) for category, condition in checked)
    return _merge(entries), unresolved


def _supplement_candidates(raw: str, unresolved: list[Source]) -> dict[Source, list[ParsedCondition]]:
    """Allow an AI-proposed partition of an unparsed explicit conjunction."""
    result = {}
    for span in unresolved:
        parts = _logical_parts(span.source_text, r"并且|而且|同时要求")
        if len(parts) < 2 or not all(parts):
            continue
        offset = span.start
        entries = []
        for part in parts:
            start = raw.index(part, offset, span.end)
            offset = start + len(part)
            source = Source(start, offset, part)
            checked = _verified_clause(source, False)
            if not checked or any(category != "hard" for category, _ in checked):
                break
            entries.extend(ParsedCondition(category, condition, [source]) for category, condition in checked)
        else:
            result[span] = _merge(entries)
    return result


class ProposalError(ValueError):
    pass


SYSTEM = """你是招聘需求结构化解析器。只输出 JSON 对象 {"conditions": [...]}。
每项包含 id（唯一字符串）、category（hard 或 preference）、condition、sources、confidence（0 到 1 或 null）。
condition 为 {"field":字段,"operator":运算符,"value":值} 或 {"operator":"AND/OR/NOT","conditions":[子条件]}。
sources 是原文引用列表，每项必须包含 start、end、source_text，使用 Python 字符索引 [start,end)。
优先用已提供的原文区间。每条条件必须有原文支持，保留所有限制、冲突和条件关系。
不用输出岗位、检查数量、动作或未确定的条件。优先项不可改成硬性条件；不要求和无需不构成排除。
候选规则供核对，按原文理解；回复中不要包含额外字段或 Markdown。"""


def _wire(condition: Condition) -> dict:
    if isinstance(condition, ConditionGroup):
        return {"operator": condition.operator, "conditions": [_wire(child) for child in condition.conditions]}
    return {"field": condition.field, "operator": condition.operator, "value": condition.value}


def _attach_source(node, excerpt, depth=0):
    if depth > 16 or type(node) is not dict:
        raise ProposalError("invalid_schema")
    if set(node) == {"operator", "conditions"} and isinstance(node["conditions"], list):
        return {**node, "conditions": [_attach_source(child, excerpt, depth + 1) for child in node["conditions"]]}
    if set(node) != {"field", "operator", "value"}:
        raise ProposalError("invalid_schema")
    return {**node, "source": excerpt}


def _model_entries(response: str, raw: str, reference: list[ParsedCondition],
                   supplements: dict[Source, list[ParsedCondition]]) -> tuple[list[ParsedCondition], set[Source]]:
    if isinstance(response, str) and not response.strip():
        raise ProposalError("empty_output")
    try:
        document = json.loads(response)
    except (ValueError, TypeError, RecursionError) as error:
        raise ProposalError("invalid_json") from error
    if type(document) is not dict or set(document) != {"conditions"} or type(document["conditions"]) is not list:
        raise ProposalError("invalid_schema")
    def keys_for(entries):
        return {(item.category, _signature(item.condition)) for item in entries}

    baseline_sources = {(item.category, _signature(item.condition)): set(item.sources) for item in reference}
    allowed = {(item.category, _signature(item.condition)): item for item in _merge([
        replace(item) for item in reference + [entry for group in supplements.values() for entry in group]
    ])}
    result, seen, identifiers, cited = [], set(), set(), set()
    for proposal in document["conditions"]:
        if (type(proposal) is not dict or set(proposal) != {"id", "category", "condition", "sources", "confidence"}
                or type(proposal["id"]) is not str or not proposal["id"].strip() or proposal["id"] in identifiers
                or proposal["category"] not in ("hard", "preference")
                or type(proposal["sources"]) is not list or not proposal["sources"]):
            raise ProposalError("invalid_schema")
        identifiers.add(proposal["id"])
        confidence = proposal["confidence"]
        if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
            raise ProposalError("invalid_schema")
        citations = []
        for item in proposal["sources"]:
            if type(item) is not dict or set(item) != {"start", "end", "source_text"}:
                raise ProposalError("invalid_schema")
            start, end, excerpt = item["start"], item["end"], item["source_text"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw)
                    or type(excerpt) is not str or raw[start:end] != excerpt):
                raise ProposalError("invalid_source")
            citations.append(Source(start, end, excerpt))
        try:
            condition = load_condition(_attach_source(proposal["condition"], citations[0].source_text))
        except ValueError as error:
            raise ProposalError("invalid_schema") from error
        category = proposal["category"]
        key = category, _signature(condition)
        keys = [key]
        if key not in allowed and category == "hard" and isinstance(condition, ConditionGroup) and condition.operator == "AND":
            # Explicit conjunction of mandatory facts supported by the same quoted clause.
            keys = [(category, _signature(child)) for child in condition.conditions]
        backed = [allowed.get(key) for key in keys]
        if (any(item is None or not set(citations) <= set(item.sources) for item in backed)
                or any(is_sensitive(leaf) for leaf in iter_criteria(condition))):
            raise ProposalError("ungrounded_condition")
        if seen.intersection(keys) or len(set(keys)) != len(keys):
            raise ProposalError("invalid_schema")
        seen.update(keys)
        cited.update((key, source) for key in keys for source in citations)
        verified_sources = set.intersection(*(baseline_sources.get(key, set()) for key in keys)) | set(citations)
        result.append(ParsedCondition(category, condition, sorted(verified_sources, key=lambda span: span.start),
                                      "ai", confidence))
    if not keys_for(reference) <= seen:
        raise ProposalError("incomplete_output")
    resolved = set()
    for span, group in supplements.items():
        expected = {((entry.category, _signature(entry.condition)), source)
                    for entry in group for source in entry.sources}
        if expected <= cited:
            resolved.add(span)
        elif expected & cited:
            raise ProposalError("incomplete_output")
    return _merge(result), resolved


def _request_model(raw: str, reference: list[ParsedCondition], spans: list[Source], client, *, select=False) -> str:
    all_spans = list(dict.fromkeys(spans + [source for item in reference for source in item.sources]))
    prompt = json.dumps({"raw_text": raw, "source_spans": [asdict(span) for span in all_spans],
                         "rule_candidates": [{"category": item.category, "condition": _wire(item.condition),
                                              "sources": [asdict(source) for source in item.sources]}
                                             for item in reference]}, ensure_ascii=False)
    model = os.environ.get("BOSS_TASK_AI_MODEL") or os.environ.get("BOSS_AGENT_LLM_MODEL")
    if not model:
        raise ProposalError("missing_model")
    try:
        timeout = float(os.environ.get("BOSS_TASK_AI_TIMEOUT_SECONDS", "5"))
    except ValueError as error:
        raise ProposalError("invalid_timeout") from error
    if not isfinite(timeout) or not 0 < timeout <= 30:
        raise ProposalError("invalid_timeout")
    if select:
        prompt = json.dumps({"raw_text": raw, "choices": [
            {"id": index, "category": item.category, "condition": _wire(item.condition),
             "evidence": [source.source_text for source in item.sources]}
            for index, item in enumerate(reference)
        ]}, ensure_ascii=False)
        return client(
            '根据原文逐项核对候选条件。收录所有有原文支持且类别正确的选项。'
            '保留硬性条件与优先项的区别。忽略性别条件。'
            '只返回 JSON 对象 {"accepted_ids":[整数ID,...]}，无需重写条件或原文。',
            prompt, model, timeout=timeout, disable_deepseek_thinking=True,
        )
    return client(SYSTEM, prompt, model, timeout=timeout)


def _confirmed_entries(response, reference):
    try:
        document = json.loads(response)
    except (ValueError, TypeError, RecursionError) as error:
        raise ProposalError("invalid_json") from error
    if type(document) is not dict or set(document) != {"accepted_ids"}:
        raise ProposalError("invalid_schema")
    ids = document["accepted_ids"]
    if type(ids) is not list or any(type(index) is not int or not 0 <= index < len(reference) for index in ids):
        raise ProposalError("invalid_schema")
    if len(set(ids)) != len(ids):
        raise ProposalError("invalid_schema")
    if len(ids) != len(reference) or not ids:
        raise ProposalError("incomplete_output")
    return [replace(reference[index], origin="ai") for index in sorted(ids)]


def _metadata(mode, reason, entries, unresolved):
    sources = []
    for index, entry in enumerate(entries):
        for child_index, leaf in enumerate(iter_criteria(entry.condition)):
            sources.append({"id": f"criterion-{index + 1}.{child_index + 1}", "field": leaf.field,
                            "operator": leaf.operator, "value": leaf.value, "category": entry.category,
                            "sources": [asdict(source) for source in entry.sources], "origin": entry.origin,
                            "confidence": entry.confidence, "needs_clarification": not entry.sources,
                            "explanation": "原文与条件已校验" if entry.sources else "旧规则未提供可定位的原文片段"})
    return {"mode": mode, "fallback_reason": reason, "criteria_sources": sources,
            "warnings": ["AI 解析未通过校验，已使用本地规则"] if reason else [],
            "unresolved_requirements": unresolved, "offset_unit": "python_codepoint"}


def parse_task_with_metadata(raw: str, *, enabled: bool | None = None, client=None, select=False) -> ParseResult:
    legacy = parse_recruitment_task(raw)
    spans = _spans(raw)
    if enabled is None:
        enabled = os.environ.get("BOSS_TASK_AI_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        entries = [ParsedCondition(category, condition, _legacy_sources(condition, raw, spans))
                   for category, conditions in (("hard", legacy.hard_filters), ("preference", legacy.soft_preferences))
                   for condition in conditions]
        unresolved = [{"reason": item} for item in legacy.ambiguous_fields]
        return ParseResult(legacy, legacy, _metadata("rule", None, entries, unresolved))

    reference, unresolved = _rule_reference(raw, spans)
    supplements = _supplement_candidates(raw, unresolved)
    mode, reason, entries = "rule_fallback", None, reference
    try:
        if client is None and not _has_llm_config():
            raise ProposalError("missing_api_key")
        if client is None and not os.environ.get("BOSS_AGENT_LLM_BASE_URL", "").strip():
            raise ProposalError("missing_endpoint")
        possible_spans = [source for group in supplements.values() for item in group for source in item.sources]
        response = _request_model(raw, reference, spans + possible_spans, client or _request_json_completion,
                                  select=select)
        entries, resolved = ((_confirmed_entries(response, reference), set()) if select
                             else _model_entries(response, raw, reference, supplements))
        unresolved = [source for source in unresolved if source not in resolved]
        mode = "ai"
    except ProposalError as error:
        reason = str(error)
    except TimeoutError:
        reason = "timeout"
    except URLError as error:
        reason = "timeout" if isinstance(error.reason, TimeoutError) else "service_error"
    except Exception:
        reason = "service_error"

    blockers = [item for item in legacy.ambiguous_fields if not item.startswith("unresolved_condition:")]
    blockers.extend(f"unresolved_condition:{span.source_text}" for span in unresolved)
    task = replace(legacy, hard_filters=[item.condition for item in entries if item.category == "hard"],
                   soft_preferences=[item.condition for item in entries if item.category == "preference"],
                   ambiguous_fields=list(dict.fromkeys(blockers)))
    task = load_preview_task(task.to_dict())
    metadata = _metadata(mode, reason, entries, [
        {**asdict(span), "reason": "无法从原文确认并安全表达此条件"} for span in unresolved
    ])
    if select:
        metadata["model_strategy"] = "verified_choices"
    return ParseResult(task, legacy, metadata)


def execution_matches(result: ParseResult) -> bool:
    """Phase 2 keeps the original executor. Never show an executable divergent plan."""
    def signature(task):
        return [sorted(map(_signature, conditions), key=repr)
                for conditions in (task.hard_filters, task.soft_preferences)]

    return (signature(result.task) == signature(result.legacy_task)
            and result.task.ambiguous_fields == result.legacy_task.ambiguous_fields)
