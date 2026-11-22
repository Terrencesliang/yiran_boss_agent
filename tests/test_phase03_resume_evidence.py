"""Historical OCR sentence replays and local evidence contract regressions."""

from datetime import date
import json
import socket

import pytest

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.cli import main
from boss_agent.recommendation import rank_recruitment_preview
from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_parser import parse_recruitment_task
from boss_agent.task_schema import Criterion
from test_browser_recruitment_preview import detail, write_mock_data
from test_phase03_task_intake import JOB, ORIGINAL


# Sanitized OCR shapes from candidate_audit.csv, with school/names removed.
HISTORICAL = [
    ("A01", "人工智能本科 2023 - 2027", 2027),
    ("A02", "行政管理 , 本科 2022 - 2026", 2026),
    ("A05", "工商管理 , 本科 2021 - 2025", 2025),
    ("A06", "应用心理学本科 2022 - 2026", 2026),
    ("A08", "计算机科学与技术 , 本科 2022 - 2026", 2026),
    ("A09", "电子信息类 , 本科 2021 - 2025", 2025),
    ("A10", "人力资源管理本科 2022 - 2026", 2026),
    ("A11", "音乐表演 “ 本科 2021 - 2025", 2025),
    ("A12", "人力资源管理 , 本科 2024 - 2025", 2025),
    ("A13", "市场营销 , 本科 2024 - 2026", 2026),
    ("A14", "工商管理 , 本科 2022 - 2024", 2024),
]


def rule(field, operator, value):
    return Criterion(field, operator, value, field)


@pytest.mark.parametrize("key,text,year", HISTORICAL, ids=[row[0] for row in HISTORICAL])
def test_historical_education_interval(key, text, year):
    resume = extract_resume_evidence({"ocr": text}, candidate_id=key, capture_status="partial")
    finding = evaluate_condition(rule("graduation_year", "equals", year), resume)
    assert finding.status == "PASS"
    evidence, = finding.evidence
    assert (evidence.source, evidence.quote, evidence.start, evidence.end, evidence.value) == (
        "ocr", text, 0, len(text), year,
    )
    assert evidence.anonymous_candidate_id and evidence.anonymous_candidate_id != key
    assert evidence.date_precision == "year" and evidence.capture_status == "partial"
    assert evaluate_condition(rule("education_mode", "equals", "full_time"), resume).status == "UNKNOWN"


@pytest.mark.parametrize("text,precision", [
    ("教育经历\n样例大学\n软件工程\n学士\n2022.09–2026.06", "month"),
    ("2026 届", "year"),
    ("预计 2026 年毕业", "year"),
])
def test_graduation_formats(text, precision):
    resume = extract_resume_evidence({"dom": text})
    evidence = evaluate_condition(rule("graduation_year", "equals", 2026), resume).evidence[0]
    assert evidence.date_precision == precision
    if precision == "month":
        groups = {resume.facts[field][0].evidence.record_id for field in ("major", "education_level", "graduation_year")}
        assert len(groups) == 1
        assert resume.facts["education_level"][0].value == "bachelor"


def test_different_education_records_cannot_donate_fields():
    task = parse_recruitment_task("岗位：样例岗位，计算机大类全日制本科，2026届")
    text = ("教育经历\n样例甲大学\n软件工程\n本科 2022 - 2026\n"
            "样例乙大学\n行政管理\n全日制硕士 2026 - 2028")
    result = rank_recruitment_preview(task, [{"candidate_id": "demo", "sources": {"dom": text}}])
    assert result["summary"]["needs_review"] == 1
    assert result["summary"]["qualified"] == 0
    text = text.replace("本科 2022", "全日制本科 2022")
    result = rank_recruitment_preview(task, [{"candidate_id": "demo", "sources": {"dom": text}}])
    assert result["summary"]["qualified"] == 1


def test_cross_source_and_same_record_year_conflicts():
    sources = {"dom": "样例大学 软件工程 全日制本科 2022.09–2026.06",
               "ocr": "样例大学 软件工程 非全日制本科 2022 - 2027"}
    resume = extract_resume_evidence(sources)
    for field, expected in [("graduation_year", 2026), ("education_mode", "full_time")]:
        result = evaluate_condition(rule(field, "equals", expected), resume)
        assert result.status == "UNKNOWN" and {item.source for item in result.evidence} == {"dom", "ocr"}
        assert all(item.conflict for item in result.evidence)


def test_different_schools_across_sources_do_not_donate_fields():
    task = parse_recruitment_task("岗位：样例岗位，计算机大类全日制本科，2026届")
    sources = {"dom": "教育经历\n样例甲大学\n软件工程\n本科 2022 - 2026",
               "ocr": "教育经历\n样例乙大学\n全日制本科 2022 - 2026"}
    result = rank_recruitment_preview(task, [{"candidate_id": "demo", "sources": sources}])
    assert result["summary"]["needs_review"] == 1


def test_offline_capture_failure_is_distinct_from_unknown_and_fail():
    task = parse_recruitment_task("岗位：样例岗位，全日制本科")
    result = rank_recruitment_preview(task, [{"candidate_id": "demo", "capture_status": "CAPTURE_FAILED"}])
    assert result["capture_failed_candidates"][0]["eligibility_status"] == "CAPTURE_FAILED"
    assert result["review_candidates"] == result["rejected_candidates"] == []


@pytest.mark.parametrize("text,positive,limit", [
    ("2025.05.01–2025.05.20\n招聘实习\n负责筛选简历及面试安排", "PASS", "PASS"),
    ("人事助理\n2024-02-29 至 2025-02-28", "PASS", "PASS"),
    ("2024-02-29 至 2025-03-01 人事助理", "PASS", "FAIL"),
    ("2024-01-01 至 2025-01-01 人事助理", "PASS", "PASS"),
    ("2024-01-01 至 2025-01-02 人事助理", "PASS", "FAIL"),
    ("2024-01-01 至 2024-08-01 人事助理\n2024-06-01 至 2025-01-01 招聘实习", "PASS", "PASS"),
    ("2024-01-01 至 2024-07-01 人事助理\n2024-08-01 至 2025-02-02 招聘实习", "PASS", "FAIL"),
    ("2025-01-01 至今 人事助理", "PASS", "PASS"),
    ("2024.01 至 2025.01 人事助理", "PASS", "UNKNOWN"),
    ("2024 至 2025 岗位：招聘专员", "PASS", "UNKNOWN"),
    ("曾担任人事助理", "PASS", "UNKNOWN"),
    ("2025-01-01 至 2025-01-10\n职责：办理入职、员工关系、组织员工培训", "PASS", "PASS"),
    ("2025-01-01 至 2025-01-10\n职责：开发 AI 面试系统及简历筛选功能", "UNKNOWN", "UNKNOWN"),
    ("2025-01-01 至 2025-01-10 学习过人力资源课程", "UNKNOWN", "UNKNOWN"),
    ("2025-01-01 至 2025-01-10 求职意向：人事助理", "UNKNOWN", "UNKNOWN"),
    ("2025-01-01 至 2025-01-10 招聘实习\n曾担任人事助理", "PASS", "UNKNOWN"),
], ids=[
    "short-internship", "leap-anniversary", "leap-plus-day", "twelve-months", "twelve-plus-day",
    "overlap", "separate-sum", "ongoing", "month-range", "year-range", "undated", "duties-only",
    "hr-software", "course", "intent", "missing-extra-period",
])
def test_hr_calendar_boundaries(text, positive, limit):
    source = "工作经历\n" + text
    resume = extract_resume_evidence({"dom": source}, as_of=date(2026, 1, 1))
    for operator, value, expected in [("gt", 0, positive), ("lte", 12, limit)]:
        finding = evaluate_condition(rule("hr_experience_months", operator, value), resume)
        assert finding.status == expected
        assert all(source[item.start:item.end] == item.quote for item in finding.evidence)


def test_hr_cross_source_date_conflict():
    sources = {"dom": "工作经历\n2025.01.01 至 2025.03.01 招聘实习",
               "ocr": "工作经历\n2025.01.01 至 2025.04.01 招聘实习"}
    finding = evaluate_condition(rule("hr_experience_months", "lte", 12), extract_resume_evidence(sources))
    assert finding.status == "UNKNOWN" and all(item.conflict for item in finding.evidence)


def test_original_request_consumes_adapter_observations(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOSS_RECRUITMENT_AUDIT_DIR", str(tmp_path / "audit"))
    def forbidden(*args, **kwargs):
        pytest.fail("refetch, reparsing or real contact is not allowed")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ChromeDebugClient, "click_recommend_card_greet", forbidden)
    monkeypatch.setattr("boss_agent.recommendation.extract_resume_evidence", forbidden)
    root = tmp_path / "fixtures"
    common = "教育经历\n样例学院 人工智能 全日制本科 2022.09–2026.06\n工作经历\n"
    records = {
        key: detail(key, common + body) for key, body in {
            "short": "2025.05.01–2025.05.20 招聘实习\n使用 Agent 汇总招聘数据",
            "other": "2025.05.03–2025.05.20 人事助理",
            "unknown": "2025.05.01–2025.05.20 开发 AI 面试系统",
        }.items()
    }
    cards = [{"candidateId": key} for key in (*records, "broken")]
    write_mock_data(root, cards, records)
    assert main([
        "action", "screen-recommend-detail", "--request", ORIGINAL, "--job-context", JOB,
        "--mock-dir", str(root), "--report-path", str(tmp_path / "preview.json"),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["contact_preview"] == ["short", "other"]
    assert report["review_candidates"][0]["hard_filter_status"] == "UNKNOWN"
    assert report["capture_failed_candidates"][0]["eligibility_status"] == "CAPTURE_FAILED"
    assert report["summary"]["actual_successful_contacts"] == 0
    finding = report["ranked_candidates"][0]["hard_filters"][-1]
    assert finding["fields_used"] == ["hr_experience_months"]
    assert finding["evidence"][0]["date_precision"] == "day/day"
