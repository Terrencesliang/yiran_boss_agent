import pytest

from boss_agent.major_catalog import CATALOG_VERSION, find_major
from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_schema import Criterion


@pytest.mark.parametrize("name,group", [
    ("通信工程", "电子信息类"),
    ("电子科学与技术", "电子信息类"),
    ("人工智能", "电子信息类"),
    ("护理学", "护理学类"),
    ("国际经济与贸易", "经济与贸易类"),
    ("人力资源管理", "工商管理类"),
])
def test_catalog_classifies_major_with_original_evidence(name, group):
    resume = extract_resume_evidence({"dom": f"教育经历\n示例大学\n{name}\n本科"})
    result = evaluate_condition(Criterion("major_group", "equals", group, group), resume)
    assert result.status == "PASS"
    assert result.evidence[0].quote == name
    assert result.evidence[0].taxonomy_version == CATALOG_VERSION


def test_unknown_major_and_missing_subgroup_are_not_guessed():
    assert find_major("未收录的方向") is None
    assert find_major("具身智能").discipline == "交叉学科"
    assert find_major("具身智能").group is None
    resume = extract_resume_evidence({"ocr": "专业：未收录的方向"})
    assert evaluate_condition(Criterion("major_group", "equals", "计算机类", "计算机类"), resume).status == "UNKNOWN"


def test_alias_applies_to_major_preference_without_rewriting_citation():
    resume = extract_resume_evidence({"dom": "专业：计科"})
    rule = Criterion("major", "equals", "计算机科学与技术", "专业优先")
    result = evaluate_condition(rule, resume)
    assert result.status == "PASS"
    assert result.evidence[0].quote == "专业：计科"


def test_combined_major_and_degree_line():
    resume = extract_resume_evidence({"dom": "教育经历\n示例大学\n通信工程 • 本科"})
    rule = Criterion("major_group", "equals", "电子信息类", "电子信息类")
    result = evaluate_condition(rule, resume)
    assert result.status == "PASS"
    assert result.evidence[0].quote == "通信工程 • 本科"
