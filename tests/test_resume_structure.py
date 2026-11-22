from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_schema import Criterion
from boss_agent.resume_structure import extract_resume_observations


def test_natural_resume_extracts_evidence_without_guessing():
    text = ("教育背景\n2024年毕业于某大学，获本科学位，主修会计学。\n"
            "工作经历\n使用 Excel 进行关账，仅了解 Python。\n"
            "项目经历\n交付月结改进方案。\n持有初级会计资格证书。")
    sources = {"dom": text}
    observations = extract_resume_observations(sources)
    assert all(text[item.start:item.end] == item.quote for item in observations)
    assert any(item.field == "achievement" for item in observations)
    assert any(item.field == "certificates" for item in observations)
    resume = extract_resume_evidence(sources)
    for field, value, expected in [
        ("education_level", "bachelor", "PASS"), ("education_mode", "full_time", "UNKNOWN"),
        ("graduate_status", "fresh_graduate", "UNKNOWN"), ("hands_on_tools", "Excel", "PASS"),
        ("hands_on_tools", "Python", "UNKNOWN"),
    ]:
        assert evaluate_condition(Criterion(field, "equals", value, field), resume).status == expected
