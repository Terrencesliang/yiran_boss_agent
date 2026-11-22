from datetime import date

from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_schema import Criterion
from boss_agent.work_timeline import extract_work_records, measure_work_months


def test_month_union_scope_and_ongoing_date():
    sources = {"dom": ("工作经历\n2024-01 至 2024-06 岗位：招聘专员｜行业：咨询\n"
                       "2024/04 至 2024/09 岗位：招聘助理，实习\n"
                       "2024年09月 至今 岗位：开发工程师，兼职").replace("｜", "|")}
    as_of = date(2024, 11, 20)
    records = extract_work_records(sources)
    assert [item.kind for item in records] == ["work_unspecified", "internship", "part_time"]
    total = measure_work_months(records, as_of)
    assert (total.minimum, total.maximum) == (11, 11)
    relevant = measure_work_months(records, as_of, category="role", term="招聘")
    assert (relevant.minimum, relevant.maximum) == (9, 9)
    evidence = extract_resume_evidence(sources, as_of=as_of)
    rule = Criterion("experience_months:role:招聘", "between", [0, 12], "招聘经验不超过一年")
    assert evaluate_condition(rule, evidence).status == "PASS"


def test_year_only_and_missing_intervals_do_not_invent_duration():
    records = extract_work_records({"dom": "工作经历\n2023年至2024年 岗位：招聘专员"})
    bounds = measure_work_months(records, date(2026, 1, 1))
    assert (bounds.minimum, bounds.maximum) == (2, 24)
    sources = {"dom": "工作经历\n2023年至2024年 岗位：招聘专员\n曾担任招聘经理"}
    evidence = extract_resume_evidence(sources, as_of=date(2026, 1, 1))
    rule = Criterion("experience_months:role:招聘", "lte", 12, "经验上限")
    assert evaluate_condition(rule, evidence).status == "UNKNOWN"

def test_multiline_role_uses_one_source_span():
    text = "工作经历\n2024-01 至 2024-12\n岗位：人力资源专员"
    records = extract_work_records({"dom": text})
    assert len(records) == 1
    record = records[0]
    assert text[record.evidence.start:record.evidence.end] == record.evidence.quote
    bounds = measure_work_months(records, date(2026, 1, 1), category="role", term="人力资源")
    assert (bounds.minimum, bounds.maximum) == (12, 12)
