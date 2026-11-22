"""Deterministic calendar-month bounds for resume work records."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import re

from boss_agent.resume_structure import ResumeObservation, section_lines


ENDPOINT = r"(?:19|20)\d{2}(?:[-./年](?:0?[1-9]|1[0-2])(?:[-./月](?:0?[1-9]|[12]\d|3[01])日?)?月?(?!\d)|年)?"
PERIOD = re.compile(
    rf"(?<!\d)({ENDPOINT})\s*(?:(至今|到现在|在职|present)|(至|到|[-–—~～])\s*({ENDPOINT}|至今|在职|现在|present))(?!\d|[-/年]\d)",
    re.I,
)
SCOPE = re.compile(r"(?<!未)(?<!不)(岗位|职位|行业|职责|担任|从事|role|industry|duties)\s*[:：]?\s*([^，。；;|｜\n]+)", re.I)
SCOPE_TYPES = {"岗位": "role", "职位": "role", "担任": "role", "从事": "duty",
               "行业": "industry", "职责": "duty", "role": "role", "industry": "industry", "duties": "duty"}
NON_WORK = re.compile(r"教育|就读|学位|学历|毕业|学士|硕士|博士|bachelor|degree", re.I)
WORK_SIGNAL = re.compile(r"任职|担任|入职|岗位|职位|实习|兼职|供职|就业|employed|worked at|role", re.I)
ONGOING = {"至今", "到现在", "在职", "现在", "present"}
HR_ROLE = re.compile(r"人力资源(?:专员|助理|实习|经理)?|人事(?:专员|助理|实习|经理)|招聘(?:专员|助理|实习|经理|顾问)|员工关系(?:专员|助理)|培训(?:专员|助理)|HRBP|\bHR\b", re.I)
HR_DUTY = re.compile(r"筛选简历|简历筛选|邀约面试|面试安排|办理.{0,8}(?:入职|离职|转正|调岗)|入转调离|员工关系|劳动合同|组织.{0,8}员工培训")
NON_HR = re.compile(r"(?:开发|搭建|设计|测试).{0,20}(?:面试系统|招聘系统|人事软件|HR\s*软件)|(?:面试系统|招聘系统|人事软件|HR\s*软件).{0,16}(?:开发|搭建|设计|测试)|求职意向|期望岗位|课程|学习过", re.I)
OTHER_ROLE = re.compile(r"开发工程师|全栈开发|AI训练师|软件工程师|销售专员|电商运营|数据标注", re.I)
HR_DURATION_POLICY = "hr-calendar-anniversary-v1"


@dataclass(frozen=True)
class WorkRecord:
    evidence: ResumeObservation
    start: str | None
    end: str | None
    scopes: tuple[tuple[str, str], ...]
    kind: str
    hr_work: bool | None = None


@dataclass(frozen=True)
class MonthBounds:
    minimum: int
    maximum: int | None
    evidence: tuple[ResumeObservation, ...]
    uncertain: bool
    conflict: bool = False


def extract_work_records(sources: dict[str, str]) -> list[WorkRecord]:
    records = []
    for source, text in sources.items():
        lines = list(section_lines(text))
        consumed_until = 0
        for index, (section, line) in enumerate(lines):
            if line.start() < consumed_until:
                continue
            if section in {"education", "projects", "skills"}:
                continue
            period = PERIOD.search(line[0])
            undated_role = re.search(r"任职于|曾担任|入职|工作起止|起止时间", line[0])
            if section == "work" and not period and index + 1 < len(lines):
                next_section, following = lines[index + 1]
                if next_section == "work" and PERIOD.search(following[0]):
                    continue
            if section != "work" and (NON_WORK.search(line[0]) or not WORK_SIGNAL.search(line[0])):
                continue
            if not period and not undated_role:
                continue
            start, end = line.start(), line.end()
            if period and section == "work" and index:
                previous_section, previous = lines[index - 1]
                if (previous_section == section and not PERIOD.search(previous[0])
                        and not HR_DUTY.search(previous[0]) and not SCOPE.search(line[0])
                        and (HR_ROLE.search(previous[0]) or OTHER_ROLE.search(previous[0]))
                        and not (HR_ROLE.search(line[0]) or OTHER_ROLE.search(line[0]))):
                    start = previous.start()
            if period:
                for next_index in range(index + 1, min(len(lines), index + 4)):
                    next_section, following = lines[next_index]
                    if next_section != section or PERIOD.search(following[0]):
                        break
                    if re.match(r"\s*(?:曾担任|另曾|任职于)", following[0]):
                        break
                    after = lines[next_index + 1] if next_index + 1 < len(lines) else None
                    if after and after[0] == section and PERIOD.search(after[1][0]) and not HR_DUTY.search(following[0]):
                        break
                    if not (SCOPE.match(following[0].strip()) or HR_DUTY.search(following[0])
                            or section == "work" and HR_ROLE.search(following[0])):
                        break
                    end = following.end()
            consumed_until = end
            quote = text[start:end]
            scopes = tuple((SCOPE_TYPES[m[1].lower()], m[2].strip()) for m in SCOPE.finditer(quote))
            kind = "internship" if "实习" in quote else "part_time" if "兼职" in quote else "work_unspecified"
            hr_work = None
            if NON_HR.search(quote):
                hr_work = False
            elif (section == "work" or undated_role or scopes) and (HR_ROLE.search(quote) or HR_DUTY.search(quote)):
                hr_work = True
            elif OTHER_ROLE.search(quote):
                hr_work = False
            start_value = period[1] if period else None
            end_value = (period[2] or period[4]).lower() if period else None
            precision = "/".join(_date_precision(value) for value in (start_value, end_value))
            records.append(WorkRecord(
                ResumeObservation("work_period", quote, source, quote, start, end,
                                  "ocr_text" if source.startswith("ocr") else "source_text", date_precision=precision),
                start_value, end_value, scopes, kind, hr_work,
            ))
    return records


def _date_precision(endpoint: str | None) -> str:
    if endpoint is None:
        return "unknown"
    if endpoint in ONGOING:
        return "as_of"
    return {1: "year", 2: "month", 3: "day"}[len(re.findall(r"\d+", endpoint))]


def _month_range(endpoint: str, as_of: date) -> tuple[int, int]:
    if endpoint in ONGOING:
        current = as_of.year * 12 + as_of.month - 1
        return current, current
    numbers = [int(item) for item in re.findall(r"\d+", endpoint)]
    first = numbers[0] * 12
    if len(numbers) >= 2:
        month = first + numbers[1] - 1
        return month, month
    return first, first + 11


@dataclass(frozen=True)
class HRBounds:
    minimum_days: int
    maximum_days: int | None
    anchor: date | None
    evidence: tuple[ResumeObservation, ...]
    has_work: bool
    conflict: bool

    def threshold(self, months: int) -> tuple[int, int]:
        if self.anchor is None:
            years, remainder = divmod(months, 12)
            return years * 365 + remainder * 28, years * 366 + remainder * 31
        month_index = self.anchor.year * 12 + self.anchor.month - 1 + months
        year, month = divmod(month_index, 12)
        if year > 9999:
            return date.max.toordinal(), date.max.toordinal()
        days = (date(year, month + 1, min(self.anchor.day, monthrange(year, month + 1)[1])) - self.anchor).days
        return days, days


def _day_range(endpoint: str, as_of: date) -> tuple[date, date]:
    if endpoint in ONGOING:
        return as_of, as_of
    numbers = [int(item) for item in re.findall(r"\d+", endpoint)]
    if len(numbers) == 1:
        return date(numbers[0], 1, 1), date(numbers[0], 12, 31)
    year, month = numbers[:2]
    if len(numbers) == 2:
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    exact = date(*numbers)
    return exact, exact


def report_day_windows(start: str | None, end: str | None, as_of: date) -> dict:
    """Expose the same calendar endpoint bounds used by the existing HR engine."""
    def encode(first, last):
        return [first.isoformat(), last.isoformat()]

    if not start or not end:
        return {"status": "MISSING_DATE", "guaranteed": None, "possible": None}
    try:
        first, latest_start = _day_range(start, as_of)
        earliest_end, last = _day_range(end, as_of)
    except (ValueError, IndexError, TypeError):
        return {"status": "INVALID_DATE", "guaranteed": None, "possible": None}
    if first > last:
        return {"status": "REVERSED_DATES", "guaranteed": None, "possible": None}
    if first > as_of:
        return {"status": "AFTER_REFERENCE_DATE", "guaranteed": None, "possible": None}
    last, earliest_end = min(as_of, last), min(as_of, earliest_end)
    return {"status": "BOUNDED", "guaranteed": encode(latest_start, earliest_end) if latest_start < earliest_end else None,
            "possible": encode(first, last)}


def measure_hr_days(records: list[WorkRecord], as_of: date) -> HRBounds | None:
    """Union half-open day ranges and keep uncertainty in date and HR scope."""
    relevant = [record for record in records if record.hr_work is not False]
    if not relevant:
        return None
    evidence = tuple(record.evidence for record in relevant)
    minimum, maximum = set(), set()
    unbounded = False
    anchors = set()
    dates_by_role = {}
    has_work = any(record.hr_work is True for record in relevant)
    for record in relevant:
        if not record.start or not record.end:
            unbounded = True
            continue
        try:
            first, latest = _day_range(record.start, as_of)
            earliest, last = _day_range(record.end, as_of)
        except ValueError:
            unbounded = True
            continue
        last, earliest = min(as_of, last), min(as_of, earliest)
        if first > last:
            unbounded = True
            continue
        key = re.sub(r"[\s:：|｜，,。]+", "", PERIOD.sub("", record.evidence.quote))
        if key and record.hr_work:
            role = HR_ROLE.search(key)
            signature = role[0].casefold() if role else key.casefold()
            dates_by_role.setdefault(signature, {}).setdefault(record.evidence.source, set()).add((first, latest, earliest, last))
        if record.hr_work:
            anchors.add(first)
            minimum.update(range(latest.toordinal(), earliest.toordinal()))
        maximum.update(range(first.toordinal(), max(first.toordinal() + 1, last.toordinal())))
    conflict = any(len(sources) > 1 and len({frozenset(periods) for periods in sources.values()}) > 1
                   for sources in dates_by_role.values())
    anchor = min(anchors, default=as_of)
    # Highly imprecise starts can change the anniversary length.
    if any(record.start and _date_precision(record.start) != "day" for record in relevant):
        anchor = None
    return HRBounds(max(len(minimum), int(has_work)), None if unbounded or not has_work else len(maximum),
                    anchor, evidence, has_work, conflict)


def _scope_match(record: WorkRecord, category: str | None, term: str | None) -> bool | None:
    if term is None:
        return True
    if category == "kind":
        return record.kind == term if record.kind != "work_unspecified" else None
    texts = [value for key, value in record.scopes if category is None or key == category]
    if not texts:
        return None
    return any(term.casefold() in value.casefold() if re.search(r"[\u3400-\u9fff]", term)
               else re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", value, re.I) is not None for value in texts)


def measure_work_months(records: list[WorkRecord], as_of: date, *,
                        category: str | None = None, term: str | None = None) -> MonthBounds | None:
    """Union calendar-month buckets, including end/current months; never sum overlapping periods."""
    minimum, maximum = set(), set()
    evidence = []
    uncertain = False
    unbounded = False
    confirmed = False
    periods = {}
    current = as_of.year * 12 + as_of.month - 1
    for record in records:
        matched = _scope_match(record, category, term)
        if matched is False:
            continue
        evidence.append(record.evidence)
        confirmed |= matched is True
        key = PERIOD.sub("", record.evidence.quote).strip().casefold()
        if key and record.start and record.end:
            periods.setdefault(key, {}).setdefault(record.evidence.source, set()).add(
                (_month_range(record.start, as_of), _month_range(record.end, as_of))
            )
        if not record.start or not record.end:
            uncertain = unbounded = True
            continue
        first, latest_start = _month_range(record.start, as_of)
        earliest_end, last = _month_range(record.end, as_of)
        if first > min(last, current):
            uncertain = unbounded = True
            continue
        last = min(last, current)
        earliest_end = min(earliest_end, current)
        maximum.update(range(first, last + 1))
        if matched is True:
            minimum.update(range(latest_start, earliest_end + 1))
        uncertain |= matched is None or first != latest_start or earliest_end != last
    if not evidence:
        return None
    # A relevant total cannot be proven by unclassified history alone.
    conflict = any(len(by_source) > 1 and len({frozenset(items) for items in by_source.values()}) > 1
                   for by_source in periods.values())
    if conflict:
        return MonthBounds(0, None, tuple(dict.fromkeys(evidence)), True, True)
    return MonthBounds(len(minimum), len(maximum) if not unbounded and confirmed else None,
                       tuple(dict.fromkeys(evidence)), uncertain or not confirmed)
