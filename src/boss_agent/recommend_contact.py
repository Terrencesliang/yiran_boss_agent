"""Contact an explicit shortlist after rechecking its current resume evidence."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from boss_agent.candidate_adapter import adapt_recommend_candidate
from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.recommend_preview import load_preview_task
from boss_agent.recommendation import prepare_recruitment_preview, rank_recruitment_preview
from boss_agent.resume_model import with_recognized_resume
from boss_agent.task_schema import iter_criteria


def _write_state(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    stream = lock.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise ValueError("contact_run_already_active") from exc
    try:
        yield
    finally:
        stream.close()


def _find_card(client, candidate_id, url_contains, *, context_ok):
    def current_card():
        cards = client.capture_recommend_candidates(max_count=1500, url_contains=url_contains)["candidates"]
        return [card for card in cards if card.get("candidateId") == candidate_id]

    if not context_ok():
        return None
    found = current_card()
    if found:
        return found[0] if len(found) == 1 else None
    client.advance_recommend_viewport(url_contains, to_top=True)
    for step in range(41):
        if not context_ok() or not client.wait_for_recommend_results_stable(url_contains).get("stable"):
            return None
        found = current_card()
        if len(found) == 1:
            return found[0]
        if found or step == 40 or not client.advance_recommend_viewport(url_contains).get("advanced"):
            return None
    return None


def _close_detail(client, url_contains):
    result = client.close_recommend_detail(url_contains)
    return bool(result.get("closed") and result.get("verifiedClosed") is not False
                and not (result.get("after") or {}).get("detailOpen"))


def _deliver(client, candidate_id, url_contains, state_path, state, key):
    row = {"candidate_id": candidate_id, "greeted": False}
    record = {"status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()}
    state["contacts"][key] = record
    _write_state(state_path, state)
    try:
        outcome = client.click_recommend_card_greet(
            candidate_id=candidate_id, url_contains=url_contains, allow_live=True,
        )
    except Exception:
        outcome = {"clicked": None, "reason": "send_unconfirmed"}
    confirmed = (outcome.get("clicked") is True and outcome.get("greeted") is True
                 and outcome.get("candidateId") == candidate_id
                 and client._is_recommend_greeted_status(outcome.get("status")))
    if confirmed:
        record["status"] = "confirmed"
        row.update(greeted=True, reason="confirmed", status=outcome["status"])
    elif outcome.get("clicked") is False:
        already = outcome.get("reason") == "already_contacted"
        record["status"] = "already_contacted" if already else "not_sent"
        row["reason"] = outcome.get("reason") or "not_sent"
    else:
        record["status"] = "unconfirmed"
        row["reason"] = "send_unconfirmed"
    _write_state(state_path, state)
    return row


def _read_state(state_path):
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"version": 1, "contacts": {}}
    if (type(state) is not dict or state.get("version") != 1 or type(state.get("contacts")) is not dict
            or any(type(key) is not str or type(value) is not dict
                   or value.get("status") not in {"pending", "confirmed", "already_contacted", "not_sent", "unconfirmed"}
                   for key, value in state["contacts"].items())):
        raise ValueError("invalid_contact_state")
    return state


def greet_current_candidate(client, candidate_id, job, job_id, url_contains, *, state_path):
    """Contact the exact just-reviewed card without navigating or reloading the listing."""
    row = {"candidate_id": candidate_id, "greeted": False}
    with _state_lock(state_path):
        state = _read_state(state_path)
        key = json.dumps([job_id or job, candidate_id], ensure_ascii=False)
        previous = state["contacts"].get(key, {}).get("status")
        if previous in {"confirmed", "already_contacted"}:
            return {**row, "reason": "already_recorded"}, None
        if previous in {"pending", "unconfirmed"}:
            return {**row, "reason": "prior_send_unconfirmed"}, "prior_send_unconfirmed"
        if client.has_recommend_challenge(url_contains):
            return {**row, "reason": "page_challenge_detected"}, "page_challenge_detected"
        if not _close_detail(client, url_contains):
            return {**row, "reason": "detail_cleanup_unverified"}, "detail_cleanup_unverified"
        if not client.verify_recommend_job_context(job, job_id, url_contains):
            return {**row, "reason": "job_context_changed"}, "job_context_changed"
        current = [card for card in client.capture_recommend_candidates(max_count=1500, url_contains=url_contains)["candidates"]
                   if card.get("candidateId") == candidate_id]
        if len(current) != 1:
            return {**row, "reason": "candidate_not_found"}, None
        row = _deliver(client, candidate_id, url_contains, state_path, state, key)
        return row, "send_unconfirmed" if row["reason"] == "send_unconfirmed" else None


def run_recruitment_contacts(
    client: ChromeDebugClient, report: dict, candidate_ids: list[str], *,
    send: bool = False, state_path: Path, judge=None, resume_model=None,
    url_contains: str = "/web/chat/recommend",
) -> dict:
    if type(send) is not bool:
        raise ValueError("send_must_be_boolean")
    task = load_preview_task(report)
    prepare_recruitment_preview(task)
    if task.action != "greet" or not candidate_ids:
        raise ValueError("explicit_contact_shortlist_required")
    if (any(type(value) is not str or not value.strip() or value != value.strip() for value in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
            or len(candidate_ids) > (task.target_success_count or 0)):
        raise ValueError("invalid_contact_shortlist")
    qualified = {row["candidate_id"] for row in report.get("ranked_candidates", [])
                 if row.get("hard_filter_status") == "PASS" and row.get("capture_status", "complete") == "complete"}
    allowed = set(report.get("contact_preview", [])) & qualified
    if not set(candidate_ids) <= allowed:
        raise ValueError("shortlist_not_qualified_in_preview")
    if send and client.mock_dir is None and report.get("data_source") != "boss_browser_preview":
        raise ValueError("live_contact_requires_browser_preview")
    if send and any(row.get("candidate_evidence", {}).get("resume_model_status")
                    for row in report.get("ranked_candidates", []) if row["candidate_id"] in candidate_ids) and resume_model is None:
        raise ValueError("ai_recheck_required")
    requirements = [leaf for condition in task.hard_filters + task.soft_preferences for leaf in iter_criteria(condition)]
    result = {"job": task.job, "candidate_ids": candidate_ids, "dry_run": not send,
              "actual_successful_contacts": 0, "results": [], "stop_reason": None}
    if not send:
        return result
    with _state_lock(state_path):
        state = _read_state(state_path)
        try:
            client.pin_recommend_page(url_contains)
            client.activate_recommend_page(url_contains)
            job_id = report.get("job_id")
            def context_ok():
                return client.verify_recommend_job_context(task.job, job_id, url_contains)

            if not context_ok():
                raise ValueError("job_context_changed")
            for candidate_id in candidate_ids:
                key = json.dumps([job_id or task.job, candidate_id], ensure_ascii=False)
                previous = state["contacts"].get(key, {})
                row = {"candidate_id": candidate_id, "greeted": False}
                if previous.get("status") in {"confirmed", "already_contacted"}:
                    result["results"].append({**row, "reason": "already_recorded"})
                    continue
                if previous.get("status") in {"pending", "unconfirmed"}:
                    result["results"].append({**row, "reason": "prior_send_unconfirmed"})
                    result["stop_reason"] = "prior_send_unconfirmed"
                    break
                if client.has_recommend_challenge(url_contains):
                    result["stop_reason"] = "page_challenge_detected"
                    break
                if not _close_detail(client, url_contains):
                    result["stop_reason"] = "prior_detail_not_closed"
                    break
                card = _find_card(client, candidate_id, url_contains, context_ok=context_ok)
                if card is None:
                    if not context_ok():
                        result["stop_reason"] = "job_context_changed"
                        break
                    result["results"].append({**row, "reason": "candidate_not_found"})
                    continue
                opened = client.open_recommend_candidate(candidate_id, url_contains=url_contains, strict_identity=True)
                detail = client.capture_candidate_resume(url_contains, expected_candidate_id=candidate_id)
                try:
                    candidate = adapt_recommend_candidate(card, opened, detail, job_id=job_id)
                    ranking_input = candidate.to_ranking_input()
                    if resume_model is not None:
                        resume, findings, _ = with_recognized_resume(candidate.structured_evidence, candidate_id,
                                                                    resume_model, requirements)
                        ranking_input.update(structured_evidence=resume, model_findings=findings)
                    current = rank_recruitment_preview(task, [ranking_input], judge=judge)
                except ValueError:
                    current = {"ranked_candidates": []}
                if candidate_id not in current.get("contact_preview", []):
                    result["results"].append({**row, "reason": "current_evidence_not_qualified"})
                    continue
                if not _close_detail(client, url_contains):
                    result["stop_reason"] = "detail_cleanup_unverified"
                    break
                if not client.verify_recommend_job_context(task.job, job_id, url_contains):
                    result["stop_reason"] = "job_context_changed"
                    break
                row = _deliver(client, candidate_id, url_contains, state_path, state, key)
                result["actual_successful_contacts"] += int(row["greeted"])
                if row["reason"] == "send_unconfirmed":
                    result["stop_reason"] = "send_unconfirmed"
                result["results"].append(row)
                if result["stop_reason"]:
                    break
            if not _close_detail(client, url_contains) and result["stop_reason"] is None:
                result["stop_reason"] = "detail_cleanup_unverified"
        finally:
            client.release_recommend_page()
    return result
