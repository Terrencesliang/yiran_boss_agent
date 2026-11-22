"""Compile Chinese recruitment requests locally without browser or model calls."""

import re
import unicodedata

from boss_agent.major_catalog import find_major, known_major_group
from boss_agent.task_schema import LEGACY_EXPERIENCE_FIELDS, Condition, ConditionGroup, Criterion, RecruitmentTask
from boss_agent.task_validator import SENSITIVE, decode_model_clauses


NUMBER = r"[0-9]+|[零一二两三四五六七八九十百]+"
CHECK = rf"(?:最多|至多|上限)?\s*(?:检查|查看|遍历|审核|浏览|初筛)\s*(?P<n>{NUMBER})\s*(?:份(?:简历)?|位候选人)"
GREET = rf"(?:向|给|联系|打招呼|招呼|成功联系|发送|目标成功联系)\s*(?:(?P<cap>最多|至多|不超过)\s*)?(?:符合条件的?|匹配的?|合适的?)?\s*(?P<n>{NUMBER})\s*(?:位(?:符合条件的人|候选人|人)?|人)"
GREET_REVERSE = rf"(?:(?P<cap>最多|至多|不超过)\s*)?(?:向|给)?\s*(?P<n>{NUMBER})\s*(?:人|位)\s*(?:打招呼|发招呼|联系)"
OUTREACH_CLAUSE = re.compile(
    rf"(?:(?P<initial_cap>最多|至多|不超过)\s*)?(?P<verb>联系|邀请|给|向)\s*"
    rf"(?:(?P<cap>最多|至多|不超过)\s*)?(?:符合(?:条件|要求)(?:的)?\s*)?"
    rf"(?P<n>{NUMBER})\s*(?:人|位(?:候选人|人|符合条件的人)?)"
    rf"\s*(?P<ending>沟通|打招呼|发招呼|发送招呼)?"
)
PREFERENCE = re.compile(r"最好(?:是|有)?|优先(?:考虑)?|加分(?:项)?|希望有|偏好")
ATTACHED_SENSITIVE_CLAUSE = r"(?<=\))\s*(?=男生|女生|男性|女性)|(?<=男生|女生|男性|女性)\s*、"
DURATION = re.compile(
    rf"(?P<prefix>至少|不少于|至多|不超过)?\s*(?P<amount>{NUMBER})\s*(?P<unit>年|个?月)\s*(?P<bound>以上|及以上|以内|以下|内)?"
    r"\s*(?:的)?(?P<scope>[^,。]*?)(?:工作)?经验$"
)
DURATION_SUFFIX = re.compile(
    rf"(?P<scope>[^,。]*?)(?:工作)?经验\s*(?P<bound>不超过|至多|最多|至少|不少于)\s*"
    rf"(?P<amount>{NUMBER})\s*(?P<unit>年|个?月)"
)
COHORTS = re.compile(r"(?:\d{2}(?:\d{2})?\s*届(?:[、\s]|或)*)+(?:应届生|应届毕业生)?")
DEGREES = {"中专": "secondary", "大专": "associate", "本科": "bachelor", "硕士": "master", "博士": "doctorate"}
FIELDS = {
    "专业": "major", "专业大类": "major_group", "行业": "industry_experience",
    "工具": "tools", "技能": "skills", "证书": "certificates", "资格证书": "certificates",
    "项目成果": "project_outcomes", "项目经历": "project_experience", "能力": "custom_ability",
}
SEMANTIC_FIELDS = {"project_outcomes", "project_experience", "custom_ability"}
RECRUITMENT_PARSER_POLICY = "phase03-c2-20261122"


def _number(value: str) -> int:
    if value.isascii() and value.isdecimal():
        return int(value)
    digits = dict(zip("零一二三四五六七八九", range(10)))
    digits["两"] = 2
    total = current = 0
    for character in value:
        if character in "十百":
            total += (current or 1) * (10 if character == "十" else 100)
            current = 0
        else:
            current = digits[character]
    return total + current


def _extract_counts(text: str, patterns: tuple[str, ...]) -> set[int]:
    return {_number(match["n"]) for pattern in patterns for match in re.finditer(pattern, text)}


def parse_recruitment_task(text: str, *, model_output: str | None = None) -> RecruitmentTask:
    """Compile supported expressions and surface unresolved clauses for review.

    An optional external segmenter may propose a lossless split, with no authority
    to supply criteria, action, or approval. Local parsing remains authoritative.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("request_required")
    if model_output is not None:
        text = "".join(decode_model_clauses(model_output, text))
    text = unicodedata.normalize("NFKC", text).strip()
    hard, soft, ambiguous, unsupported = [], [], [], []
    job_pattern = r"(?:招聘岗位|岗位|职位)\s*[:：]\s*([^,;。\n]+)|在[「『“](.+?)[」』”](?:岗位|职位)"
    job_names = {
        next(value.strip() for value in match.groups() if value)
        for match in re.finditer(job_pattern, text)
    }
    if len(job_names) > 1:
        ambiguous.append("conflicting_job")
    job_name = next(iter(job_names)) if len(job_names) == 1 else None
    text = re.sub(job_pattern, ",", text)
    if job_name and SENSITIVE.search(job_name):
        unsupported.append(job_name)
        job_name = None

    text = re.sub(r"^(?:请\s*)?(?:在|从)?[「『“]?推荐牛人[」』”]?(?:页面|页|中|里)?\s*", "", text)
    text = re.sub(r"^筛选并(?=(?:向|给))", "", text)
    text = re.sub(r"(打招呼)\s*:\s*", r"\1,", text)
    text = re.sub(r"(?:并且|且|并)(?=\s*(?:优先|最好|偏好))", ",", text)
    text = re.sub(
        r"、(?=[^、,;。]*(?:大类(?:专业)?|全日制|(?:以内|以下).*经验))",
        ",", text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_.+-]*(?:\s*,\s*[A-Za-z][A-Za-z0-9_.+-]*)+)"
        r"(?=\s*(?:的\s*)?(?:实操|实践|实际使用)(?:经验|经历))",
        lambda match: re.sub(r"\s*,\s*", "、", match[1]),
        text,
    )
    clauses = _logical_parts(text, rf"[,;。!！\n]|{ATTACHED_SENSITIVE_CLAUSE}") or re.split(r"[,;。!！\n]", text)
    actionable = "，".join(clause for clause in clauses if not SENSITIVE.search(clause))
    outreach = {}
    for raw in clauses:
        clause = re.sub(r"^请\s*", "", raw.strip())
        match = OUTREACH_CLAUSE.fullmatch(clause)
        if match and (match["ending"] or match["verb"] == "联系"):
            outreach[clause] = match
    check_counts = _extract_counts(actionable, (CHECK,))
    greet_counts = _extract_counts(actionable, (GREET, GREET_REVERSE)) | {
        _number(match["n"]) for match in outreach.values()
    }
    contact_limit_only = any(match["initial_cap"] or match["cap"] for match in outreach.values()) or any(
        match["cap"] is not None for pattern in (GREET, GREET_REVERSE)
        for match in re.finditer(pattern, actionable)
    )
    if len(check_counts) > 1:
        ambiguous.append("conflicting_max_checks")
    if len(greet_counts) > 1:
        ambiguous.append("conflicting_target_success_count")

    wants_greet = bool(outreach or re.search(r"打招呼|发招呼|成功联系|(?:向|给).{0,20}联系|联系\s*(?:符合条件的?)?\s*\d", actionable))
    if re.search(r"(?:不要|无需|禁止|暂不|不需要|先不)(?:打招呼|联系|发送)", actionable):
        if greet_counts:
            ambiguous.append("conflicting_action")
        wants_greet = False

    has_major_context = any(re.search(r"大类|类专业|专业\s*:", clause) for clause in clauses)
    previous_preference = False
    for raw in clauses:
        clause = raw.strip(" ：:？? ")
        continues_preference = previous_preference and clause.startswith("以及")
        previous_preference = False
        if not clause:
            continue
        if SENSITIVE.search(clause):
            unsupported.append(clause)
            continue
        clause = re.sub(r"^(?:请\s*)?(?:在|从)?推荐牛人(?:页面|页|中|里)?\s*", "", clause)
        clause = re.sub(r"^(?:帮我|请)?\s*(?:筛选|筛查|找出|只看)", "", clause).strip()
        clause = re.sub(r"^请\s*", "", clause)
        if clause in outreach:
            continue
        if degree_phrase := re.fullmatch(r"(?:找|先预览|预览)?\s*((?:非全日制|全日制)?(?:本科|硕士|博士|大专|中专))(?:候选人|名单)", clause):
            clause = degree_phrase[1]
        if bare_degree := re.fullmatch(r"找\s*((?:非全日制|全日制)?(?:本科|硕士|博士|大专|中专))", clause):
            clause = bare_degree[1]
        if not clause:
            continue
        if _extract_counts(clause, (CHECK, GREET, GREET_REVERSE)):
            # Consume only execution phrases occupying the entire clause.
            if any(re.fullmatch(pattern + r"\s*(?:打招呼|发送招呼|为止)?", clause)
                   for pattern in (CHECK, GREET, GREET_REVERSE)):
                continue
        if re.fullmatch(r"(?:不要|无需|禁止|暂不|不需要|先不)(?:打招呼|联系|发送)|只预览|仅生成计划", clause):
            continue
        if re.fullmatch(r"(?:向符合条件的人)?打招呼|联系符合条件的人", clause):
            continue
        if continues_preference:
            clause = clause.removeprefix("以及").strip().removesuffix("的人")
        major_note = re.fullmatch(r"(.+(?:大类|类)(?:专业)?.*)\s*\(\s*最好是\s*([^()]+)\)", clause)
        if major_note:
            base_rules = _parse_leaf(major_note[1].strip(), clause, False)
            if any(rule.field == "major_group" for rule in base_rules):
                majors = [name.strip() for name in re.split(r"[,、]|或", major_note[2])]
                if all(majors):
                    hard.extend(base_rules)
                    soft.append(Criterion("major", "one_of" if len(majors) > 1 else "equals",
                                          majors if len(majors) > 1 else majors[0], clause))
                    previous_preference = True
                    continue
        preference_suffix = re.search(rf"\(\s*(?:{PREFERENCE.pattern})\s*\)$", clause)
        preference = continues_preference or bool(PREFERENCE.search(clause))
        previous_preference = preference
        destination = soft if preference else hard
        body = clause[:preference_suffix.start()] if preference_suffix else clause
        if preference:
            body = PREFERENCE.sub("", body).strip(" ，、")
        if convention := re.fullmatch(r"(?P<names>[^:]+(?:或|、)[^:]+)专业", body):
            names = re.split(r"或|、", convention["names"])
            if not preference and any(name.strip().endswith(("类", "大类")) for name in names):
                destination.append(ConditionGroup("OR", [
                    Criterion("major_group" if name.strip().endswith("类") else "major", "equals",
                              name.strip().replace("大类", "类"), clause)
                    for name in names
                ]))
                continue
            body = "专业:" + convention["names"]
        if has_major_context and clause.startswith("最好是"):
            names = [find_major(part.strip().removesuffix("专业")) for part in re.split(r"、|或", body)]
            if all(names):
                body = "专业:" + body
        continuation = find_major(clause.removesuffix("专业")) if has_major_context else None
        previous = soft[-1] if soft else None
        if (continuation and isinstance(previous, Criterion) and previous.field == "major"
                and previous.source.startswith("最好是")
                and (joined := re.search(re.escape(previous.source) + r"\s*,\s*" + re.escape(clause), text))):
            names = previous.value if isinstance(previous.value, list) else [previous.value]
            soft[-1] = Criterion("major", "one_of", list(dict.fromkeys([*names, continuation.name])), joined[0])
            previous_preference = True
            continue
        conditions = _parse_condition(body, clause, preference)
        if conditions:
            destination.extend(conditions)
        else:
            ambiguous.append(f"unresolved_condition:{clause}")

    return RecruitmentTask(
        job=job_name, hard_filters=hard, soft_preferences=soft,
        max_checks=next(iter(check_counts)) if len(check_counts) == 1 else None,
        target_success_count=next(iter(greet_counts)) if wants_greet and len(greet_counts) == 1 else None,
        action="greet" if wants_greet else "screen",
        ambiguous_fields=list(dict.fromkeys(ambiguous)), unsupported_conditions=unsupported,
        contact_limit_only=wants_greet and contact_limit_only,
    )



def _logical_parts(text: str, token: str) -> list[str]:
    """Split at top-level connectives while preserving quoted field values."""
    cuts = []
    depth = 0
    quote = None
    start = 0
    pattern = re.compile(token, re.IGNORECASE)
    for index, character in enumerate(text):
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {'"', "「", "“"}:
            quote = {'"': '"', "「": "」", "“": "”"}[character]
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return []
        elif depth == 0 and index >= start:
            match = pattern.match(text, index)
            if match:
                cuts.append(text[start:index].strip())
                start = match.end()
    if depth or quote:
        return []
    return [*cuts, text[start:].strip()]


def _parse_condition(text: str, source: str, preference: bool) -> list[Condition]:
    if COHORTS.fullmatch(text):
        return _parse_leaf(text, source, preference)
    if re.fullmatch(r"(?:有\s*)?[^:()]+\s*(?:实操|实践|实际使用)(?:经验|经历)", text):
        return _parse_leaf(text, source, preference)
    # Preserve explicit field labels across alternatives, e.g. 工具: Figma 或 Sketch.
    labeled = re.fullmatch(r"([^:]+):\s*(.+)", text)
    if labeled and labeled[1] in FIELDS and not re.search(r"\b(?:AND|OR|NOT)\b|并且|且|[:()]", labeled[2], re.I):
        field = FIELDS[labeled[1]]
        parts = [item.strip() for item in re.split(r"或|、", labeled[2])]
        if field in SEMANTIC_FIELDS:
            leaves = [Criterion(field, "verify", item, source) for item in parts if item]
            return leaves if len(leaves) < 2 else [ConditionGroup("OR" if "或" in labeled[2] else "AND", leaves)]
        return [Criterion(field, "one_of" if len(parts) > 1 else "equals",
                          parts if len(parts) > 1 else parts[0], source)] if all(parts) else []
    for operator, separator in (("OR", r"或者|或(?!以上)|\bOR\b"), ("AND", r"并且|且|\bAND\b")):
        parts = _logical_parts(text, separator)
        if not parts or not all(parts):
            return []
        if len(parts) > 1:
            children = [_parse_condition(part, source, preference) for part in parts]
            if not all(children):
                return []
            return [ConditionGroup(operator, [
                child[0] if len(child) == 1 else ConditionGroup("AND", child) for child in children
            ])]
    if text.startswith("(") and text.endswith(")"):
        return _parse_condition(text[1:-1].strip(), source, preference)
    negated = re.match(r"^(?:NOT\b|排除|不接受|不要)\s*(.+)$", text, re.I)
    if negated:
        child = _parse_condition(negated[1], source, preference)
        return [ConditionGroup("NOT", child if len(child) == 1 else [ConditionGroup("AND", child)])] if child else []
    if re.search(r"不限|不要求|不(?:是|要|限)", text):
        return []
    return _parse_leaf(text, source, preference)


def _parse_leaf(text: str, source: str, preference: bool) -> list[Condition]:
    field_value = re.fullmatch(r"([^:]+):\s*(.+)", text)
    if field_value and field_value[1] in FIELDS:
        field = FIELDS[field_value[1]]
        return [Criterion(field, "verify" if field in SEMANTIC_FIELDS else "equals", field_value[2], source)]
    duration_text = text.removeprefix("有").strip()
    duration = DURATION.fullmatch(duration_text) or DURATION_SUFFIX.fullmatch(duration_text)
    if duration:
        scope = duration["scope"].strip().removesuffix("相关")
        if scope == "人力":
            scope = "人力资源"
        field = LEGACY_EXPERIENCE_FIELDS.get(scope, f"experience_months:{scope}") if scope else "experience_months"
        months = _number(duration["amount"]) * (12 if duration["unit"] == "年" else 1)
        upper_bounds = {"以内", "以下", "内", "不超过", "至多", "最多"}
        prefix = duration.groupdict().get("prefix")
        if prefix and duration["bound"] and (prefix in upper_bounds) != (duration["bound"] in upper_bounds):
            return []
        operator = "lte" if (prefix or duration["bound"]) in upper_bounds else "gte"
        if operator == "lte":
            if text.startswith("有") and field == "hr_experience_months":
                return [Criterion(field, "gt", 0, source), Criterion(field, "lte", months, source)]
            return [Criterion(field, "between", [0, months], source)]
        return [Criterion(field, operator, months, source)]
    if COHORTS.fullmatch(text):
        years = sorted({int(year) + (2000 if len(year) == 2 else 0)
                        for year in re.findall(r"(?<!\d)(\d{4}|\d{2})\s*届", text)})
        rules = [Criterion("graduation_year", "one_of", years, source)]
        if "应届" in text:
            rules.append(Criterion("graduate_status", "equals", "fresh_graduate", source))
        return rules
    if text in {"应届生", "应届毕业生"}:
        return [Criterion("graduate_status", "equals", "fresh_graduate", source)]
    degree = re.fullmatch(
        r"(?:(.+?(?:大类|类))(?:专业)?)?(非全日制|全日制)?(中专|大专|本科|硕士|博士)?(?:学历)?(及以上|或以上|以上)?",
        text,
    )
    if degree and any(degree.groups()):
        group, mode, level, above = degree.groups()
        rules = []
        if mode:
            rules.append(Criterion("education_mode", "equals", "full_time" if mode == "全日制" else "part_time", source))
        if level:
            rules.append(Criterion("education_level", "gte" if above else "equals", DEGREES[level], source))
        if group:
            group = group.replace("大类", "类")
            if group.startswith("有") and known_major_group(group[1:]) and not known_major_group(group):
                group = group[1:]
            rules.append(Criterion("major_group", "equals", group, source))
        return rules
    major = re.fullmatch(r"(.+?)专业", text)
    if major:
        name = major[1].replace("大类", "类")
        return [Criterion("major_group" if name.endswith("类") else "major", "equals", name, source)]
    practice = re.fullmatch(r"(?:(?:必须\s*)?有\s*)?(.+?)\s*(?:的\s*)?(?:实操|实践|实际使用)(?:经验|经历)", text)
    if practice:
        tools = [part.strip() for part in re.split(r"或者|或|、", practice[1])]
        if all(tools):
            return [Criterion("hands_on_tools", "has_any", tools, source)]
    if re.fullmatch(r"(?:(?:必须\s*)?有\s*)?(?:AI\s*Agent|Agent|WorkBuddy)\s*(?:经验|经历)", text, re.I):
        return []
    certificate = re.fullmatch(r"(?:持有|取得|具有)\s*(.+?)(?:证书|资格证)?", text)
    if certificate and not re.search(r"经验|能力|经历", certificate[1]):
        return [Criterion("certificates", "equals", certificate[1], source)]
    if preference and re.fullmatch(r"[^、]+(?:、[^、]+)+", text):
        # A bare list has no reliable field. Preserve the intent as semantic.
        return [Criterion("job_related_preference", "verify", text, source)]
    if preference and text or re.search(r"经验|经历|熟悉|负责|熟练|交付|成果|能力|证书|资格", text):
        return [Criterion("job_related_preference" if preference else "job_related_requirement", "verify", text, source)]
    return []
