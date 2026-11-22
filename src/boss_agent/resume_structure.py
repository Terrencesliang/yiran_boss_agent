"""Conservative bilingual resume extraction with source-relative evidence."""

from dataclasses import asdict, dataclass, replace
import re

from boss_agent.major_catalog import find_major, known_major_group, normalize_major_name


HEADINGS = {
    "个人简介": "basic", "基本信息": "basic", "summary": "basic", "profile": "basic",
    "教育经历": "education", "教育背景": "education", "education": "education",
    "工作经历": "work", "工作经验": "work", "work experience": "work", "employment": "work",
    "项目经历": "projects", "项目经验": "projects", "projects": "projects",
    "技能": "skills", "技能与证书": "skills", "专业技能": "skills", "skills": "skills",
    "证书": "skills", "certifications": "skills", "其他信息": "other",
}
DEGREE = r"博士|硕士|本科|学士|大专|中专|bachelor(?:'s)?|master(?:'s)?|doctorate|phd"
DEGREE_NAME = {"本科": "本科", "硕士": "硕士", "博士": "博士", "大专": "大专", "中专": "中专",
               "学士": "本科", "bachelor": "本科", "master": "硕士", "doctorate": "博士", "phd": "博士"}
EDUCATION_FIELDS = {"education_level", "education_mode", "major", "major_group", "graduation_year", "education_start_year", "degree"}
EDUCATION_RANGE = re.compile(r"\b(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?\s*(?:至|到|[-–—~～])\s*((?:19|20)\d{2})([./年-]\d{1,2}月?)?\b")
SCHOOL = re.compile(r"大学|学院|university", re.I)
SCHOOL_KEY = re.compile(r"[\u3400-\u9fff]{1,40}(?:大学|学院)")
SCHOOL_ACTIVITY = re.compile(r"^(?:在校期间|社团经历|校内活动|校园活动)|(?:大学|学院)\s*(?:红十字会|学生会|志愿者协会)")
DOM_EDUCATION_VALUE = re.compile(r"\s*(?P<major>[^•·・\r\n]{2,40}?)\s*[•·・]\s*(?P<degree>博士|硕士|本科|大专|中专)\s*")
KNOWN_SCHOOLS = ("湖南工业大学科技学院", "广州科技职业技术大学", "广东药科大学", "广东海洋大学",
                 "广东科技学院", "广州华立学院", "湖南工业大学", "武汉工程大学")


def school_name(text):
    compact = re.sub(r"\s+", "", text)
    known = [name for name in KNOWN_SCHOOLS if name in compact]
    if known:
        name = max(known, key=len)
        suffix = compact.partition(name)[2]
        if not re.match(r"[\u3400-\u9fff]{1,8}学院", suffix):
            return name
    match = SCHOOL_KEY.search(text)
    return match[0] if match else None
TOOL = r"[A-Za-z][A-Za-z0-9.+#/-]{0,30}|[\u4e00-\u9fff]{2,12}(?=\s|[、，。；]|$)"
PRACTICE = re.compile(rf"(?:使用|运用|操作|used|worked with)\s*({TOOL})(?:\s*[,、/]\s*({TOOL}))?", re.I)
CERT = re.compile(r"(?:持有|已取得|已通过|获得)\s*([^，。；;]{2,42}?(?:证书|资格证|资格))(?=[，。；;\s]|$)")
OUTCOME = re.compile(r"(?:交付|上线|落地|降低|提升|节约|delivered|launched|reduced|improved)", re.I)
EXPLICIT_FIELD = {
    "学历": "education_level", "学习形式": "education_mode", "专业": "major", "专业大类": "major_group",
    "毕业届别": "graduation_year", "应届资格": "graduate_status",
    "技能": "skills", "工具": "tools", "实操工具": "hands_on_tools", "证书": "certificates",
}
BLOCK_NEGATIVE = re.compile(r"未交付|未上线|未(?:实际)?使用|不会|不能|禁止|从未|无需|没有|\bnot\b|\bnever\b", re.I)


@dataclass(frozen=True)
class ResumeObservation:
    field: str
    value: str
    source: str
    quote: str
    start: int
    end: int
    quality: str
    conflict: bool = False
    record_id: str | None = None
    date_precision: str | None = None
    record_school: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def section_lines(text: str, extra_headings: dict[str, str] | None = None):
    section = "other"
    headings = HEADINGS if extra_headings is None else {**HEADINGS, **extra_headings}
    for line in re.finditer(r"[^\r\n]+", text):
        heading = line[0].strip(" #：:").casefold()
        if heading in headings:
            section = headings[heading]
        else:
            yield section, line


REPORT_MAJOR_LABEL = re.compile(
    r"(?:^|[\s，,；;|｜])(?:主修专业|专业名称|专业代码|专业编号|专业)"
    r"\s*[:：]\s*(?P<value>[^，,；;|｜。\r\n]+)"
)


def report_major_observations(sources, observations):
    """Recover explicit inline major labels while keeping the legacy parser intact."""
    anchors = {}
    for item in observations:
        if item.field in EDUCATION_FIELDS:
            for line in re.finditer(r"[^\r\n]+", item.quote):
                anchors[(item.source, item.start + line.start())] = item
    for source, text in sources.items():
        for section, line in section_lines(text):
            if section not in {"education", "basic", "other"} or re.search(r"招聘要求|岗位要求|求职意向|期望岗位", line[0]):
                continue
            for match in REPORT_MAJOR_LABEL.finditer(line[0]):
                value = re.split(r"\s+(?:学历|学校|毕业时间)\s*[:：]", match["value"], maxsplit=1)[0].strip()
                if not value or len(value) > 40:
                    continue
                item = anchors.get((source, line.start()))
                yield ResumeObservation(
                    "major_group" if known_major_group(value) else "major", value, source,
                    line[0], line.start(), line.end(), "ocr_text" if source == "ocr" else "source_text",
                    record_id=item.record_id if item else f"{source}:{line.start()}",
                    record_school=item.record_school if item else None,
                )


def _education_observations(line: str, section: str) -> list[tuple[str, str]]:
    # Natural text is used only when it explicitly describes an education record.
    interval = EDUCATION_RANGE.search(line)
    if section not in {"education", "basic", "other"} or not (
        section == "education" or re.search(r"毕业于|就读于|获.*学位|大学|学院|graduated|degree|university", line, re.I)
        or interval and re.search(DEGREE, line, re.I)
        or re.fullmatch(r"\s*(?:预计\s*)?(?:19|20)\d{2}\s*(?:届|年?\s*毕业)\s*", line)
    ):
        return []
    if re.search(r"要求|招聘|招生|未取得|未毕业|未获得|\bnot\b", line, re.I):
        return []
    values = []
    degree = re.search(DEGREE, line, re.I)
    if degree and degree[0] in {"博士", "硕士", "本科", "大专", "中专"} and not line[degree.end():].startswith("学位"):
        values.append(("education_level", DEGREE_NAME[degree[0].casefold().removesuffix("'s")]))
    for degree_token in re.finditer(r"学士|硕士学位|博士学位|bachelor(?:'s)?|master(?:'s)?|doctorate|phd", line, re.I):
        values.append(("degree", degree_token[0]))
    mode = re.search(r"非全日制|全日制", line)
    if mode:
        values.append(("education_mode", mode[0]))
    major = re.search(r"(?:专业\s*[:：]\s*|主修\s*)([^，。；、:：]{2,28})", line)
    if major:
        values.append(("major", major[1].strip()))
    elif section == "education" and find_major(line):
        values.append(("major", line.strip()))
    elif section == "education" and known_major_group(line):
        values.append(("major_group", normalize_major_name(line)))
    elif degree:
        prefix = line[:degree.start()]
        schools = list(SCHOOL.finditer(prefix))
        if schools:
            prefix = prefix[schools[-1].end():]
        prefix = re.sub(r"非全日制|全日制", "", prefix).strip(" \t,，·•・|｜/\"“’「」")
        if find_major(prefix) or known_major_group(prefix) or (
            (interval or schools) and re.fullmatch(r"[\u3400-\u9fff][\u3400-\u9fff（）()·]{1,27}", prefix)
        ):
            values.append(("major", prefix))
    graduation = re.search(r"(20\d{2})\s*年?\s*(?:毕业|届)", line)
    if graduation:
        values.append(("graduation_year", graduation[1]))
    if interval:
        values.append(("graduation_year", interval[1]))
        values.append(("education_start_year", interval[0][:4]))
    return values


def extract_resume_observations(sources: dict[str, str]) -> list[ResumeObservation]:
    observations = []
    for source, text in sources.items():
        quality = "ocr_text" if source == "ocr" or source.startswith("ocr:") else "source_text"
        record_id = None
        record_has_interval = False
        record_degree = None
        record_school = None
        lines = list(section_lines(text))
        handled = set()
        if source == "dom":
            for index in range(2, len(lines)):
                _, fields_line = lines[index]
                _, years_line = lines[index-1]
                _, school_line = lines[index-2]
                fields_match = DOM_EDUCATION_VALUE.fullmatch(fields_line[0])
                years = EDUCATION_RANGE.fullmatch(years_line[0].strip())
                school = school_line[0].strip()
                if not fields_match or not years or not re.fullmatch(r"[\u3400-\u9fff·（）()]{2,38}(?:大学|学院|学校)", school):
                    continue
                if text[school_line.end():years_line.start()].strip() or text[years_line.end():fields_line.start()].strip():
                    continue
                record = f"{source}:{school_line.start()}"
                major = fields_match["major"].strip()
                for field, value, line in (
                    ("major_group" if known_major_group(major) else "major", major, fields_line),
                    ("education_level", fields_match["degree"], fields_line),
                    ("education_start_year", years[0][:4], years_line),
                    ("graduation_year", years[1], years_line),
                ):
                    observations.append(ResumeObservation(field, value, source, line[0], line.start(), line.end(),
                                                          quality, record_id=record, record_school=school))
                handled.update((index-2,index-1,index))
        for index, (section, line) in enumerate(lines):
            if index in handled:
                continue
            quote = line[0]
            start = line.start()
            if section == "education" and index:
                previous_section, previous = lines[index-1]
                prefix = previous[0].strip()
                if (previous_section == section and not text[previous.end():start].strip() and
                        any(2 <= len(prefix) < len(name) and name.startswith(prefix) and
                            quote.lstrip().startswith(name[len(prefix):]) for name in KNOWN_SCHOOLS)):
                    start = previous.start()
                    quote = text[start:line.end()]
            school_activity = bool(SCHOOL_ACTIVITY.search(quote.strip()))
            values = [] if school_activity else _education_observations(quote, section)
            label = re.fullmatch(r"\s*([^:：]+)[:：]\s*(.*?)\s*", quote)
            if label and label[1].strip() in EXPLICIT_FIELD and label[2]:
                key = EXPLICIT_FIELD[label[1].strip()]
                if key in {"skills", "tools", "hands_on_tools", "certificates"}:
                    values.extend((key, value.strip()) for value in re.split(r"[,，、;；]", label[2])
                                  if value.strip() not in {"", "无", "没有"})
                elif key not in {"education_level", "education_mode"}:
                    values.append((key, label[2]))
                elif key == "education_level":
                    values.extend(_education_observations(label[2], "education"))
                else:
                    values.append((key, label[2]))
            values = [("major_group", normalize_major_name(value))
                      if field == "major" and known_major_group(value) else (field, value)
                      for field, value in values]
            education_row = any(name in EDUCATION_FIELDS for name, _ in values)
            interval = EDUCATION_RANGE.search(quote) if education_row else None
            school = school_name(quote) if not school_activity and (education_row or section == "education") else None
            degree = next((value for name, value in values if name == "education_level"), None)
            if (school and (section == "education" or school != record_school)) or (
                    education_row and (record_id is None or (interval and record_has_interval)
                                             or degree and record_degree and degree != record_degree)):
                record_id = f"{source}:{start}"
                record_has_interval = False
                record_degree = None
                record_school = school
            record_has_interval |= bool(interval)
            record_degree = degree or record_degree
            if not BLOCK_NEGATIVE.search(quote):
                for match in PRACTICE.finditer(quote):
                    values.extend((field, tool) for tool in match.groups() if tool for field in ("tools", "hands_on_tools"))
                values.extend(("certificates", match[1]) for match in CERT.finditer(quote))
                if section == "projects" and OUTCOME.search(quote):
                    values.append(("achievement", quote.strip()))
            if section in ("work", "projects") and quote.strip():
                values.append(("work_record" if section == "work" else "project_record", quote.strip()))
            observations.extend(ResumeObservation(
                                    field, value, source, quote, start, line.end(), quality,
                                    record_id=record_id if field in EDUCATION_FIELDS else None,
                                    date_precision=("month" if interval and interval[1] == value and interval[2]
                                                    else "year") if field == "graduation_year" else None,
                                    record_school=record_school if field in EDUCATION_FIELDS else None)
                                for field, value in values)
    unique = list(dict.fromkeys(observations))
    records = {}
    for item in unique:
        if item.record_id:
            records.setdefault(item.source, {})[item.record_id] = item.record_school
    schools = {school for entries in records.values() for school in entries.values() if school}
    single = len(schools) <= 1 and all(len(entries) <= 1 for entries in records.values())
    grouped = {}
    for item in unique:
        if item.field not in EDUCATION_FIELDS | {"graduate_status"}:
            continue
        key = item.record_school or item.record_id
        if item.record_school and any(
            list(entries.values()).count(item.record_school) > 1 for entries in records.values()
        ):
            key = item.record_id
        grouped.setdefault((None if single else key, item.field), []).append(item)
    def comparable(item):
        major = find_major(item.value) if item.field == "major" else None
        return normalize_major_name(major.name if major else item.value)

    conflicts = {item for items in grouped.values() if len({comparable(row) for row in items}) > 1
                 for item in items}
    return [replace(item, conflict=True) if item in conflicts else item for item in unique]

@dataclass(frozen=True)
class SourceBlock:
    source: str
    section: str
    start: int
    end: int
    text: str


def split_resume_sources(sources: dict[str, str], max_chars: int = 4000) -> list[SourceBlock]:
    """Partition original strings without dropping whitespace or changing offsets."""
    if max_chars < 1:
        raise ValueError("invalid_block_size")
    result = []
    for source, text in sources.items():
        boundaries = [(0, "basic")]
        for line in re.finditer(r"[^\r\n]+", text):
            heading = line[0].strip(" #：:").casefold()
            if heading in HEADINGS:
                boundaries.append((line.start(), HEADINGS[heading]))
        boundaries.append((len(text), "other"))
        for (start, section), (end, _) in zip(boundaries, boundaries[1:]):
            while start < end:
                stop = min(start + max_chars, end)
                if stop < end:
                    newline = text.rfind("\n", start + 1, stop)
                    if newline >= 0:
                        stop = newline + 1
                result.append(SourceBlock(source, section, start, stop, text[start:stop]))
                start = stop
    return result
