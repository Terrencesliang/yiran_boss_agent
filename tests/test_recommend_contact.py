import json
from pathlib import Path
from dataclasses import replace
from functools import partial
from unittest.mock import Mock

import pytest

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.cli import main
from boss_agent.recommend_contact import _state_lock, run_recruitment_contacts
from boss_agent.recommend_preview import run_recommend_preview
import boss_agent.recommend_contact as contact_module
from boss_agent.task_parser import parse_recruitment_task
from test_browser_recruitment_preview import detail, write_mock_data


def setup_preview(tmp_path):
    fixture = tmp_path / "fixtures"
    cards = [{"candidateId": key, "candidateName": key} for key in ("one", "two")]
    write_mock_data(fixture, cards, {
        key: detail(key, f"样本{key}\n学历：全日制本科") for key in ("one", "two")
    })
    client = ChromeDebugClient("http://127.0.0.1:9222", fixture)
    task = parse_recruitment_task("岗位：开发助理，全日制本科，联系2人")
    report = run_recommend_preview(client, task)
    assert report["contact_preview"] == ["one", "two"]
    return client, report


def test_default_contact_preview_never_touches_browser(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    monkeypatch.setattr(client, "pin_recommend_page", Mock(side_effect=AssertionError("browser touched")))
    result = run_recruitment_contacts(client, report, ["one"], state_path=tmp_path / "state.json")
    assert result["dry_run"] and result["actual_successful_contacts"] == 0
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("ids", [["unknown"], ["one", "one"], ["one", "two", "three"], []])
def test_invalid_shortlist_precedes_browser_access(tmp_path, monkeypatch, ids):
    client, report = setup_preview(tmp_path)
    monkeypatch.setattr(client, "pin_recommend_page", Mock(side_effect=AssertionError("browser touched")))
    with pytest.raises(ValueError):
        run_recruitment_contacts(client, report, ids, send=True, state_path=tmp_path / "state.json")


def test_mock_shortlist_cli_sends_once_and_resumes_without_duplicates(tmp_path, capsys):
    client, report = setup_preview(tmp_path)
    report_path = tmp_path / "preview.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    args = ["contact-recruitment", "--preview-report", str(report_path),
            "--candidate-id", "one", "--candidate-id", "two", "--mock-dir", str(client.mock_dir),
            "--state-file", str(tmp_path / "state.json"), "--send"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["actual_successful_contacts"] == 2
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["actual_successful_contacts"] == 0
    assert {row["reason"] for row in second["results"]} == {"already_recorded"}


def test_current_unknown_evidence_prevents_contact(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    records_path = client.mock_dir / "candidate_resumes.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["one"]["fullDomText"] = "学历：本科"
    records_path.write_text(json.dumps(records), encoding="utf-8")
    click = Mock(side_effect=AssertionError("should not send"))
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")
    assert result["results"][0]["reason"] == "current_evidence_not_qualified"
    click.assert_not_called()


@pytest.mark.parametrize("target", [1, 2])
def test_incremental_send_happens_before_opening_next_resume(tmp_path, monkeypatch, target):
    client, report = setup_preview(tmp_path)
    task = replace(parse_recruitment_task("岗位：开发助理，全日制本科，联系2人"), target_success_count=target)
    events = []
    capture = client.capture_candidate_resume
    click = client.click_recommend_card_greet

    def capture_logged(*args, expected_candidate_id, **kwargs):
        events.append(("capture", expected_candidate_id))
        return capture(*args, expected_candidate_id=expected_candidate_id, **kwargs)

    def click_logged(*, candidate_id, **kwargs):
        events.append(("send", candidate_id))
        return click(candidate_id=candidate_id, **kwargs)

    monkeypatch.setattr(client, "capture_candidate_resume", capture_logged)
    monkeypatch.setattr(client, "click_recommend_card_greet", click_logged)
    result = run_recommend_preview(client, task, greet_current=partial(
        contact_module.greet_current_candidate, state_path=tmp_path / "state.json"))
    assert events == [("capture","one"),("send","one"),("capture","two"),("send","two")][:target*2]
    assert result["summary"]["actual_successful_contacts"] == target
    assert result["summary"]["checked"] == target
    assert result["stop_reason"] == "contact_target_reached"


def test_contact_checks_current_view_before_resetting_scroll(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    move = Mock(side_effect=AssertionError("already visible, do not reset list"))
    monkeypatch.setattr(client, "advance_recommend_viewport", move)
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")
    assert result["actual_successful_contacts"] == 1
    move.assert_not_called()


@pytest.mark.parametrize("card_returns", [True, False])
def test_contact_waits_for_the_requested_card_after_scrolling(tmp_path, monkeypatch, card_returns):
    client, report = setup_preview(tmp_path)
    stable = Mock(return_value={"stable": True})
    phases = iter([{"candidates":[]}, {"candidates":[]},
                   {"candidates":[{"candidateId":"one", "candidateName":"one"}] if card_returns else
                                 [{"candidateId":"two", "candidateName":"two"}]}])
    monkeypatch.setattr(client, "capture_recommend_candidates", lambda **kwargs: next(phases))
    monkeypatch.setattr(client, "advance_recommend_viewport",
                        Mock(side_effect=[{"advanced":True}, {"advanced":True}, {"advanced":False}]))
    monkeypatch.setattr(client, "wait_for_recommend_results_stable", stable)
    click = Mock(return_value={"clicked":True, "greeted":True, "candidateId":"one", "status":"已打招呼"})
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")
    assert stable.call_count == 2
    assert result["actual_successful_contacts"] == int(card_returns)
    assert result["results"][0]["reason"] == ("confirmed" if card_returns else "candidate_not_found")
    if card_returns:
        click.assert_called_once_with(candidate_id="one", url_contains="/web/chat/recommend", allow_live=True)
    else:
        click.assert_not_called()


def test_ai_preview_has_no_rule_fallback_when_contact_is_rechecked(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    report["ranked_candidates"][0]["candidate_evidence"] = {"resume_model_status": "completed"}
    click = Mock(side_effect=AssertionError("no model evidence, do not send"))
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    with pytest.raises(ValueError, match="ai_recheck_required"):
        run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json",
                                      resume_model=lambda payload: '{"education":[],"work":[],"decisions":[]}')
    assert result["results"][0]["reason"] == "current_evidence_not_qualified"
    click.assert_not_called()


@pytest.mark.parametrize("outcome", [
    TimeoutError("connection lost after dispatch"),
    {"clicked": True, "greeted": False},
    {"clicked": True, "greeted": True, "candidateId": "other", "status": "已打招呼"},
    {"clicked": True, "greeted": True, "candidateId": "one", "status": "未知"},
])
def test_unconfirmed_send_stops_batch_and_blocks_retries(tmp_path, monkeypatch, outcome):
    client, report = setup_preview(tmp_path)
    click = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    state_path = tmp_path / "state.json"
    first = run_recruitment_contacts(client, report, ["one", "two"], send=True, state_path=state_path)
    assert first["actual_successful_contacts"] == 0 and first["stop_reason"] == "send_unconfirmed"
    click.assert_called_once_with(candidate_id="one", url_contains="/web/chat/recommend", allow_live=True)
    second = run_recruitment_contacts(client, report, ["one", "two"], send=True, state_path=state_path)
    assert second["stop_reason"] == "prior_send_unconfirmed"
    assert click.call_count == 1


def test_active_or_abandoned_pending_record_is_never_resent(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    state_path = tmp_path / "state.json"
    key = json.dumps([report["job"], "one"], ensure_ascii=False)
    state_path.write_text(json.dumps({"version": 1, "contacts": {key: {"status": "pending"}}}), encoding="utf-8")
    monkeypatch.setattr(client, "click_recommend_card_greet", Mock(side_effect=AssertionError("duplicate send")))
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=state_path)
    assert result["stop_reason"] == "prior_send_unconfirmed"


def test_os_lock_excludes_concurrent_runs_and_is_reusable(tmp_path):
    state_path = tmp_path / "state.json"
    with _state_lock(state_path):
        with pytest.raises(ValueError, match="contact_run_already_active"):
            with _state_lock(state_path):
                pytest.fail("second run entered the lock")
    with _state_lock(state_path):
        pass


def test_live_contact_rejects_simulated_report_before_browser(tmp_path, monkeypatch):
    _, report = setup_preview(tmp_path)
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "pin_recommend_page", Mock(side_effect=AssertionError("browser touched")))
    with pytest.raises(ValueError, match="live_contact_requires_browser_preview"):
        run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")


def test_post_send_state_write_failure_retains_pending_record(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    state_path = tmp_path / "state.json"
    original_write = contact_module._write_state
    writes = 0

    def fail_second_write(path, state):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated disk failure")
        original_write(path, state)

    click = Mock(wraps=client.click_recommend_card_greet)
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    monkeypatch.setattr(contact_module, "_write_state", fail_second_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        run_recruitment_contacts(client, report, ["one"], send=True, state_path=state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert next(iter(state["contacts"].values()))["status"] == "pending"
    result = run_recruitment_contacts(client, report, ["one"], send=True, state_path=state_path)
    assert result["stop_reason"] == "prior_send_unconfirmed"
    assert click.call_count == 1


def test_changed_job_stops_before_send(tmp_path, monkeypatch):
    client, report = setup_preview(tmp_path)
    monkeypatch.setattr(client, "verify_recommend_job_context", Mock(return_value=False))
    click = Mock(side_effect=AssertionError("wrong job"))
    monkeypatch.setattr(client, "click_recommend_card_greet", click)
    with pytest.raises(ValueError, match="job_context_changed"):
        run_recruitment_contacts(client, report, ["one"], send=True, state_path=tmp_path / "state.json")
    click.assert_not_called()


@pytest.mark.parametrize("status", ["已打招呼", "继续沟通"])
def test_live_sender_requires_same_id_and_confirmed_status(monkeypatch, status):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"webSocketDebuggerUrl": "mock"}])
    monkeypatch.setattr(client, "has_recommend_challenge", lambda _: False)
    evaluate = Mock(side_effect=[
        {"candidateId": "one", "clicked": True},
        {"candidateId": "one", "status": status},
    ])
    monkeypatch.setattr(client, "_evaluate_json", evaluate)
    result = client.click_recommend_card_greet("one", allow_live=True)
    assert result["greeted"] and result["candidateId"] == "one" and result["status"] == status
    assert evaluate.call_count == 2
