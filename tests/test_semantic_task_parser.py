from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from threading import Thread
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from boss_agent.recommend_preview import load_preview_task
from boss_agent.semantic_task_parser import execution_matches, parse_task_with_metadata
from boss_agent.task_parser import parse_recruitment_task
from boss_agent.task_schema import ConditionGroup, iter_criteria
from boss_agent.task_validator import task_blockers


REQUEST = ("招聘计算机大类专业本科生，计算机科学与技术和软件工程优先，"
           "人力资源相关工作经验一年以内，有 WorkBuddy 或 Agent 实操经验者加分。")


def test_local_tool_preference_preserves_comma_and_space_in_source():
    phrase = "有workbuddy， agent的实操经验（优先）"
    raw = f"本科，{phrase}"
    result = parse_task_with_metadata(raw, enabled=False)
    preference, = result.task.soft_preferences
    assert (preference.field, preference.operator, preference.value) == (
        "hands_on_tools", "has_any", ["workbuddy", "agent"]
    )
    source, = next(item for item in result.metadata["criteria_sources"]
                   if item["field"] == "hands_on_tools")["sources"]
    assert raw[source["start"]:source["end"]] == source["source_text"] == phrase


@pytest.mark.parametrize("model", [False, True])
def test_attached_unsupported_condition_preserves_education_and_sources(model):
    education = "有计算机大类专业全日制本科（最好是人工智能，计算机科学与技术）"
    raw = education + "男生、一年以内的人力经验，有workbuddy， agent的实操经验（优先）"
    def confirm(_system, prompt, _model, **_kwargs):
        choices = json.loads(prompt)["choices"]
        return json.dumps({"accepted_ids": [item["id"] for item in choices]})

    result = parse_task_with_metadata(raw, enabled=model, client=confirm, select=model)
    assert result.metadata["mode"] == ("ai" if model else "rule")
    hard = {leaf.field: leaf.value for rule in result.task.hard_filters for leaf in iter_criteria(rule)}
    assert hard["education_level"] == "bachelor"
    assert hard["education_mode"] == "full_time"
    assert hard["major_group"] == "计算机类"
    assert result.task.unsupported_conditions == ["男生"]
    assert "unsupported_conditions" in task_blockers(result.task)
    for item in result.metadata["criteria_sources"]:
        assert item["sources"]
        for source in item["sources"]:
            assert raw[source["start"]:source["end"]] == source["source_text"]


@pytest.fixture(autouse=True)
def configure_mock_model(monkeypatch):
    monkeypatch.setenv("BOSS_TASK_AI_MODEL", "fixture-model")
    monkeypatch.setenv("BOSS_TASK_AI_TIMEOUT_SECONDS", "2")
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def leaf(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


def proposal(raw, identifier, category, condition, excerpt):
    start = raw.index(excerpt)
    return dict(id=identifier, category=category, condition=condition, confidence=0.9,
                sources=[dict(start=start, end=start + len(excerpt), source_text=excerpt)])


def fixed_model(system, prompt, model, *, timeout):
    assert model == "fixture-model" and timeout == 2
    raw = json.loads(prompt)["raw_text"]
    assert raw == REQUEST
    return json.dumps({"conditions": [
        proposal(raw, "education", "hard", {"operator": "AND", "conditions": [
            leaf("education_level", "gte", "bachelor"), leaf("major_group", "equals", "计算机类")
        ]}, "招聘计算机大类专业本科生"),
        proposal(raw, "major", "preference", leaf("major", "one_of", ["计算机科学与技术", "软件工程"]),
                 "计算机科学与技术和软件工程优先"),
        proposal(raw, "hr", "hard", leaf("hr_experience_months", "between", [0, 12]),
                 "人力资源相关工作经验一年以内"),
        proposal(raw, "tools", "preference", {"operator": "OR", "conditions": [
            leaf("hands_on_tools", "has_any", ["WorkBuddy"]), leaf("hands_on_tools", "has_any", ["Agent"])
        ]}, "有 WorkBuddy 或 Agent 实操经验者加分"),
    ]})


def claims(rules):
    return [(item.field, item.operator, item.value) for rule in rules for item in iter_criteria(rule)]


def test_model_long_request_and_grounded_groups():
    result = parse_task_with_metadata(REQUEST, enabled=True, client=fixed_model)
    assert result.legacy_task.ambiguous_fields
    assert result.metadata["mode"] == "ai"
    assert claims(result.task.hard_filters) == [
        ("education_level", "gte", "bachelor"), ("major_group", "equals", "计算机类"),
        ("hr_experience_months", "between", [0, 12]),
    ]
    assert result.task.hard_filters[0].operator == "AND"
    assert result.task.soft_preferences[1].operator == "OR"
    assert result.task.ambiguous_fields == []
    assert not execution_matches(result)
    assert load_preview_task(result.task.to_dict()) == result.task
    for item in result.metadata["criteria_sources"]:
        assert item["origin"] == "ai" and not item["needs_clarification"]
        for quote in item["sources"]:
            assert REQUEST[quote["start"]:quote["end"]] == quote["source_text"]


@pytest.mark.parametrize(("raw", "hard", "soft"), [
    ("招聘计算机类本科生", [("education_level", "gte", "bachelor"), ("major_group", "equals", "计算机类")], []),
    ("计算机类本科，软件工程优先", [("education_level", "gte", "bachelor"), ("major_group", "equals", "计算机类")],
     [("major", "equals", "软件工程")]),
    ("有 HR 经验且不超过一年", [("hr_experience_months", "gt", 0), ("hr_experience_months", "lte", 12)], []),
    ("HR 经验一年以内", [("hr_experience_months", "between", [0, 12])], []),
    ("无需 HR 相关经验，无需 AI Agent 使用经验", [], []),
    ("WorkBuddy 和 Agent 都必须熟练", [("job_related_requirement", "verify", "熟练使用 WorkBuddy"),
                                      ("job_related_requirement", "verify", "熟练使用 Agent")], []),
    ("计算机类专业，人工智能专业优先", [("major_group", "equals", "计算机类")], [("major", "equals", "人工智能")]),
    ("计算机类专业，最好是人工智能，计算机科学与技术",
     [("major_group", "equals", "计算机类")], [("major", "one_of", ["人工智能", "计算机科学与技术"])]),
    ("不接受大专，有招聘经历优先，但非必须", [("education_level", "equals", "associate")],
     [("job_related_preference", "verify", "有招聘经历")]),
])
def test_rule_fallback_preserves_distinctions(raw, hard, soft):
    result = parse_task_with_metadata(raw, enabled=True)
    assert result.metadata["fallback_reason"] == "missing_api_key"
    assert claims(result.task.hard_filters) == hard
    assert claims(result.task.soft_preferences) == soft
    assert not result.task.ambiguous_fields
    if raw.startswith("不接受"):
        assert result.task.hard_filters[0].operator == "NOT"


def test_parentheses_full_width_characters_and_comma_tool_list():
    raw = "招聘计算机类本科（最好是人工智能、软件工程）、有ＷｏｒｋＢｕｄｄｙ，Agent 的实操经验（优先）"
    result = parse_task_with_metadata(raw, enabled=True)
    assert not result.task.ambiguous_fields
    assert isinstance(result.task.soft_preferences[-1], ConditionGroup)
    quoted = result.metadata["criteria_sources"][-1]["sources"][0]
    assert raw[quoted["start"]:quoted["end"]] == quoted["source_text"]
    assert "ＷｏｒｋＢｕｄｄｙ" in quoted["source_text"]


def test_conflicting_duration_and_unscoped_experience_block():
    raw = "必须有三年以上 HR 经验，HR 经验不能超过一年，有相关经验且不超过一年"
    result = parse_task_with_metadata(raw, enabled=True)
    blockers = task_blockers(replace(result.task, job="测试岗位"))
    assert "conflicting_hr_experience_months" in blockers
    assert "unresolved_condition:有相关经验且不超过一年" in blockers
    assert len(result.metadata["criteria_sources"]) == 2


def test_model_can_complete_a_grounded_conjunction():
    raw = "招聘计算机类本科生并且有 HR 经验"
    items = [
        proposal(raw, "education", "hard", {"operator": "AND", "conditions": [
            leaf("education_level", "gte", "bachelor"), leaf("major_group", "equals", "计算机类")
        ]}, "招聘计算机类本科生"),
        proposal(raw, "hr", "hard", leaf("hr_experience_months", "gt", 0), "有 HR 经验"),
    ]
    assert parse_task_with_metadata(raw, enabled=True).task.ambiguous_fields
    result = parse_task_with_metadata(
        raw, enabled=True, client=lambda *args, **kwargs: json.dumps({"conditions": items})
    )
    assert result.metadata["mode"] == "ai"
    assert not result.task.ambiguous_fields
    assert len(result.task.hard_filters) == 2
    partial = parse_task_with_metadata(
        raw, enabled=True, client=lambda *args, **kwargs: json.dumps({"conditions": items[:1]})
    )
    assert partial.metadata["fallback_reason"] == "incomplete_output"
    assert partial.task.ambiguous_fields


@pytest.mark.parametrize(("attack", "reason"), [
    ("degree", "ungrounded_condition"), ("duration", "ungrounded_condition"),
    ("fabricated", "invalid_source"), ("promotion", "ungrounded_condition"),
    ("offset", "invalid_source"), ("field", "ungrounded_condition"),
    ("partial", "incomplete_output"), ("wrong_span", "ungrounded_condition"),
])
def test_model_cannot_change_or_omit_grounded_conditions(attack, reason):
    def bad_model(*args, **kwargs):
        items = json.loads(fixed_model(*args, **kwargs))["conditions"]
        if attack == "degree":
            items[0]["condition"]["conditions"][0]["value"] = "doctorate"
        elif attack == "duration":
            items[2]["condition"]["value"] = [0, 36]
        elif attack == "fabricated":
            items[2]["sources"][0]["source_text"] = "三年人力资源经验"
        elif attack == "promotion":
            items[1]["category"] = "hard"
        elif attack == "offset":
            items[1]["sources"][0]["start"] += 1
        elif attack == "field":
            items[2]["condition"] = leaf("unknown_field", "equals", "value")
        elif attack == "wrong_span":
            items[2]["sources"] = items[0]["sources"]
        else:
            items.pop(2)
        return json.dumps({"conditions": items})

    result = parse_task_with_metadata(REQUEST, enabled=True, client=bad_model)
    assert result.metadata["mode"] == "rule_fallback"
    assert result.metadata["fallback_reason"] == reason
    assert result.task == parse_task_with_metadata(REQUEST, enabled=True).task


@pytest.mark.parametrize(("output", "expected"), [
    (TimeoutError("private"), "timeout"), (URLError("private"), "service_error"),
    ("not JSON", "invalid_json"), ("", "empty_output"), ("[]", "invalid_schema"),
    (RuntimeError("private"), "service_error"),
])
def test_failure_modes_are_safe(output, expected):
    def fail(*args, **kwargs):
        if isinstance(output, Exception):
            raise output
        return output

    result = parse_task_with_metadata("计算机类本科，背景合适", enabled=True, client=fail)
    assert result.metadata["fallback_reason"] == expected
    assert "unresolved_condition:背景合适" in result.task.ambiguous_fields
    assert "private" not in json.dumps(result.metadata)


def test_repeated_source_references_and_legacy_default():
    raw = "HR 经验一年以内，HR 经验一年以内"
    enhanced = parse_task_with_metadata(raw, enabled=True)
    assert len(enhanced.task.hard_filters) == 1
    assert len(enhanced.metadata["criteria_sources"][0]["sources"]) == 2
    legacy = parse_task_with_metadata(REQUEST, enabled=False, client=lambda *a, **k: pytest.fail("unexpected model call"))
    assert legacy.task == parse_recruitment_task(REQUEST)
    assert execution_matches(legacy)


def test_unconfigured_ai_does_not_call_model_and_preserves_unknown_hard_requirement(monkeypatch):
    def unexpected_model_call(*args, **kwargs):
        pytest.fail("unconfigured parsing attempted a model request")

    monkeypatch.setattr("boss_agent.semantic_task_parser._request_json_completion", unexpected_model_call)
    raw = "本科，必须满足已约定的其他门槛"
    result = parse_task_with_metadata(raw, enabled=True)
    assert result.metadata["fallback_reason"] == "missing_api_key"
    assert "unresolved_condition:必须满足已约定的其他门槛" in task_blockers(replace(result.task, job="测试岗位"))


def test_plan_api_keeps_legacy_fields_and_gates_changed_rules(monkeypatch):
    workbench = Path(__file__).resolve().parents[1] / "recruitment-workbench"
    monkeypatch.syspath_prepend(str(workbench))
    spec = spec_from_file_location("semantic_test_workbench", workbench / "server.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    class FixtureWorkspace:
        def overview(self):
            return {"published_jobs": ["测试岗位"]}

    def post(raw):
        body = json.dumps({"job": "测试岗位", "request": raw}).encode()
        url = f"http://127.0.0.1:{server.server_port}/api/plan"
        with build_opener(ProxyHandler({})).open(Request(url, body, {"Content-Type": "application/json"}), timeout=3) as response:
            return json.load(response)

    with module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler) as server:
        server.workbench = FixtureWorkspace()
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            monkeypatch.setenv("BOSS_TASK_AI_ENABLED", "0")
            legacy = post("本科")
            assert {"task", "plan"} <= legacy.keys()
            assert legacy["parsing_metadata"]["mode"] == "rule"
            assert not legacy["plan"]["blockers"]
            monkeypatch.setenv("BOSS_TASK_AI_ENABLED", "1")
            fallback = post("本科，必须满足已约定的其他门槛")
            assert fallback["parsing_metadata"]["fallback_reason"] == "missing_api_key"
            assert "unresolved_condition:必须满足已约定的其他门槛" in fallback["plan"]["blockers"]
            monkeypatch.setattr(module, "parse_task_with_metadata",
                                lambda raw: parse_task_with_metadata(raw, enabled=True, client=fixed_model))
            parsed = post(REQUEST)
            assert parsed["parsing_metadata"]["mode"] == "ai"
            assert parsed["plan"]["blockers"] == ["preview_requires_matching_rules"]
            assert load_preview_task(parsed["task"]).job == "测试岗位"
        finally:
            server.shutdown()
            thread.join()
