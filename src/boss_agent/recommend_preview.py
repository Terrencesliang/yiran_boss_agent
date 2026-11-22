"""Run a structured recommendation preview via the existing Chrome client."""

from dataclasses import dataclass, field, fields, replace
from datetime import date
from hashlib import sha256
from uuid import uuid4
import json
import time

from boss_agent.candidate_adapter import adapt_recommend_candidate, stable_candidate_id
from boss_agent.candidate_evidence import VERSION as EVIDENCE_VERSION, evidence_report
from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.job_profile import JobProfile, load_condition
from boss_agent.recommendation import _scoring_dimensions, prepare_recruitment_preview, rank_recruitment_preview
from boss_agent.rule_engine import SemanticJudge
from boss_agent.resume_model import with_recognized_resume
from boss_agent.task_parser import DEGREES
from boss_agent.task_planner import plan_recruitment_task
from boss_agent.task_schema import ConditionGroup, Criterion, RecruitmentTask, iter_criteria, validate_boss_page_filters
from boss_agent.task_validator import SENSITIVE
from boss_agent.shadow_rule_engine import LEGACY_VERSION, VERSION as SHADOW_VERSION, evaluate_shadow


def load_preview_task(payload: object) -> RecruitmentTask:
    if type(payload) is not dict:
        raise ValueError("invalid_recruitment_task")
    data = dict(payload.get("task", payload)) if type(payload.get("task", payload)) is dict else {}
    required = {"job", "hard_filters", "soft_preferences", "max_checks", "target_success_count", "action"}
    if not required <= data.keys() or data.keys() - {field.name for field in fields(RecruitmentTask)}:
        raise ValueError("invalid_recruitment_task")
    for field in ("hard_filters", "soft_preferences"):
        if type(data[field]) is not list:
            raise ValueError("invalid_conditions")
        data[field] = [load_condition(item) for item in data[field]]
    for field in ("ambiguous_fields", "unsupported_conditions"):
        value = data.get(field, [])
        if type(value) is not list or any(type(item) is not str for item in value):
            raise ValueError(f"invalid_{field}")
    if data["job"] is not None and (type(data["job"]) is not str or not data["job"].strip()):
        raise ValueError("invalid_job")
    if data["action"] not in ("screen", "greet"):
        raise ValueError("invalid_action")
    for field in ("max_checks", "target_success_count"):
        value = data[field]
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"invalid_{field}")
    for field in ("approval_required", "dry_run", "contact_limit_only"):
        if field in data and type(data[field]) is not bool:
            raise ValueError(f"invalid_{field}")
    if data.get("contact_limit_only") and data["action"] != "greet":
        raise ValueError("contact_limit_requires_greet")
    if data.get("platform", "boss_zhipin") != "boss_zhipin" or data.get("entry_page", "recommend") != "recommend":
        raise ValueError("unsupported_recruitment_platform_or_page")
    validate_boss_page_filters(data.get("boss_page_filters", {}))
    return RecruitmentTask(**{**data, "approval_required": True, "dry_run": True})


def _page_filters(task: RecruitmentTask) -> dict[str, str]:
    """Only translate mandatory, exact degrees the existing UI can represent."""
    pending = list(task.hard_filters)
    degrees = set()
    labels = {value: label for label, value in DEGREES.items() if label in {"大专", "本科", "硕士", "博士"}}
    while pending:
        condition = pending.pop()
        if isinstance(condition, ConditionGroup) and condition.operator == "AND":
            pending.extend(condition.conditions)
        if isinstance(condition, Criterion) and condition.field == "education_level" and condition.operator == "equals":
            degree = labels.get(condition.value) if isinstance(condition.value, str) else None
            if degree:
                degrees.add(degree)
    filters = dict(validate_boss_page_filters(task.boss_page_filters))
    if len(degrees) == 1:
        filters["学历"] = next(iter(degrees))
    return filters


def _detail_closed(client: ChromeDebugClient, url_contains: str) -> bool:
    result = client.close_recommend_detail(url_contains)
    return bool(result.get("closed") and result.get("verifiedClosed") is not False
                and not (result.get("after") or {}).get("detailOpen"))


@dataclass
class CaptureBatch:
    greetings: list[dict] = field(default_factory=list)
    observed: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    previous_fingerprints: dict[str, str] = field(default_factory=dict)
    duplicates: int = 0
    scrolls: int = 0
    stop_reason: str | None = None

    @property
    def checked(self) -> int:
        return len(self.observed) + len(self.failed)


def _collect_current_view(client: ChromeDebugClient, batch: CaptureBatch, budget: int,
                          url_contains: str, job_id: str | None, delay: float, on_progress=None, on_candidate=None) -> None:
    payload = client.capture_recommend_candidates(max_count=budget * 3, url_contains=url_contains)
    for card in payload.get("candidates", []):
        if batch.checked >= budget:
            break
        candidate_id = stable_candidate_id(card)
        if candidate_id is None:
            batch.failed.append({"candidate_id": None, "capture_status": "failed", "reason": "candidate_id_required"})
            continue
        if candidate_id in batch.seen:
            batch.duplicates += 1
            continue
        batch.seen.add(candidate_id)
        try:
            if not _detail_closed(client, url_contains):
                batch.failed.append({"candidate_id": candidate_id, "capture_status": "failed", "reason": "prior_detail_not_closed"})
                batch.stop_reason = "prior_detail_not_closed"
                break
            opened = client.open_recommend_candidate(candidate_id, url_contains=url_contains, strict_identity=True)
            if opened.get("opened") is not True or opened.get("candidateId") != candidate_id:
                detail = {}
            else:
                detail = client.capture_candidate_resume(url_contains, expected_candidate_id=candidate_id)
            standard = adapt_recommend_candidate(card, opened, detail, job_id=job_id)
            fingerprints = {
                key: sha256(text.strip().encode()).hexdigest()
                for key, value in (("dom", "fullDomText"), ("ocr", "ocrText"))
                if isinstance(text := detail.get(value), str) and text.strip()
            }
            stale = any(batch.previous_fingerprints.get(source) == digest for source, digest in fingerprints.items())
            batch.previous_fingerprints = fingerprints
            if stale:
                raise ValueError("repeated_detail_content_identity_unverified")
            batch.observed.append(standard.to_ranking_input())
        except ValueError as exc:
            # Adapter errors are short identifiers; transport details never enter a report.
            reason = str(exc)
            if reason not in {
                "card_open_failed_or_identity_mismatch", "detail_identity_mismatch_or_unverified",
                "detail_not_ready", "resume_evidence_missing", "repeated_detail_content_identity_unverified",
                "detail_missing", "detail_changed_during_ocr",
            }:
                reason = "candidate_capture_error"
            batch.failed.append({"candidate_id": candidate_id, "capture_status": "failed", "reason": reason})
        except Exception:
            batch.failed.append({"candidate_id": candidate_id, "capture_status": "failed", "reason": "candidate_capture_error"})
        else:
            if on_candidate is not None:
                on_candidate(batch, batch.observed[-1])
        if on_progress is not None:
            on_progress(batch, candidate_id)
        if batch.stop_reason:
            break
        if delay:
            time.sleep(delay)


def _collect(client: ChromeDebugClient, budget: int, url_contains: str, job_title: str,
             job_id: str | None, delay: float, max_scrolls: int, no_new_limit: int,
             on_progress=None, on_candidate=None) -> CaptureBatch:
    batch = CaptureBatch()
    consecutive_empty = 0
    while True:
        try:
            if client.has_recommend_challenge(url_contains):
                batch.stop_reason = "page_challenge_detected"
                break
            if not client.verify_recommend_job_context(job_title, job_id, url_contains):
                batch.stop_reason = "job_context_changed"
                break
            before = len(batch.seen)
            _collect_current_view(client, batch, budget, url_contains, job_id, delay,
                                  on_progress=on_progress, on_candidate=on_candidate)
            consecutive_empty = consecutive_empty + 1 if len(batch.seen) == before else 0
            if batch.stop_reason:
                break
            if batch.checked >= budget or batch.scrolls >= max_scrolls or consecutive_empty >= no_new_limit:
                batch.stop_reason = ("check_limit_reached" if batch.checked >= budget else
                                     "no_new_candidates" if consecutive_empty >= no_new_limit else "scroll_limit_reached")
                break
            if not _detail_closed(client, url_contains):
                batch.stop_reason = "prior_detail_not_closed"
                break
            if not client.verify_recommend_job_context(job_title, job_id, url_contains):
                batch.stop_reason = "job_context_changed"
                break
            move = client.advance_recommend_viewport(url_contains)
            if move.get("advanced") is not True:
                batch.stop_reason = str(move.get("reason") or "scroll_unverified")
                break
            batch.scrolls += 1
            if not client.verify_recommend_job_context(job_title, job_id, url_contains):
                batch.stop_reason = "job_context_changed"
                break
            if client.wait_for_recommend_results_stable(url_contains).get("stable") is not True:
                batch.stop_reason = "recommend_results_unstable"
                break
        except Exception:
            batch.stop_reason = "recommend_viewport_capture_error"
            break
    return batch


def run_recommend_preview(
    client: ChromeDebugClient, task: RecruitmentTask, *,
    profile: JobProfile | None = None, template: str | None = None,
    profile_version: str | None = None, original_request: str | None = None,
    default_max_checks: int = 200, url_contains: str = "/web/chat/recommend",
    operation_delay: float = 0, max_filter_retries: int = 2, judge: SemanticJudge | None = None,
    allow_live_preview: bool = False, max_scrolls: int = 8, no_new_limit: int = 2, on_prepared=None,
    expected_job_id: str | None = None,
    shadow_evaluation: bool = False,
    include_candidate_profile: bool = False,
    education_model=None,
    resume_model=None,
    progress=None,
    greet_current=None,
) -> dict:
    if shadow_evaluation and client.mock_dir is None:
        raise ValueError("shadow_evaluation_requires_simulated_browser")
    # An explicitly selected profile adds requirements; conflicts never overwrite the task.
    requested = replace(task, dry_run=True, approval_required=True)
    planned_task, dimensions = prepare_recruitment_preview(requested, profile, template)
    if greet_current is not None and planned_task.action != "greet":
        raise ValueError("incremental_greeting_requires_confirmed_contact_task")
    requirements = [rule for condition in [*planned_task.hard_filters,
                    *(item.condition for item in _scoring_dimensions(planned_task, dimensions))]
                    for rule in iter_criteria(condition)] if resume_model is not None else []

    def apply_model(candidate):
        resume, findings, status = with_recognized_resume(candidate["structured_evidence"], candidate["candidate_id"],
                                                          resume_model, requirements)
        candidate.update(structured_evidence=resume, resume_model_status=status, model_findings=findings)
    if planned_task.job and SENSITIVE.search(planned_task.job):
        raise ValueError("invalid_job")
    budget = planned_task.max_checks or default_max_checks
    if type(budget) is not int or not 1 <= budget <= 500:
        raise ValueError("max_checks_must_be_between_1_and_500")
    if client.mock_dir is None and not allow_live_preview:
        raise ValueError("live_preview_requires_explicit_authorization")
    if not 0 <= operation_delay <= 60 or not 0 <= max_filter_retries <= 5:
        raise ValueError("invalid_browser_preview_limits")
    if type(max_scrolls) is not int or not 0 <= max_scrolls <= 40 or type(no_new_limit) is not int or not 1 <= no_new_limit <= 5:
        raise ValueError("invalid_scroll_limits")
    started_on = date.today()
    plan = plan_recruitment_task(planned_task, preview_only=True)
    if dimensions:
        plan["blockers"] = [reason for reason in plan["blockers"] if reason != "criteria_required"]
    if plan["blockers"]:
        raise ValueError(", ".join(plan["blockers"]))
    plan["status"] = "planned"
    filters = _page_filters(planned_task)
    if on_prepared is not None:
        on_prepared(planned_task, plan, filters, budget)
    client.pin_recommend_page(url_contains)
    try:
        client.activate_recommend_page(url_contains)
        stop_reason = None
        batch = CaptureBatch()
        job_id = None
        if client.open_recommend_page(url_contains).get("opened") is not True:
            stop_reason = "recommend_page_unavailable"
        elif client.has_recommend_challenge(url_contains):
            stop_reason = "page_challenge_detected"
        elif not _detail_closed(client, url_contains):
            stop_reason = "prior_detail_not_closed"
        else:
            identity = {"expected_job_id": expected_job_id} if expected_job_id is not None else {}
            job = client.switch_recommend_job_filter(planned_task.job, url_contains, exact=True, **identity)
            if (job.get("switched") is not True or job.get("selected") != " ".join(planned_task.job.split())
                    or expected_job_id is not None and job.get("jobId") != expected_job_id):
                stop_reason = "job_selection_unverified"
            else:
                job_id = job.get("jobId")
                if client.has_recommend_filter_tags(url_contains):
                    stop_reason = "existing_page_filters_require_reset"
                if filters and stop_reason is None:
                    filter_options = {"confirmed_page_filters": True} if "年龄" in planned_task.boss_page_filters else {}
                    result = client.apply_recommend_filters(
                        job_title="", city="", filters=filters, url_contains=url_contains,
                        operation_delay_seconds=operation_delay, max_filter_retries=max_filter_retries,
                        **filter_options,
                    )
                    if result.get("filterVerificationPassed") is not True:
                        stop_reason = "page_filters_unverified"
                if stop_reason is None and client.wait_for_recommend_results_stable(url_contains).get("stable") is not True:
                    stop_reason = "recommend_results_unstable"
                if stop_reason is None:
                    try:
                        on_progress = None
                        if progress is not None:
                            def on_progress(batch, candidate_id, _budget=budget):
                                progress(phase="collect", checked=batch.checked, budget=_budget, candidate=candidate_id,
                                         **({"greeted": sum(item["greeted"] for item in batch.greetings)}
                                            if greet_current is not None else {}))
                        def on_candidate(current_batch, candidate):
                            if progress is not None:
                                progress(phase="ranking", checked=current_batch.checked, budget=budget)
                            if resume_model is not None:
                                apply_model(candidate)
                            preview = rank_recruitment_preview(requested, [candidate], profile=profile,
                                                               template=template, judge=judge, as_of=started_on)
                            if not preview["contact_preview"]:
                                return
                            if progress is not None:
                                progress(phase="contact", checked=current_batch.checked, budget=budget)
                            candidate_id = candidate["candidate_id"]
                            try:
                                event, reason = greet_current(client, candidate_id, planned_task.job, job_id, url_contains)
                            except (OSError, ValueError):
                                reason = "send_state_unverified"
                                event = {"candidate_id": candidate_id, "greeted": False, "reason": reason}
                            current_batch.greetings.append(event)
                            sent = sum(item["greeted"] for item in current_batch.greetings)
                            current_batch.stop_reason = reason or (
                                "contact_target_reached" if sent >= planned_task.target_success_count else None)

                        batch = _collect(client, budget, url_contains, planned_task.job, job_id,
                                         operation_delay, max_scrolls, no_new_limit, on_progress=on_progress,
                                         on_candidate=on_candidate if greet_current is not None else None)
                        stop_reason = batch.stop_reason
                    finally:
                        try:
                            if stop_reason not in {"page_challenge_detected", "job_context_changed"}:
                                if client.has_recommend_challenge(url_contains):
                                    stop_reason = "page_challenge_detected"
                                elif not _detail_closed(client, url_contains):
                                    stop_reason = "detail_cleanup_unverified"
                        except Exception:
                            stop_reason = "detail_cleanup_failed"

        if progress is not None:
            progress(phase="ranking", checked=batch.checked, budget=budget)
        if resume_model is not None:
            for candidate in batch.observed:
                if "model_findings" not in candidate:
                    apply_model(candidate)
        result = rank_recruitment_preview(
            requested, batch.observed, profile=profile, template=template, judge=judge,
            default_max_checks=default_max_checks, as_of=started_on,
        )
        for rank, row in enumerate(result["ranked_candidates"], 1):
            row["rank"] = rank
        capture_batch_id = uuid4().hex
        observed = {candidate["candidate_id"]: candidate for candidate in batch.observed}
        for key in ("ranked_candidates", "rejected_candidates", "review_candidates"):
            for row in result[key]:
                row["job"] = planned_task.job
                candidate = observed[row["candidate_id"]]
                row["candidate_evidence"] = evidence_report(candidate, capture_batch_id, started_on, task=planned_task,
                                                             education_model=education_model)
                if include_candidate_profile:
                    row["candidate_profile"] = {
                        "name": candidate["candidate_name"] or None,
                        "resume_sources": candidate["sources"],
                    }
        result["summary"].update({
            "checked": batch.checked, "capture_failed": len(batch.failed),
            "recommended_contacts": len(result["contact_preview"]),
            "actual_successful_contacts": sum(item["greeted"] for item in batch.greetings),
            "duplicate_cards_skipped": batch.duplicates,
        })
        if shadow_evaluation:
            official_rows = [row for key in ("ranked_candidates", "rejected_candidates", "review_candidates")
                             for row in result[key]]
            comparisons = []
            scoring = _scoring_dimensions(planned_task, dimensions)
            for row in official_rows + batch.failed:
                key = row["candidate_id"]
                legacy = row.get("eligibility_status", "CAPTURE_FAILED")
                evidence = row.get("candidate_evidence") or {
                    "identity_status": "UNVERIFIED", "capture_status": "failed", "issues": [row["reason"]],
                }
                shadow = evaluate_shadow(planned_task, evidence, observed.get(key, {}), scoring)
                comparisons.append({"candidate_id": key, "official_status": legacy, "official_score": row.get("score"),
                                    "official_rank": row.get("rank"), "official_criteria": row.get("hard_filters", []),
                                    "shadow": shadow, "qualification_changed": shadow["compatibility_status"] != legacy})
            result["shadow_evaluation"] = {
                "official_rule_version": LEGACY_VERSION, "shadow_rule_version": SHADOW_VERSION,
                "rows": comparisons, "shadow_ranked_candidate_ids": [
                    row["candidate_id"] for row in sorted(
                        (item for item in comparisons if item["shadow"]["status"] == "PASS"),
                        key=lambda item: (-item["shadow"]["score"], item["candidate_id"]),
                    )
                ],
            }
        return {
            **result, "plan": plan, "job": planned_task.job, "job_id": job_id, "profile_version": profile_version,
            "original_request": original_request, "page_filters": filters, "max_checks": budget,
            "candidate_evidence_version": EVIDENCE_VERSION, "capture_batch_id": capture_batch_id,
            "capture_failed_candidates": [{**row, "job": planned_task.job, "eligibility_status": "CAPTURE_FAILED",
                                           "candidate_evidence": {"version": EVIDENCE_VERSION,
                                                                  "capture_batch_id": capture_batch_id,
                                                                  "identity_status": "UNVERIFIED",
                                                                  "capture_status": "failed",
                                                                  "issues": [row["reason"]], "evidence": {}}}
                                          for row in batch.failed],
            "stop_reason": stop_reason, "scrolls": batch.scrolls,
            **({"inline_greetings": batch.greetings, "execution_mode": "screen_and_greet"} if greet_current is not None else {}),
            "visited_candidate_ids": sorted(batch.seen),
            "data_source": "simulated_browser" if client.mock_dir is not None else "boss_browser_preview",
        }
    finally:
        client.release_recommend_page()


def profile_fingerprint(data: dict) -> str:
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
