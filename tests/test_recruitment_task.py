import ast
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from boss_agent.chrome_debug import ChromeDebugClient, build_click_recommend_card_greet_expression
from boss_agent.cli import _click_recommend_card_greet_once, _screen_recommend_detail, main
from boss_agent.contact_policy import LIVE_CONTACT_DISABLED
from boss_agent.task_parser import parse_recruitment_task
from boss_agent.task_planner import plan_recruitment_task
from boss_agent.task_validator import hard_filters_pass, task_blockers, validate_approval
from boss_agent.task_schema import ConditionGroup


REQUEST = (
    "在推荐牛人中筛选计算机大类专业全日制本科，最好是人工智能、计算机科学与技术，"
    "一年以内人力资源经验，有 WorkBuddy、Agent 实操经验优先，25届26届应届生，向符合条件的10人打招呼。"
)


def values(rules):
    return {rule.field: rule.value for rule in rules if not isinstance(rule, ConditionGroup)}


def test_example_compiles_hard_soft_and_contact_goal():
    task = parse_recruitment_task(REQUEST)
    assert values(task.hard_filters) == {
        "education_mode": "full_time", "education_level": "bachelor",
        "hr_experience_months": [0, 12], "graduation_year": [2025, 2026],
        "graduate_status": "fresh_graduate", "major_group": "计算机类",
    }
    assert values(task.soft_preferences) == {
        "major": ["人工智能", "计算机科学与技术"], "hands_on_tools": ["WorkBuddy", "Agent"],
    }
    assert task.target_success_count == 10
    assert task.max_checks is None
    assert task.dry_run is True and task.approval_required is True
    assert task_blockers(task) == ["job_required"]


def test_clause_order_does_not_change_inferred_requirements():
    task = parse_recruitment_task("岗位：电商人事AI助理，" + REQUEST)
    reversed_task = parse_recruitment_task("。".join(reversed(REQUEST.rstrip("。").split("，"))) + "，职位：电商人事AI助理")
    assert values(reversed_task.hard_filters) == values(task.hard_filters)
    assert values(reversed_task.soft_preferences) == values(task.soft_preferences)
    assert reversed_task.action == "greet"
    assert reversed_task.target_success_count == 10
    assert task_blockers(reversed_task) == []


def test_parenthesized_preferences_keep_education_and_tool_list_intact():
    task = parse_recruitment_task(
        "计算机大类专业本科（最好是人工智能，计算机科学与技术）、"
        "一年以内的人力经验，有workbuddy，agent的实操经验（优先）"
    )
    assert values(task.hard_filters) == {
        "education_level": "bachelor", "hr_experience_months": [0, 12], "major_group": "计算机类",
    }
    assert values(task.soft_preferences) == {
        "major": ["人工智能", "计算机科学与技术"],
        "hands_on_tools": ["workbuddy", "agent"],
    }
    assert task.ambiguous_fields == []


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("检查10份简历", (10, None)),
        ("查看十份简历，先不联系", (10, None)),
        ("向符合条件的10人打招呼", (None, 10)),
        ("给十人打招呼，最多遍历30份简历", (30, 10)),
    ],
)
def test_review_limit_and_success_target_are_distinct(extra, expected):
    task = parse_recruitment_task("职位：运营助理，本科，" + extra)
    assert (task.max_checks, task.target_success_count) == expected
    assert task_blockers(task) == []


@pytest.mark.parametrize(
    ("text", "blocker"),
    [
        ("本科，打招呼", "target_success_count_required"),
        ("在推荐牛人中筛选", "criteria_required"),
        ("岗位：运营，职位：设计，本科", "conflicting_job"),
        ("岗位：运营，本科，硕士", "conflicting_education_level"),
        ("岗位：运营，本科，检查5份，检查6份", "conflicting_max_checks"),
        ("岗位：运营，本科，检查5份，向6人打招呼", "target_success_count_exceeds_max_checks"),
        ("岗位：运营，本科，打招呼10人，不要打招呼", "conflicting_action"),
    ],
)
def test_incomplete_or_conflicting_tasks_do_not_become_ready(text, blocker):
    plan = plan_recruitment_task(parse_recruitment_task(text))
    assert plan["status"] == "needs_clarification"
    assert blocker in plan["blockers"]


def test_sensitive_requirements_cannot_filter_or_rank():
    task = parse_recruitment_task("职位：运营助理，本科，只要女，女生优先，年龄25岁")
    assert values(task.hard_filters) == {"education_level": "bachelor"}
    assert task.soft_preferences == []
    assert len(task.unsupported_conditions) == 3
    assert "unsupported_conditions" in task_blockers(task)


@pytest.mark.parametrize("raw", ["not json", "[]", '{"clauses":[1]}', '{"clauses":["本科"]}', '{"clauses":[],"approved":true}'])
def test_malformed_or_lossy_model_output_is_rejected(raw):
    with pytest.raises(ValueError, match="invalid_model_output"):
        parse_recruitment_task(REQUEST, model_output=raw)


def test_valid_model_shape_preserves_source():
    raw = json.dumps({"clauses": [REQUEST[:10], REQUEST[10:]]})
    assert parse_recruitment_task(REQUEST, model_output=raw) == parse_recruitment_task(REQUEST)


def test_degree_does_not_prove_education_mode_and_unknown_cannot_pass():
    task = parse_recruitment_task("岗位：人事助理，全日制本科")
    assert [rule.field for rule in task.hard_filters] == ["education_mode", "education_level"]
    assert not hard_filters_pass(task, ["unknown", "supported"])
    assert not hard_filters_pass(task, ["supported"])
    assert hard_filters_pass(task, ["supported", "supported"])
    bachelor = parse_recruitment_task("岗位：人事助理，本科")
    assert "education_mode" not in values(bachelor.hard_filters)


def test_approval_requires_exact_task_shortlist_and_count():
    task = parse_recruitment_task("岗位：运营助理，本科，给2人打招呼")
    ids = ["geek-001", "geek-002"]
    approval = {"approved": True, "task": task.to_dict(), "candidate_ids": ids, "count": 2}
    assert validate_approval(task, ids, approval)
    assert not validate_approval(task, ids, None)
    assert not validate_approval(task, ids, {**approval, "approved": "yes"})
    assert not validate_approval(task, ids, {**approval, "count": 1})
    assert not validate_approval(task, ids[::-1], approval)
    assert not validate_approval(replace(task, job="其他岗位"), ids, approval)
    assert not validate_approval(task, ["geek-001"] * 2, approval)
    assert not validate_approval(task, ["", "geek-001"], approval)


def test_plan_cli_has_approval_before_contact(capsys, monkeypatch):
    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", Mock(side_effect=AssertionError("no browser")))
    assert main(["plan-recruitment", "--request", "岗位：运营助理，本科，给2人打招呼"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plan"]["mode"] == "plan_only"
    assert result["plan"]["status"] == "planned"
    assert [step["id"] for step in result["plan"]["steps"]] == [
        "confirm_job", "open_recommend", "apply_page_filters", "capture_candidates",
        "verify_hard_filters", "evaluate_preferences", "build_candidate_pool", "rank_candidates",
        "wait_for_approval", "contact", "verify_contact", "report",
    ]


def test_legacy_cli_first_contact_is_blocked_or_preview(monkeypatch, capsys):
    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", Mock(side_effect=AssertionError("no browser")))
    assert main(["action", "greet-recommendations", "--name", "候选人"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == LIVE_CONTACT_DISABLED
    assert main(["action", "process-recommendations", "--job-title", "运营助理", "--send"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == LIVE_CONTACT_DISABLED


def test_live_detail_legacy_command_defaults_to_preview(monkeypatch, tmp_path):
    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", Mock())
    monkeypatch.setattr("boss_agent.cli.load_local_llm_env", Mock())
    screen = Mock(return_value={})
    monkeypatch.setattr("boss_agent.cli._screen_recommend_detail", screen)
    config = tmp_path / "task.json"
    config.write_text('{"dryRun": false}', encoding="utf-8")
    assert main(["action", "screen-recommend-detail", "--criteria", "本科", "--config", str(config)]) == 0
    assert screen.call_args.kwargs["dry_run"] is True


def test_cli_rejects_sensitive_legacy_config_before_browser(monkeypatch):
    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", Mock(side_effect=AssertionError("no browser")))
    monkeypatch.setattr("boss_agent.cli.load_local_llm_env", Mock())
    with pytest.raises(SystemExit, match="2"):
        main(["action", "screen-recommend-detail", "--criteria", "本科", "--filter", "性别=女"])


def test_low_level_live_send_paths_cannot_connect(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", Mock(side_effect=AssertionError("no browser")))
    assert client.click_recommend_card_greet(card_index=4)["reason"] == "candidate_id_required"
    assert client.click_recommend_card_greet(candidate_id="geek-001")["reason"] == LIVE_CONTACT_DISABLED
    assert client.click_recommend_detail_greet()["reason"] == LIVE_CONTACT_DISABLED
    assert client.greet_recommend_candidates_by_name(["候选人"])["reason"] == LIVE_CONTACT_DISABLED
    assert client.greet_candidate("您好", send=True)["reason"] == LIVE_CONTACT_DISABLED
    assert client.greet_candidate("您好") == {"prepared": True, "sent": False, "body": "您好", "dryRun": True}


@pytest.mark.parametrize("label", ["打招呼", "页面加载中", "未知", "", "未联系", "已联系失败"])
def test_unrecognized_status_never_counts_as_success(label):
    assert not ChromeDebugClient._is_recommend_greeted_status(label)


@pytest.mark.parametrize("outcome", [
    {"clicked": True, "greeted": False},
    {"clicked": True, "greeted": True, "candidateId": "other", "status": "已打招呼"},
    {"clicked": True, "greeted": True, "candidateId": "geek-001", "status": "操作失败"},
    TimeoutError("connection lost"),
])
def test_unknown_send_is_recorded_once_without_fallback(outcome):
    client = Mock()
    if isinstance(outcome, Exception):
        client.click_recommend_card_greet.side_effect = outcome
    else:
        client.click_recommend_card_greet.return_value = outcome
    result = _click_recommend_card_greet_once(client, {"candidateId": "geek-001", "cardIndex": 4}, "/recommend")
    assert result["reason"] == "send_unconfirmed"
    assert not result["greeted"]
    client.click_recommend_card_greet.assert_called_once_with(candidate_id="geek-001", url_contains="/recommend")
    client.click_recommend_detail_greet.assert_not_called()


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is needed to evaluate the browser expression offline")
def test_browser_expression_clicks_and_verifies_only_the_unique_stable_id():
    click = build_click_recommend_card_greet_expression(json.dumps("b"), card_index=0)
    verify = build_click_recommend_card_greet_expression(json.dumps("b"), card_index=0, verify_only=True)
    harness = """
    const makeCard = (id) => {
      const button = {innerText: '打招呼', getAttribute: () => null,
        click() { this.innerText = '已打招呼'; this.count = (this.count || 0) + 1; }};
      const card = {getAttribute: (key) => key === 'data-geekid' ? id : null,
        innerText: 'candidate information longer than 20 characters',
        matches: () => true, querySelector: () => null, querySelectorAll: () => [button]};
      for (const el of [card, button]) {
        el.getBoundingClientRect = () => ({width: 10, height: 10});
        el.ownerDocument = {defaultView: {getComputedStyle: () => ({})}};
      }
      return [card, button];
    };
    const [a, aButton] = makeCard('a');
    const [b, bButton] = makeCard('b');
    let cards = [a, b];
    globalThis.document = {querySelector: () => null, querySelectorAll: () => cards};
    """
    program = harness + f"""
    const click = {click};
    cards = [b, a];
    const verified = {verify};
    cards = [a];
    const missing = {click};
    cards = [a, b, b];
    const duplicate = {click};
    console.log(JSON.stringify({{click, verified, missing, duplicate, aCount:aButton.count || 0, bCount:bButton.count}}));
    """
    result = subprocess.run(["node"], input=program, encoding="utf-8", capture_output=True, check=True)
    data = json.loads(result.stdout)
    click_result, verified_result, missing_result, duplicate_result = (
        json.loads(data[key]) for key in ("click", "verified", "missing", "duplicate")
    )
    assert click_result["candidateId"] == verified_result["candidateId"] == "b"
    assert verified_result["status"] == "已打招呼"
    assert data["aCount"] == 0 and data["bCount"] == 1
    assert not missing_result["clicked"] and not duplicate_result["clicked"]


def test_batch_stops_and_reports_unconfirmed_send(tmp_path, monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222", mock_dir=Path(__file__).parent / "fixtures")
    monkeypatch.setattr("boss_agent.cli.analyze_resume_against_criteria", Mock(return_value={
        "meetsCriteria": True, "confidenceLevel": "high",
    }))
    click = Mock(return_value={"clicked": True, "greeted": False, "candidateId": "geek-001"})
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    report = tmp_path / "preview.csv"
    result = _screen_recommend_detail(
        client, "/web/chat/recommend", "", "", {}, "本科", None,
        max_greetings=2, max_checks=2, post_filter_wait_seconds=0,
        report_path=report, dry_run=False,
    )
    assert result["checkedCount"] == 1
    assert result["greetedCount"] == 0
    assert result["stopReason"] == "send_unconfirmed"
    assert result["processed"][0]["reason"] == "send_unconfirmed"
    assert "send_unconfirmed" in report.read_text(encoding="utf-8-sig")
    click.assert_called_once()


def test_legacy_script_entry_points_abort_before_browser_actions():
    root = Path(__file__).resolve().parents[1]
    task_script = ast.parse((root / "tasks/run_ai_hr_rescreen.py").read_text(encoding="utf-8"))
    first_runtime_statement = next(node for node in task_script.body
                                   if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Expr)))
    assert isinstance(first_runtime_statement, ast.Raise)
    scripts = root.parent / "boss直聘-推荐牛人" / "scripts"
    if not scripts.is_dir():
        return  # The Python package can also be checked out independently.
    for script in scripts.glob("boss_*.py"):
        module = ast.parse(script.read_text(encoding="utf-8"))
        entry = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        assert isinstance(entry.body[0], ast.Raise), script.name
