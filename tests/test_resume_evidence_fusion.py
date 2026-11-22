import json

from boss_agent.resume_structure import split_resume_sources
from boss_agent.rule_engine import evaluate_condition, extract_resume_evidence
from boss_agent.task_schema import Criterion


def test_chunks_round_trip_and_late_evidence_is_locatable():
    quote = "独立交付可配置财务工作流"
    text = "工作经历\n" + "详细记录\n" * 1850 + "项目经历\n" + quote
    sources = {"dom": text, "ocr": "项目经历\n补充内容"}
    blocks = split_resume_sources(sources)
    for source, content in sources.items():
        assert "".join(block.text for block in blocks if block.source == source) == content
    calls = []

    def judge(_, batch):
        calls.append(batch)
        for key, value in batch.items():
            if quote in value:
                return json.dumps({"status": "PASS", "evidence": [{"source": key, "quote": quote}]})
        return '{"status":"UNKNOWN","evidence":[]}'

    rule = Criterion("project_outcomes", "verify", quote, "项目成果")
    finding = evaluate_condition(rule, extract_resume_evidence(sources), judge)
    assert len(calls) > 1 and all(sum(map(len, batch.values())) <= 8000 for batch in calls)
    assert finding.status == "PASS"
    evidence = finding.evidence[0]
    assert evidence.start > 6000 and text[evidence.start:evidence.end] == quote


def test_disagreeing_chunk_judgments_are_unknown():
    sources = {"dom": "x" * 8100 + "\n已经交付可用方案", "ocr": "否认已交付方案"}

    def judge(_, batch):
        for key, value in batch.items():
            for quote, status in (("已经交付可用方案", "PASS"), ("否认已交付方案", "FAIL")):
                if quote in value:
                    return json.dumps({"status": status, "evidence": [{"source": key, "quote": quote}]})
        return '{"status":"UNKNOWN","evidence":[]}'

    result = evaluate_condition(
        Criterion("project_outcomes", "verify", "交付方案", "岗位要求"), extract_resume_evidence(sources), judge,
    )
    assert result.status == "UNKNOWN" and {item.source for item in result.evidence} == {"dom", "ocr"}


def test_work_dates_conflict_between_sources():
    sources = {"dom": "工作经历\n2024-01 至 2024-06 岗位：招聘专员",
               "ocr": "工作经历\n2024-01 至 2024-10 岗位：招聘专员"}
    result = evaluate_condition(
        Criterion("experience_months:role:招聘", "gte", 8, "招聘经验"), extract_resume_evidence(sources),
    )
    assert result.status == "UNKNOWN" and "冲突" in result.reason
    assert {item.source for item in result.evidence} == {"ocr", "dom"}
