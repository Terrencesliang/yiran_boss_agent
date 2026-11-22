from datetime import date
import itertools
import json
from pathlib import Path

import pytest

from boss_agent.candidate_adapter import adapt_recommend_candidate
from boss_agent.candidate_evidence import evidence_report
from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.job_profile import load_condition
from boss_agent.recommend_preview import load_preview_task, run_recommend_preview
from boss_agent.recommendation import _scoring_dimensions, rank_recruitment_preview
from boss_agent.shadow_rule_engine import combine, evaluate_shadow
from boss_agent.task_schema import RecruitmentTask
from test_browser_recruitment_preview import detail, write_batch_fixture


CASES = json.loads((Path(__file__).parent / "fixtures" / "phase5_shadow_cases.json").read_text(encoding="utf-8"))
AS_OF = date(2026, 1, 1)


def condition(data):
    if "conditions" in data:
        return {**data, "conditions": [condition(item) for item in data["conditions"]]}
    return {**data, "source": f'{data["field"]} {data["operator"]} {data["value"]}'}


def run_case(case):
    task = RecruitmentTask("离线合成岗位", [load_condition(condition(rule)) for rule in case["hard"]],
                           [load_condition(condition(rule)) for rule in case.get("soft", [])], None, None, "screen")
    ocr = {"ocrText": case["ocr"], "ocrStatus": "ok"} if case.get("ocr") else {}
    record = detail(case["id"], case["dom"], **ocr)
    if case.get("warnings"):
        record.update(domTruncated=True)
    card = {"candidateId": case["id"]}
    opened = {"opened": True, "candidateId": case["id"]}
    if case.get("invalidateIdentity"):
        candidate = {"candidate_id": case["id"], "capture_status": "failed"}
        report = {"identity_status": "UNVERIFIED", "capture_status": "failed",
                  "issues": ["detail_identity_mismatch_or_unverified"]}
        with pytest.raises(ValueError, match="identity_mismatch"):
            adapt_recommend_candidate(card, opened, {**record, "detailCandidateId": "other"})
    else:
        candidate = adapt_recommend_candidate(card, opened, record).to_ranking_input()
        report = evidence_report(candidate, "phase5-synthetic", AS_OF, task=task)
        if case.get("invalidateEvidence"):
            for ref in report["evidence"].values():
                ref["start"] += 1
    shadow = evaluate_shadow(task, report, candidate, _scoring_dimensions(task, []))
    legacy_run = rank_recruitment_preview(task, [candidate], as_of=AS_OF)
    legacy, = [row for bucket in ("ranked_candidates", "rejected_candidates", "review_candidates", "capture_failed_candidates")
               for row in legacy_run[bucket]]
    return shadow, legacy, candidate.get("sources", {})


def leaves(items):
    for item in items:
        if item.get("children"):
            yield from leaves(item["children"])
        else:
            yield item


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_annotated_case(case):
    result, _, sources = run_case(case)
    assert result["status"] == case["status"]
    assert [item["status"] for item in result["criteria"]] == case["expected"]
    all_criteria = list(result["criteria"])
    for other in result.get("education_alternatives", []):
        all_criteria.extend(other["criteria"])
    codes = set(result["reason_codes"]) | {code for leaf in leaves(all_criteria) for code in leaf["reason_codes"]}
    assert case["reason"] in codes
    if case.get("quote"):
        cited = {key for leaf in leaves(all_criteria) for key in leaf["evidence_ids"]}
        assert any(case["quote"] in result["evidence"][key]["quote"] for key in cited)
    assert result["score"] == case.get("score", 0 if result["status"] == "PASS" else None)
    all_criteria.extend(value["evaluation"] for value in result["score_breakdown"].values())
    for item in leaves(all_criteria):
        if item["status"] in {"PASS", "FAIL", "CONFLICT"}:
            assert item["evidence_ids"]
        for key in item["evidence_ids"]:
            ref = result["evidence"][key]
            assert sources[ref["source_type"].lower()][ref["start"]:ref["end"]] == ref["quote"]
    if result["status"] == "CONFLICT":
        assert result["compatibility_status"] == "UNKNOWN"


@pytest.mark.parametrize("operator", ["AND", "OR", "NOT"])
def test_truth_table(operator):
    statuses = ("PASS", "FAIL", "UNKNOWN", "CONFLICT")
    table = {
        "AND": ("PFUC", "FFFF", "UFUC", "CFCC"),
        "OR": ("PPPP", "PFUC", "PUUC", "PCCC"),
        "NOT": ("FPUC",),
    }
    inputs = itertools.product(statuses, repeat=1 if operator == "NOT" else 2)
    actual = [combine(operator, list(items))[0] for items in inputs]
    assert actual == list("".join(table[operator]))


def test_shadow_option_does_not_allow_live_browser():
    task = RecruitmentTask("离线合成岗位", [], [], None, None, "screen")
    with pytest.raises(ValueError, match="shadow_evaluation_requires_simulated_browser"):
        run_recommend_preview(ChromeDebugClient("http://example.invalid"), task,
                              allow_live_preview=True, shadow_evaluation=True)


def test_shadow_does_not_change_official_results(tmp_path):
    write_batch_fixture(tmp_path)
    case = next(item for item in CASES if item["id"] == "major-standard")
    task = load_preview_task({"job": "测试岗位", "hard_filters": [condition(rule) for rule in case["hard"]],
                              "soft_preferences": [], "max_checks": 30, "target_success_count": None, "action": "screen"})
    baseline = run_recommend_preview(ChromeDebugClient("http://example.invalid", tmp_path), task)
    replay = run_recommend_preview(ChromeDebugClient("http://example.invalid", tmp_path), task, shadow_evaluation=True)
    assert "shadow_evaluation" not in baseline
    assert len(replay["shadow_evaluation"]["rows"]) == 30
    assert baseline["summary"] == replay["summary"]
    assert replay["task"] == baseline["task"]
    for bucket in ("ranked_candidates", "rejected_candidates", "review_candidates", "capture_failed_candidates"):
        assert [{k: v for k, v in row.items() if k != "candidate_evidence"} for row in replay[bucket]] == [
            {k: v for k, v in row.items() if k != "candidate_evidence"} for row in baseline[bucket]]
