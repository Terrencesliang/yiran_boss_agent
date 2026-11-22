import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from boss_agent.cli import main
from boss_agent.job_profile import load_condition, load_job_profile
from boss_agent.recommendation import rank_recruitment_preview
from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_parser import parse_recruitment_task
from boss_agent.task_schema import ConditionGroup, Criterion, iter_criteria
from boss_agent.task_validator import task_blockers


CASES = [
    (
        "AI 工程师", "本科或硕士，工具：Python，项目成果：部署检索服务优先",
        "学历：硕士\n工具：Python\n项目成果：独立部署检索服务并负责监控",
        "project_outcomes", None,
    ),
    (
        "人事专员", "人力资源专业，1年以上招聘经验，工具：Excel优先",
        "专业：人力资源\n招聘经验：18个月\n工具：Excel",
        "tools", "Excel",
    ),
    (
        "电商运营", "行业：跨境电商，1年以上运营经验，工具：Shopify优先",
        "行业：跨境电商\n运营经验：2年\n工具：Shopify",
        "tools", "Shopify",
    ),
    (
        "财务会计", "证书：初级会计资格，专业：会计学优先",
        "证书：初级会计资格\n专业：会计学",
        "major", "会计学",
    ),
    (
        "UI 设计师", "技能：原型设计，工具：Figma优先",
        "技能：原型设计\n工具：Figma",
        "tools", "Figma",
    ),
]


def source(text):
    return {"resume": text}


def candidate(key, text):
    return {"candidate_id": key, "sources": source(text)}


def semantic_judge(rule, sources):
    text = sources["resume"]
    if rule.value == "部署检索服务" and "独立部署检索服务并负责监控" in text:
        return json.dumps({"status": "PASS", "evidence": [
            {"source": "resume", "quote": "独立部署检索服务并负责监控"}
        ]})
    return '{"status":"UNKNOWN","evidence":[]}'


@pytest.mark.parametrize(("job", "request_text", "resume", "field", "preferred"), CASES)
def test_five_jobs_parse_and_only_score_own_evidence(job, request_text, resume, field, preferred):
    task = parse_recruitment_task(f"岗位：{job}，{request_text}")
    assert task.job == job
    assert not task_blockers(task)
    assert task.soft_preferences[0].field == field
    if preferred:
        assert task.soft_preferences[0].value == preferred
    mock_data = [candidate(other_job, other_resume) for other_job, _, other_resume, _, _ in CASES]
    # This person meets the same hard conditions, but only demonstrates
    # unrelated preferences from other roles.
    decoy = resume.replace("独立部署检索服务并负责监控", "了解检索服务")
    if preferred:
        replacement = {"Excel": "Figma", "Shopify": "Excel", "会计学": "设计学", "Figma": "Shopify"}
        decoy = decoy.replace(preferred, replacement[preferred])
    mock_data.append(candidate("other-preferences", decoy))
    output = rank_recruitment_preview(task, mock_data, judge=semantic_judge)
    assert [(row["candidate_id"], row["score"]) for row in output["ranked_candidates"]] == [
        (job, 100), ("other-preferences", 0),
    ]
    assert sum(output["summary"][key] for key in ("qualified", "unqualified", "needs_review")) == 6
    assert output["execution_mode"] == "dry_run"
    assert all(row["score"] is None for row in output["review_candidates"] + output["rejected_candidates"])


@pytest.mark.parametrize(("resume", "expected"), [
    ("学历：本科\n工具：Python\n学习形式：全日制", "PASS"),
    ("学历：硕士\n工具：Python\n学习形式：非全日制", "FAIL"),
    ("学历：本科\n工具：Python", "UNKNOWN"),
    ("学历：本科\n工具：Python\n学习形式：全日制\n学习形式：非全日制", "UNKNOWN"),
    ("学历：大专\n工具：Python\n学习形式：全日制", "FAIL"),
])
def test_nested_three_valued_logic(resume, expected):
    task = parse_recruitment_task("岗位：工程师，(本科 OR 硕士) AND 工具:Python AND NOT 非全日制")
    assert not task_blockers(task)
    root = task.hard_filters[0]
    assert isinstance(root, ConditionGroup) and root.operator == "AND"
    result = evaluate_condition(root, extract_resume_evidence(source(resume)))
    assert result.status == expected
    for evidence in result.evidence:
        assert resume[evidence.start:evidence.end] == evidence.quote


def test_cohort_alternatives_do_not_replace_fresh_graduate_status():
    task = parse_recruitment_task("岗位：管培生，25届或26届应届生")
    assert task.hard_filters[0].value == [2025, 2026]
    for year in (2025, 2026):
        evidence = extract_resume_evidence(source(f"毕业届别：{year}"))
        assert evaluate_condition(task.hard_filters[0], evidence).status == "PASS"
        assert evaluate_condition(task.hard_filters[1], evidence).status == "UNKNOWN"


@pytest.mark.parametrize("payload", [
    "bad json", '{"status":"PASS","evidence":[]}',
    '{"status":"PASS","evidence":[{"source":"resume","quote":"从未出现"}]}',
    '{"status":"YES","evidence":[]}',
])
def test_bad_semantic_result_is_unknown_and_does_not_score(payload):
    task = parse_recruitment_task("岗位：UI 设计师，技能：原型设计，项目成果：交付设计系统优先")
    report = rank_recruitment_preview(task, [candidate("design", "技能：原型设计")],
                                      judge=lambda *_: payload)
    row = report["ranked_candidates"][0]
    assert row["score"] == 0
    assert row["score_breakdown"]["preference_1"]["finding"]["status"] == "UNKNOWN"


def test_model_exceptions_are_unknown_for_hard_semantic_conditions():
    task = parse_recruitment_task("岗位：会计，项目经历：独立完成年度关账")
    judge = Mock(side_effect=TimeoutError)
    result = rank_recruitment_preview(task, [candidate("candidate", "候选人经历")], judge=judge)
    assert not result["ranked_candidates"]
    assert result["review_candidates"][0]["hard_filters"][0]["status"] == "UNKNOWN"


def test_custom_profile_weights_and_named_editable_templates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", Mock(side_effect=AssertionError("no browser")))
    monkeypatch.setattr("boss_agent.cli.judge_project_evidence", Mock(side_effect=AssertionError("no network")))
    required = asdict(Criterion("skills", "equals", "原型设计", "岗位配置"))
    figma = asdict(Criterion("tools", "equals", "Figma", "岗位配置"))
    sketch = asdict(Criterion("tools", "equals", "Sketch", "岗位配置"))
    data = {
        "name": "UI 设计师", "description": "负责数字产品设计",
        "requirements": [required], "priority_skills": [figma],
        "scoring": [{"name": "figma", "condition": figma, "weight": 3},
                    {"name": "sketch", "condition": sketch, "weight": 1}],
        "templates": {
            "双工具": {},
            "仅Sketch": {"priority_skills": [sketch],
                         "scoring": [{"name": "sketch", "condition": sketch, "weight": 10}]},
        },
    }
    path = tmp_path / "profile.json"
    resumes = tmp_path / "candidates.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    resumes.write_text(json.dumps([candidate("b", "技能：原型设计\n工具：Sketch"),
                                   candidate("a", "技能：原型设计\n工具：Figma")]), encoding="utf-8")

    def run(template):
        assert main(["evaluate-recruitment", "--request", "岗位：UI 设计师，给1人打招呼",
                     "--candidates", str(resumes), "--job-profile", str(path), "--template", template]) == 0
        return json.loads(capsys.readouterr().out)

    first = run("双工具")
    assert [(row["candidate_id"], row["score"]) for row in first["ranked_candidates"]] == [("a", 75), ("b", 25)]
    assert first["contact_preview"] == ["a"]
    second = run("仅Sketch")
    assert second["contact_preview"] == ["b"]
    assert set(second["ranked_candidates"][0]["score_breakdown"]) == {"sketch"}
    data["templates"]["仅Sketch"]["requirements"] = [asdict(Criterion("skills", "equals", "界面动效", "用户修改"))]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    edited = run("仅Sketch")
    assert edited["contact_preview"] == []
    assert edited["summary"]["needs_review"] == 2


def test_profile_mismatch_invalid_groups_and_sensitive_nested_dimensions():
    task = parse_recruitment_task("岗位：会计，本科")
    profile = load_job_profile({"name": "设计师"})
    with pytest.raises(ValueError, match="job_profile_mismatch"):
        rank_recruitment_preview(task, [], profile=profile)
    with pytest.raises(ValueError, match="invalid_condition_group"):
        load_condition({"operator": "NOT", "conditions": []})
    dimension = {"name": "dimension", "weight": 1, "condition": {
        "operator": "NOT", "conditions": [asdict(Criterion("gender", "equals", "female", "条件"))],
    }}
    with pytest.raises(ValueError, match="unsupported_sensitive"):
        load_job_profile({"name": "设计师", "scoring": [dimension]})
    assert list(iter_criteria(task.hard_filters[0])) == task.hard_filters


def test_stable_equal_score_order_and_budget_uses_unique_candidates():
    task = parse_recruitment_task("岗位：会计，本科，检查2份简历，联系10人")
    data = [candidate(key, "学历：本科") for key in ("b", "b", "a", "c")]
    output = rank_recruitment_preview(task, data)
    assert output["contact_preview"] == ["a", "b"]
    assert output["summary"]["checked"] == 2
