from __future__ import annotations

import json

import boss_agent.job_agent as job_agent


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _llm_response(content: dict) -> _Response:
    return _Response({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})


def test_prompt_requires_both_job_match_and_recruiter_authority() -> None:
    prompt = job_agent.build_job_communication_prompt(
        {
            "title": "AI应用开发工程师",
            "company": "示例科技",
            "recruiterName": "王总",
            "recruiterRole": "技术经理",
            "description": "负责企业Agent工作流开发。",
        }
    )

    assert "jobMatched 和 decisionMaker 必须同时为 true" in prompt
    assert "只能依据招聘方身份字段" in prompt
    assert "招聘方身份：技术经理" in prompt


def test_semantic_judgment_accepts_ai_job_contacted_by_manager(monkeypatch) -> None:
    monkeypatch.setenv("BOSS_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        job_agent,
        "urlopen",
        lambda request, timeout: _llm_response(
            {
                "jobMatched": True,
                "decisionMaker": True,
                "jobScore": 93,
                "authorityScore": 96,
                "category": "AI软件开发",
                "reasons": ["职责为AI应用开发", "招聘方为技术经理"],
                "evidence": ["Agent工作流开发", "技术经理"],
                "risks": [],
            }
        ),
    )

    result = job_agent.analyze_job_for_communication(
        {
            "title": "AI应用开发工程师",
            "recruiterName": "王总",
            "recruiterRole": "技术经理",
            "description": "负责企业Agent工作流开发。",
        }
    )

    assert result["matched"] is True
    assert result["decisionMaker"] is True
    assert result["jobScore"] == 93


def test_semantic_judgment_rejects_recruitment_consultant_even_if_model_accepts(monkeypatch) -> None:
    monkeypatch.setenv("BOSS_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        job_agent,
        "urlopen",
        lambda request, timeout: _llm_response(
            {
                "jobMatched": True,
                "decisionMaker": True,
                "jobScore": 95,
                "authorityScore": 90,
                "category": "AI软件开发",
                "reasons": ["模型误认为有决策权"],
                "evidence": ["招聘顾问"],
                "risks": [],
            }
        ),
    )

    result = job_agent.analyze_job_for_communication(
        {
            "title": "AI应用开发工程师",
            "recruiterRole": "招聘顾问",
            "description": "负责企业Agent工作流开发。",
        }
    )

    assert result["matched"] is False
    assert result["decisionMaker"] is False
    assert result["roleEvidence"] is False


def test_semantic_judgment_fails_closed_without_llm_config(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = job_agent.analyze_job_for_communication(
        {"title": "AI开发", "recruiterRole": "CEO", "description": "AI软件开发"}
    )

    assert result["matched"] is False
    assert result["agent"] == "semantic_no_llm_config"


def test_role_evidence_rejects_manager_assistant() -> None:
    assert job_agent.has_decision_maker_role_evidence("总经理助理") is False
    assert job_agent.has_decision_maker_role_evidence("招聘顾问") is False
    assert job_agent.has_decision_maker_role_evidence("招聘经理") is True
    assert job_agent.has_decision_maker_role_evidence("CEO") is True
