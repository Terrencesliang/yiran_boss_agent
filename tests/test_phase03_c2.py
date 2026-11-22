from dataclasses import replace

import pytest

from boss_agent.recommendation import rank_recruitment_preview
from boss_agent.rule_engine import extract_resume_evidence
from boss_agent.task_parser import parse_recruitment_task
from boss_agent.task_validator import task_blockers
from test_phase03_task_intake import JOB, ORIGINAL


@pytest.mark.parametrize("text,cap,mode,limit", [
    ("在推荐牛人里找全日制本科，邀请符合要求的7人沟通", 7, "full_time", False),
    ("只看硕士，联系不超过六位候选人", 6, None, True),
    ("本科候选人，请给2人发招呼", 2, None, False),
    ("先预览本科名单，暂不发送", None, None, False),
    ("硕士学历，最多联系5人", 5, None, True),
])
def test_exposed_failed_intents_regress(text, cap, mode, limit):
    task = replace(parse_recruitment_task(text), job=JOB)
    assert task_blockers(task) == []
    assert task.target_success_count == cap and task.contact_limit_only is limit
    assert task.action == ("screen" if cap is None else "greet")
    expected = {"education_level": "master" if "硕士" in text else "bachelor"}
    if mode:
        expected["education_mode"] = mode
    assert {field.field: field.value for field in task.hard_filters} == expected
    assert not task.soft_preferences


EDUCATION = (
    "学历：本科",
    "学习形式：全日制",
    "专业：人工智能",
    "毕业届别：2026",
    "人力资源经验：2个月",
)


@pytest.mark.parametrize("omit", range(len(EDUCATION)))
def test_any_missing_required_fact_prevents_shortlist(omit):
    task = replace(parse_recruitment_task(ORIGINAL), job=JOB)
    text = "\n".join(line for index, line in enumerate(EDUCATION) if index != omit)
    report = rank_recruitment_preview(task, [{
        "candidate_id":"without-fact", "sources":{"dom":text}, "capture_status":"complete",
    }])
    assert report["contact_preview"] == []
    assert report["review_candidates"][0]["eligibility_status"] == "UNKNOWN"


def test_stale_structured_facts_do_not_create_contact_pass():
    task = replace(parse_recruitment_task(ORIGINAL), job=JOB)
    full = {"dom":"\n".join(EDUCATION)}
    stale = extract_resume_evidence(full)
    report = rank_recruitment_preview(task, [{
        "candidate_id":"stale", "sources":{"dom":"本科"}, "structured_evidence":stale,
        "capture_status":"complete",
    }])
    assert report["contact_preview"] == []
    assert report["review_candidates"][0]["review_reason"] == "hard_condition_citation_unverified"


def test_preference_only_never_enters_contact_preview():
    task = parse_recruitment_task("岗位：设计，工具：Figma优先，给2人打招呼")
    report = rank_recruitment_preview(task,[{"candidate_id":"preference","sources":{"dom":"工具：Figma"}}])
    assert report["contact_preview"] == []
