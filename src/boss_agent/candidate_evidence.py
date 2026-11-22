"""Additive, source-relative observations for candidate preview reports."""

from datetime import date
from hashlib import sha256
import json
import re
from unicodedata import normalize

from boss_agent.major_catalog import CATALOG_VERSION, BusinessMajorAlias, BusinessMajorScope, describe_major
from boss_agent.education_pipeline import build_education
from boss_agent.resume_structure import DEGREE, DEGREE_NAME, EDUCATION_FIELDS, HEADINGS, report_major_observations, section_lines
from boss_agent.task_schema import iter_criteria
from boss_agent.work_timeline import HR_DUTY, HR_ROLE, NON_HR, OTHER_ROLE, ONGOING, PERIOD, report_day_windows


VERSION = "candidate-evidence-v1"


def stable_id(*parts) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


class EvidenceIndex:
    def __init__(self, sources: dict[str, str], batch_id: str, candidate_id: str, warnings: list[str]):
        self.sources = sources
        self.id = stable_id(batch_id, candidate_id)
        self.candidate_key = stable_id(candidate_id)
        self.refs = {}
        self.warnings = warnings

    def add(self, source, start, end, quote):
        text = self.sources.get(source, "")
        if not 0 <= start < end <= len(text) or text[start:end] != quote or not quote.strip():
            return None
        source_id = stable_id(self.id, source)
        evidence_id = stable_id(source_id, start, end)
        quality = "dom_text" if source == "dom" else "ocr_text"
        if any(warning.startswith(source) for warning in self.warnings):
            quality += "_partial"
        self.refs[evidence_id] = dict(source_type=source.upper(), source_id=source_id, start=start, end=end,
                                      quote=quote, quality=quality)
        return evidence_id

    def observation(self, item):
        return self.add(item.source, item.start, item.end, item.quote)


def extract_education(index: EvidenceIndex, observations, scopes: tuple[BusinessMajorScope, ...] = (),
                      aliases: tuple[BusinessMajorAlias, ...] = ()):
    facts = {}
    records = {}
    for item in observations:
        if item.field not in EDUCATION_FIELDS | {"school"}:
            continue
        citation = index.observation(item)
        if citation is None:
            continue
        record = item.record_id or item.source + ":unassigned"
        records.setdefault(record, dict(record_id=record, school=item.record_school, fact_ids=[]))
        field = item.field
        raw = item.value
        if field == "education_level":
            token = re.search(DEGREE, item.quote, re.I)
            if not token:
                continue
            raw = token[0]
            normalized = DEGREE_NAME[raw.casefold().removesuffix("'s")]
        else:
            if _normalized(raw) not in _normalized(item.quote):
                continue
            normalized = item.value
        major = describe_major(raw, scopes=scopes, aliases=aliases) if field in {"major", "major_group"} else None
        if major:
            normalized = major["normalized_major"] or major["major_category"]
        fact_id = stable_id(index.id, record, field, raw)
        fact = facts.setdefault(fact_id, dict(fact_id=fact_id, record_id=record, field=field,
                                             raw_value=raw, normalized_value=normalized,
                                             extraction_status="EXTRACTED", evidence_ids=[]))
        if citation not in fact["evidence_ids"]:
            fact["evidence_ids"].append(citation)
        if major:
            fact["normalization_status"] = major["status"]
            fact["major"] = major
        else:
            fact["normalization_status"] = "NORMALIZED"
        if item.conflict:
            fact["extraction_status"] = "CONFLICT"
        if fact_id not in records[record]["fact_ids"]:
            records[record]["fact_ids"].append(fact_id)
    for record in records.values():
        _reconcile_major_facts(record, facts)
        if not record["school"]:
            continue
        citations = [e for key in record["fact_ids"] for e in facts[key]["evidence_ids"]
                     if _normalized(record["school"]) in _normalized(index.refs[e]["quote"])]
        source, _, offset = record["record_id"].rpartition(":")
        if not citations and offset.isdecimal():
            start = int(offset)
            line = re.match(r"[^\r\n]+", index.sources.get(source, "")[start:])
            if line and record["school"] in line[0]:
                citations = [index.add(source, start, start + len(line[0]), line[0])]
        if citations:
            key = stable_id(index.id, record["record_id"], "school", record["school"])
            facts[key] = dict(fact_id=key, record_id=record["record_id"], field="school",
                              raw_value=record["school"], normalized_value=record["school"],
                              extraction_status="EXTRACTED", normalization_status="AS_STATED",
                              evidence_ids=list(dict.fromkeys(citations)))
            if key not in record["fact_ids"]:
                record["fact_ids"].append(key)
        else:
            record["school"] = None
    return _fuse_education(index, records, facts)


def _reconcile_major_facts(record, facts):
    major_ids = [key for key in record["fact_ids"] if facts[key]["field"] == "major"]
    if len(major_ids) < 2:
        return
    by_value = {}
    # A cited code is the primary normalization basis if it corroborates the name.
    major_ids.sort(key=lambda key: facts[key]["major"]["mapping_method"] != "exact_code")
    for key in major_ids:
        fact = facts[key]
        signature = fact["normalized_value"] or fact["raw_value"]
        if signature not in by_value:
            by_value[signature] = key
            continue
        retained = facts[by_value[signature]]
        retained["evidence_ids"] = list(dict.fromkeys(retained["evidence_ids"] + fact["evidence_ids"]))
        if retained["raw_value"] != fact["raw_value"]:
            retained.setdefault("raw_variants", [retained["raw_value"]]).append(fact["raw_value"])
        record["fact_ids"].remove(key)
        del facts[key]
    if len(by_value) > 1 and any(facts[key]["major"]["mapping_method"] == "exact_code" for key in by_value.values()):
        for key in by_value.values():
            facts[key]["extraction_status"] = "CONFLICT"


def _fuse_education(index, records, facts):
    identities = {}
    by_program = {}
    by_school_major = {}
    for record in records.values():
        fields = {}
        for key in record["fact_ids"]:
            fact = facts[key]
            fields.setdefault(fact["field"], set()).add(fact["normalized_value"])
        degrees = fields.get("education_level", set())
        years = fields.get("graduation_year", set())
        sources = {index.refs[ref]["source_type"] for key in record["fact_ids"] for ref in facts[key]["evidence_ids"]}
        majors = fields.get("major", set()) - {None}
        if record["school"] and len(majors) == len(sources) == 1:
            for date_field, dates in (("end", years), ("start", fields.get("education_start_year", set()))):
                if len(dates) == 1:
                    key = (_normalized(record["school"]), next(iter(majors)), date_field, next(iter(dates)))
                    by_school_major.setdefault(key, []).append((record, next(iter(sources))))
        if record["school"] and len(degrees) == len(years) == len(sources) == 1:
            identity = (_normalized(record["school"]), next(iter(degrees)), next(iter(years)))
            identities.setdefault(identity, []).append((record, next(iter(sources))))
            if len(majors) == 1:
                key = (next(iter(majors)), *identity[1:])
                by_program.setdefault(key, []).append((record, next(iter(sources))))
    for group in [*identities.values(), *by_school_major.values(), *by_program.values()]:
        if len(group) != 2 or group[0][1] == group[1][1]:
            continue
        target, other = group[0][0], group[1][0]
        if target["record_id"] not in records or other["record_id"] not in records:
            continue
        same_school = _normalized(target["school"]) == _normalized(other["school"])
        fact_keys = {(facts[key]["field"], facts[key]["normalized_value"] or facts[key]["raw_value"]): key
                     for key in target["fact_ids"]}
        for key in other["fact_ids"]:
            fact = facts[key]
            signature = (fact["field"], fact["normalized_value"] or fact["raw_value"])
            existing = fact_keys.get(signature)
            if existing is not None:
                retained = facts[existing]
                retained["evidence_ids"] = list(dict.fromkeys(retained["evidence_ids"] + fact["evidence_ids"]))
                if retained["raw_value"] != fact["raw_value"]:
                    retained["raw_variants"] = [retained["raw_value"], fact["raw_value"]]
                if fact["normalization_status"] == "MATCHED":
                    retained["extraction_status"] = "EXTRACTED"
                del facts[key]
            else:
                target["fact_ids"].append(key)
                fact["record_id"] = target["record_id"]
        for field in ("school", "major", "education_mode", "degree", "education_level", "education_start_year", "graduation_year"):
            alternatives = [facts[key] for key in target["fact_ids"] if facts[key]["field"] == field]
            if len({fact["normalized_value"] or fact["raw_value"] for fact in alternatives}) > 1:
                for fact in alternatives:
                    fact["extraction_status"] = "CONFLICT"
        if not same_school:
            target["school"] = None
        target["fusion"] = "aligned_school_education" if same_school else "same_program_degree_and_graduation"
        del records[other["record_id"]]
    return list(records.values()), list(facts.values())


DATE_TEXT = r"(?:19|20)\d{2}(?:[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?月?|年)?"
START_FIELD = re.compile(rf"(?:入职日期|开始日期|开始时间|入职)\s*[:：]\s*({DATE_TEXT})")
END_FIELD = re.compile(rf"(?:离职日期|结束日期|结束时间|离职)\s*[:：]\s*({DATE_TEXT}|至今|在职|现在|present)", re.I)
LOOSE_RANGE = re.compile(rf"(?P<start>{DATE_TEXT})(?:\s*(?:至|到|[—–~～])|\s+-)\s*(?P<end>{DATE_TEXT}|至今)|"
                         rf"(?P<ymd>(?:19|20)\d{{2}}[./-]\d{{1,2}}[./-]\d{{1,2}})\s*"
                         rf"(?:至|到|[-—–~～])\s*(?P<finish>{DATE_TEXT}|至今)")
COMPANY = re.compile(r"(?:公司|单位|雇主|company)\s*[:：]\s*([^\n，。；;|｜]+)", re.I)
POSITION = re.compile(r"(?:岗位|职位|曾担任|担任|role)\s*[:：]\s*([^\n，。；;|｜]+)", re.I)
BARE_COMPANY = re.compile(r"[\w（）()·]{2,45}(?:有限责任公司|股份有限公司|有限公司|公司|工作室|事务所)")
HR_POSITION = re.compile(r"招聘实习(?:生)?|人才招聘|人事行政|薪酬绩效|薪酬福利|绩效(?:专员|经理)|"
                         r"员工关系|培训(?:专员|助理|经理)|HR\s*助理", re.I)
HR_ACTION = re.compile(r"候选人邀约|招聘流程跟进|核算薪酬|薪资核算|绩效考核|员工培训|办理社保|员工入职|候选人筛选")
OTHER_POSITION = re.compile(r"销售|客服|开发|设计|运营|数据标注")
NEGATED_DUTY = re.compile(r"未(?:参与|负责|从事)|没有|不(?:负责|涉及|参与)")
BARE_POSITION = re.compile(r"人力资源(?:实习生|助理|专员|经理)|人事(?:行政|助理|实习生|专员)|"
                           r"招聘(?:实习生|实习|专员|经理|顾问)|HR\s*助理|HRBP|薪酬绩效(?:专员)?|"
                           r"员工关系(?:专员)?|培训(?:专员|经理)|行政(?:助理|专员|工作)|"
                           r"销售(?:专员|助理)|运营(?:助理|专员)|综合管理|客服(?:专员)?|"
                           r"开发工程师|软件工程师|设计师", re.I)
WORK_HEADINGS = {"实习经历": "internship", "兼职经历": "part_time", "正式工作经历": "full_time"}
WORK_SECTIONS = {"work", *WORK_HEADINGS.values()}


def _date_from_label(pattern, quote):
    match = pattern.search(quote)
    return match[1].lower() if match else None


def _experience(index, source, start, end, quote, as_of, raw_start=None, raw_end=None):
    citation = index.add(source, start, end, quote)
    if citation is None:
        return None
    company = COMPANY.search(quote) or BARE_COMPANY.search(quote)
    position = POSITION.search(quote) or BARE_POSITION.search(quote)
    company_name = (company[1] if company.lastindex else company[0]).strip() if company else None
    title = (position[1] if position.lastindex else position[0]).strip() if position else None
    # Labeled dates and a broader syntactic range preserve malformed endpoint evidence.
    period = PERIOD.search(quote)
    if period and not (raw_start or raw_end):
        raw_start, raw_end = period[1], (period[2] or period[4]).lower()
    loose = LOOSE_RANGE.search(quote)
    if loose:
        raw_start = loose["start"] or loose["ymd"]
        raw_end = (loose["end"] or loose["finish"]).lower()
    raw_start = _date_from_label(START_FIELD, quote) or raw_start
    raw_end = _date_from_label(END_FIELD, quote) or raw_end
    responsibilities = list(dict.fromkeys(match[1].strip() for match in
        re.finditer(r"(?:职责|工作内容|负责|duties)\s*[:：]\s*([^\n]+)", quote, re.I)))
    duty_text = "；".join(responsibilities) or "\n".join(quote.splitlines()[1:])
    duty_hits = [m[0] for matcher in (HR_DUTY, HR_ACTION) for part in re.split(r"[，。；;\n]", duty_text)
                 if not NEGATED_DUTY.search(part) and not NON_HR.search(part) for m in matcher.finditer(part)]
    if not duty_hits and not NEGATED_DUTY.search(quote) and not NON_HR.search(quote):
        duty_hits = [m[0] for matcher in (HR_DUTY, HR_ACTION) for m in matcher.finditer(quote)]
    responsibilities = responsibilities or list(dict.fromkeys(duty_hits))
    hr_title = bool(title and (HR_ROLE.search(title) or HR_POSITION.search(title)))
    other_title = bool(title and (OTHER_POSITION.search(title) or OTHER_ROLE.search(title)))
    if hr_title and other_title or duty_hits and other_title:
        relevance, reason = "POSSIBLE", "mixed_role_without_hr_time_allocation"
    elif duty_hits or (hr_title and not NON_HR.search(quote) and not NEGATED_DUTY.search(quote)):
        relevance, reason = "CONFIRMED", "hr_duties" if duty_hits else "explicit_hr_position"
    elif NON_HR.search(quote) or other_title:
        relevance, reason = "EXCLUDED", "non_hr_work_or_project"
    else:
        relevance, reason = "POSSIBLE", "duties_unclear"
    kind = ("internship" if "实习" in quote else "part_time" if "兼职" in quote else
            "full_time" if re.search(r"全职|正式员工|正式工作", quote) else "unknown")
    precision = lambda value: ("unknown" if not value else "as_of" if value in ONGOING else
                               {1: "year", 2: "month", 3: "day"}[len(re.findall(r"\d+", value))])
    return dict(experience_id=stable_id(index.id, "experience", source, start, end),
                company=company_name, position=title, start_date=raw_start, end_date=raw_end,
                experience_type=kind, hr_relevance=relevance, hr_reason=reason, responsibilities=responsibilities,
                date_precision={"start": precision(raw_start), "end": precision(raw_end)},
                date_windows=report_day_windows(raw_start, raw_end, as_of), evidence_ids=[citation],
                fusion="source_record", conflicts=[])


def extract_experiences(index, work_records, as_of, *, model_only=False):
    items = []
    covered = {}
    section_index = {source: list(section_lines(text, WORK_HEADINGS)) for source, text in index.sources.items()}
    for record in work_records:
        citation = record.evidence
        if index.observation(citation) is None:
            continue
        start, end = citation.start, citation.end
        lines = section_index.get(citation.source, [])
        for (previous_section, previous), (section, current) in zip(lines, lines[1:]):
            if (current.start() == start and previous_section == section and section in WORK_SECTIONS
                    and COMPANY.fullmatch(previous[0]) and not COMPANY.search(citation.quote)):
                start = previous.start()
                break
        quote = index.sources[citation.source][start:end]
        item = _experience(index, citation.source, start, end, quote, as_of,
                           record.start, record.end)
        if item:
            if citation.quality == "ai_verified_text":
                item.update(hr_relevance={True:"CONFIRMED", False:"EXCLUDED", None:"POSSIBLE"}[record.hr_work],
                            hr_reason="ai_source_recognition", start_date=record.start, end_date=record.end,
                            date_windows=report_day_windows(record.start, record.end, as_of))
            items.append(item)
            covered.setdefault(citation.source, []).append((citation.start, citation.end))
    for source, lines in (() if model_only else section_index.items()):
        text = index.sources[source]
        for offset, (section, line) in enumerate(lines):
            if section not in WORK_SECTIONS or any(first <= line.start() < last for first, last in covered.get(source, [])):
                continue
            if not (START_FIELD.search(line[0]) or END_FIELD.search(line[0]) or PERIOD.search(line[0]) or LOOSE_RANGE.search(line[0])
                    or POSITION.search(line[0]) or BARE_POSITION.search(line[0])):
                continue
            first, last = line.start(), line.end()
            for next_section, following in lines[offset + 1:offset + 4]:
                if next_section != section or any(a <= following.start() < b for a, b in covered.get(source, [])):
                    break
                if POSITION.search(following[0]) or PERIOD.search(following[0]) or LOOSE_RANGE.search(following[0]):
                    break
                if not (START_FIELD.search(following[0]) or END_FIELD.search(following[0])
                        or re.match(r"(?:职责|工作内容)\s*[:：]", following[0])):
                    break
                last = following.end()
            item = _experience(index, source, first, last, text[first:last], as_of)
            if item:
                items.append(item)
                covered.setdefault(source, []).append((first, last))
    for item in items:
        ref = index.refs[item["evidence_ids"][0]]
        source = ref["source_type"].lower()
        text = index.sources[source]
        previous = None
        for match in re.finditer(r"[^\r\n]+", text[:ref["start"]]):
            label = match[0].strip(" #：:").casefold()
            if label in WORK_HEADINGS or label in HEADINGS:
                previous = match
        if previous and (kind := WORK_HEADINGS.get(previous[0].strip(" #：:").casefold())):
            if item["experience_type"] == "unknown":
                item["experience_type"] = kind
                item["evidence_ids"].append(index.add(source, previous.start(), previous.end(), previous[0]))
    return items


def _normalized(text):
    return re.sub(r"\s+", "", normalize("NFKC", text)).casefold() if text else None


def _period_identity(item):
    # Retain precision. A calendar month is not identical to a specific day.
    return tuple(tuple(int(n) for n in re.findall(r"\d+", value)) or value
                 for value in (item["start_date"], item["end_date"])) if item["start_date"] and item["end_date"] else None


def _employment_key(item):
    return _normalized(item["company"]), _normalized(item["position"])


def _overlap(first, second):
    return bool(first and second and max(first[0], second[0]) < min(first[1], second[1]))


def fuse_experiences(index, items):
    """Merge only cross-source records with corroborated employment and period."""
    merged = []
    for item in items:
        key = _employment_key(item)
        period = _period_identity(item)
        source = index.refs[item["evidence_ids"][0]]["source_type"]
        matches = [other for other in merged if all(key) and _employment_key(other) == key and period
                   and _period_identity(other) == period and item["date_windows"]["status"] == "BOUNDED"
                   and all(index.refs[e]["source_type"] != source for e in other["evidence_ids"])]
        if len(matches) != 1:
            merged.append(item)
            continue
        other = matches[0]
        old_duties = {_normalized(duty) for duty in other["responsibilities"]}
        new_duties = {_normalized(duty) for duty in item["responsibilities"]}
        if old_duties and new_duties and not (old_duties <= new_duties or new_duties <= old_duties):
            if other["hr_relevance"] != item["hr_relevance"]:
                other["conflicts"].append("hr_relevance_conflict")
                item["conflicts"].append("hr_relevance_conflict")
            merged.append(item)
            continue
        other["evidence_ids"].extend(item["evidence_ids"])
        other["responsibilities"] = list(dict.fromkeys(other["responsibilities"] + item["responsibilities"]))
        if other["hr_relevance"] != item["hr_relevance"]:
            other["conflicts"].append("hr_relevance_conflict")
            other["hr_relevance"] = "POSSIBLE"
            other["hr_reason"] = "conflicting_sources"
        if other["experience_type"] != item["experience_type"]:
            if other["experience_type"] == "unknown":
                other["experience_type"] = item["experience_type"]
            elif item["experience_type"] != "unknown":
                other["experience_type"] = "unknown"
                other["conflicts"].append("experience_type_conflict")
        other["fusion"] = "same_company_position_and_period"
    for number, item in enumerate(merged):
        key = _employment_key(item)
        sources = {index.refs[e]["source_type"] for e in item["evidence_ids"]}
        for other in merged[number + 1:]:
            other_sources = {index.refs[e]["source_type"] for e in other["evidence_ids"]}
            if (all(key) and key == _employment_key(other) and sources.isdisjoint(other_sources)
                    and _period_identity(item) != _period_identity(other)
                    and _overlap(item["date_windows"]["possible"], other["date_windows"]["possible"])):
                for row in (item, other):
                    if "inconsistent_dates_or_unresolved_record_identity" not in row["conflicts"]:
                        row["conflicts"].append("inconsistent_dates_or_unresolved_record_identity")
    identities = {}
    for item in merged:
        key = _employment_key(item)
        period = _period_identity(item)
        if all(key) and period and item["date_windows"]["status"] == "BOUNDED":
            identities.setdefault((key, period), []).append(item)
    for identity, rows in identities.items():
        if len(rows) == 1:
            rows[0]["experience_id"] = stable_id(index.candidate_key, "employment", identity)
    return merged


def _union(windows):
    ranges = []
    overlap = False
    for start, end in sorted(window for window in windows if window and window[0] < window[1]):
        if ranges and start <= ranges[-1][1]:
            overlap |= start < ranges[-1][1]
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    days = sum((date.fromisoformat(end) - date.fromisoformat(start)).days for start, end in ranges)
    return days, ranges, overlap


def hr_totals(experiences, as_of, *, capture_complete):
    confirmed = [row for row in experiences if row["hr_relevance"] == "CONFIRMED" and not row["conflicts"]]
    relevant = [row for row in experiences if row["hr_relevance"] != "EXCLUDED" or row["conflicts"]]
    lower, guaranteed, overlap_min = _union([row["date_windows"]["guaranteed"] for row in confirmed])
    upper, possible, overlap_max = _union([row["date_windows"]["possible"] for row in relevant])
    unbounded = (not capture_complete or not relevant
                 or any(row["date_windows"]["status"] not in {"BOUNDED", "AFTER_REFERENCE_DATE"} for row in relevant))
    return dict(as_of=as_of.isoformat(), policy="calendar-half-open-observed-hr-v1",
                confirmed_hr_days=lower if any(row["date_windows"]["status"] == "BOUNDED" for row in confirmed) else None,
                possible_hr_days_min=lower if relevant else None,
                possible_hr_days_max=None if unbounded else upper,
                has_unknown_upper_bound=unbounded, confirmed_hr_experience=bool(
                    [row for row in confirmed if row["date_windows"]["status"] != "AFTER_REFERENCE_DATE"]
                ) or None,
                overlap_detected=overlap_min or overlap_max, guaranteed_union=guaranteed, possible_union=possible,
                included_experience_ids=[row["experience_id"] for row in confirmed if row["date_windows"]["guaranteed"]],
                uncertain_experience_ids=[row["experience_id"] for row in relevant
                                          if row not in confirmed or row["date_windows"]["status"] != "BOUNDED"
                                          or row["date_windows"]["guaranteed"] != row["date_windows"]["possible"]])


def _major_checks(task, majors):
    checks = []
    if task is None:
        return checks
    for category, rules in (("hard", task.hard_filters), ("preference", task.soft_preferences)):
        for condition in rules:
            for rule in iter_criteria(condition):
                if rule.field not in {"major", "major_group"} or rule.operator not in {"equals", "one_of"}:
                    continue
                expected = rule.value if isinstance(rule.value, list) else [rule.value]
                if not all(isinstance(value, str) for value in expected):
                    continue
                mappings = [describe_major(value) for value in expected]
                key = "major_category" if rule.field == "major_group" else "normalized_major"
                accepted = {item[key] for item in mappings}
                comparisons = []
                for fact in majors:
                    value = fact["major"][key]
                    status = "UNRESOLVED" if not value or None in accepted or fact["extraction_status"] == "CONFLICT" else (
                        "MATCH" if value in accepted else "OUTSIDE_SCOPE")
                    comparisons.append(dict(fact_id=fact["fact_id"], status=status,
                                            basis=fact["major"]["mapping_method"]))
                checks.append(dict(category=category, field=rule.field, expected=expected,
                                   basis="official_catalog_relation", observations=comparisons,
                                   missing_evidence=not comparisons))
    return checks


def evidence_report(candidate, batch_id, as_of, *, scopes=(), aliases=(), task=None, education_model=None):
    """Build an audit view after qualification; never feed it into the rule engine."""
    resume = candidate["structured_evidence"]
    warnings = candidate["capture_warnings"]
    index = EvidenceIndex(candidate["sources"], batch_id, candidate["candidate_id"], warnings)
    ai_status = candidate.get("resume_model_status")
    supplementary = [] if ai_status else report_major_observations(index.sources, resume.observations)
    records, facts = extract_education(index, [*resume.observations, *supplementary], scopes, aliases)
    education = build_education(index, records, facts, candidate["capture_status"], education_model)
    if ai_status:
        education["model_status"] = ai_status
        if ai_status == "failed":
            education["status"] = "parse_failed"
        for fact in facts:
            fact["method"] = "ai_source_recognition"
    experiences = fuse_experiences(index, extract_experiences(index, resume.work_records, as_of, model_only=bool(ai_status)))
    projects = []
    for item in resume.observations:
        if item.field == "project_record" and (citation := index.observation(item)):
            projects.append(dict(experience_type="project", evidence_ids=[citation]))
    majors = [fact for fact in facts if "major" in fact]
    field_issues = []
    if not majors:
        field_issues.append("major_evidence_missing")
    elif any(fact["normalization_status"] == "UNMAPPED" for fact in majors):
        field_issues.append("major_unmapped")
    if not experiences:
        field_issues.append("work_records_missing")
    if any(row["date_windows"]["status"] in {"MISSING_DATE", "INVALID_DATE", "REVERSED_DATES"} for row in experiences):
        field_issues.append("work_dates_missing_or_invalid")
    if any(row["hr_reason"] in {"duties_unclear", "mixed_role_without_hr_time_allocation"} for row in experiences):
        field_issues.append("work_duties_unverified")
    if any(row["conflicts"] for row in experiences) or any(fact["extraction_status"] == "CONFLICT" for fact in facts):
        field_issues.append("evidence_conflict")
    if warnings:
        field_issues.append("capture_incomplete")
    return dict(version=VERSION, capture_batch_id=batch_id, capture_id=index.id, identity_status="VERIFIED",
                capture_status=candidate["capture_status"], capture_warnings=warnings,
                catalog_version=CATALOG_VERSION, education_records=records, facts=facts,
                education_pipeline=education, resume_model_status=ai_status,
                major_scope_checks=_major_checks(task, majors),
                experiences=experiences, projects=projects,
                hr_timeline=hr_totals(experiences, as_of, capture_complete=candidate["capture_status"] == "complete"),
                issues=field_issues, evidence=index.refs,
                sources=[dict(source_id=stable_id(index.id, source), source_type=source.upper(), characters=len(text),
                              redacted_sha256=sha256(text.encode("utf-8")).hexdigest())
                         for source, text in index.sources.items()])
