from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN = "a1b2c3d4e5f6"
TOKEN = "a" * 32
OWNER = sha256(TOKEN.encode()).hexdigest()
HASH = "7f26fa4704b49ff0abad341d8481fb5715bb0517873ec1beb7f481c650e5da58"


def load_server(monkeypatch):
    location = ROOT / "recruitment-workbench"
    monkeypatch.syspath_prepend(str(location))
    spec = spec_from_file_location("review_test_server", location / "server.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_run(workbench, owner=OWNER):
    report = json.loads((ROOT / "phase5-evidence/confirmed-preview-30.json").read_text(encoding="utf-8"))
    audit = {**report["snapshot_execution"], "run_id": RUN}
    report["snapshot_execution"] = audit
    (workbench.directory / f"{RUN}-preview.json").write_text(json.dumps(report), encoding="utf-8")
    with workbench.lock:
        workbench.state["runs"].append({**audit, "id": RUN, "status": "completed", "action": "preview",
                                      "job": report["job"], "_snapshot_execution": {"owner": owner},
                                      "finished": "2026-11-21T20:00:00Z"})
        workbench.persist()
    return report


@pytest.fixture
def http(tmp_path, monkeypatch):
    module = load_server(monkeypatch)
    directory = tmp_path / "data"
    workspace = module.Workbench(directory, tmp_path, "http://example.invalid", tmp_path)
    report = seed_run(workspace)
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.workbench = workspace
    server.review_study = module.BlindStudy(workspace.reviews, ROOT / "boss-agent/tests/fixtures/phase5_shadow_cases.json",
                                            ROOT / "phase5-evidence/synthetic-assessment.json", "fixture-secret")
    thread = Thread(target=server.serve_forever)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, body=None, token=TOKEN, header=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"workbench_owner={token}"
        if header:
            headers["X-Workbench"] = "local-ui"
        data = None if body is None else json.dumps(body).encode()
        with opener.open(Request(base + path, data=data, headers=headers), timeout=5) as response:
            return json.load(response)
    try:
        yield call, workspace, report, module
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        workspace.executor.shutdown(wait=True)


def expect_http(code, action):
    with pytest.raises(HTTPError) as error:
        action()
    assert error.value.code == code
    error.value.close()


def test_education_correction_is_versioned_and_reversible(http):
    call, _, _, _ = http
    detail_url = f"/api/runs/{RUN}/candidates/mock-01"
    url = f"/api/runs/{RUN}/education-reviews"
    candidate = call(detail_url)["candidate"]
    record = candidate["candidate_evidence"]["education_records"][0]
    facts = candidate["candidate_evidence"]["facts"]
    degree = next(fact for fact in facts if fact["fact_id"] in record["fact_ids"] and fact["field"] == "education_level")
    payload = dict(candidate_id="mock-01", record_id=record["record_id"], field="education_level", action="correct",
                   value="大专", reason="测试人工更正", evidence_ids=degree["evidence_ids"],
                   expected_version=0, evidence_revision=candidate["evidence_revision"])
    assert call(url, payload)["version"] == 1
    expect_http(409, lambda: call(url, payload))
    assert call(detail_url)["candidate"]["education_summary"]["education_level"] == ["大专"]
    assert call(url, {**payload, "action": "revoke", "expected_version": 1, "evidence_ids": []})["version"] == 2
    restored = call(detail_url)
    assert restored["candidate"]["education_summary"]["education_level"] == ["本科"]
    assert [item["action"] for item in restored["education_history"]] == ["correct", "revoke"]


def test_candidate_run_preserves_official_summary_and_protects_reads(http):
    call, _, report, _ = http
    url = f"/api/runs/{RUN}/candidates"
    payload = call(url)
    assert len(payload["rows"]) == 30
    assert payload["summary"] == {"FAIL": 9, "UNKNOWN": 17, "CAPTURE_FAILED": 4}
    assert payload["snapshot"]["task_hash"] == HASH
    assert payload["reviewed_count"] == 0 and payload["reviews"] == {"PENDING": 30}
    assert payload["shadow_engine"] == report["shadow_evaluation"]["shadow_rule_version"]
    assert {row["candidate_id"]: row["official_score"] for row in payload["rows"]} == {
        row["candidate_id"]: row.get("score") for key in ("ranked_candidates", "rejected_candidates", "review_candidates",
                                                         "capture_failed_candidates") for row in report[key]}
    assert call(f"/api/runs/{RUN}/differences")["rows"] == []
    expect_http(403, lambda: call(url, token="b"*32))
    expect_http(403, lambda: call(url, token=None))
    expect_http(403, lambda: call(url+"/unknown"))
    assert call(url+"/mock-01")["candidate"]["candidate_evidence"]["hr_timeline"]["confirmed_hr_days"] is None
    assert call(url+"/mock-27")["candidate"]["official_status"] == "CAPTURE_FAILED"


def test_review_audit_rejects_tampering_and_conflicts_and_recovers_after_restart(http):
    call, workspace, before, module = http
    root = f"/api/runs/{RUN}"
    detail = call(root+"/candidates/mock-01")["candidate"]
    payload = {"candidate_id":"mock-01", "expected_version":0, "evidence_revision":detail["evidence_revision"],
               "review_status":"NEED_MORE_EVIDENCE", "reason":"合成复核记录",
               "condition_ids":[detail["official_criteria"][0]["criterion_id"]], "evidence_ids":[]}
    route = root+"/reviews"
    expect_http(400, lambda: call(route, {**payload,"reviewer_id":"spoofed"}))
    expect_http(400, lambda: call(route, {**payload,"review_status":"CONFIRMED_MATCH"}))
    expect_http(400, lambda: call(route, {**payload,"evidence_ids":["fake"]}))
    expect_http(403, lambda: call(route, payload, token="b"*32))
    expect_http(400, lambda: call(route, payload, header=False))
    expect_http(409, lambda: call(route, {**payload,"evidence_revision":"stale"}))
    entry = call(route, payload)
    assert entry["reviewer_id"] == OWNER and entry["task_hash"] == HASH
    assert entry["plan_id"] and entry["reviewed_at"] and entry["review_id"] and entry["engine_version"]
    expect_http(409, lambda: call(route, payload))
    reference = next(iter(detail["evidence"]))
    next_review = call(route, {**payload,"expected_version":1,"review_status":"CONFIRMED_MATCH","evidence_ids":[reference]})
    assert next_review["version"] == 2
    recovered = module.Workbench(workspace.directory, workspace.agent_root, workspace.endpoint, workspace.mock_dir)
    try:
        current = recovered.reviews.read(OWNER,RUN,"candidates","mock-01")
        assert [row["version"] for row in current["history"]] == [2,1]
        assert current["candidate"]["official_status"] == "UNKNOWN"
        assert current["task"] == before["task"]
        assert recovered.reviews.read(OWNER,RUN,"candidates")["reviewed_count"] == 1
    finally:
        recovered.executor.shutdown(wait=True)


def test_blind_annotations_withhold_engine_and_preserve_disagreement(http):
    call, _, _, _ = http
    study = "/api/review-study"
    cases = call(study)
    assert cases["differences"] == 9 and cases["compatibility_differences"] == 5
    assert len(cases["cases"]) == 44 and all(len(item["key"]) == 32 for item in cases["cases"])
    url = study + "/" + cases["cases"][0]["key"]
    before = call(url)
    assert set(before) == {"case_id", "criteria", "evidence", "capture_note", "evidence_usable"}
    assert "major-standard" not in json.dumps(before)
    cite = next(iter(before["evidence"]))
    decisions = [{"criterion_id": item["criterion_id"], "status":"PASS","reason":"合成标注依据",
                  "evidence_ids":[cite]} for item in before["criteria"]]
    expect_http(400, lambda: call(url+"/annotate", {"expected_version":0,"decisions":decisions[:1]}))
    call(url+"/annotate", {"expected_version":0,"decisions":decisions})
    assert "comparison" in call(url) and "comparison" not in call(url, token="b"*32)
    expect_http(409, lambda: call(url+"/annotate", {"expected_version":0,"decisions":decisions}))
    other = [{**decisions[0],"status":"UNKNOWN","evidence_ids":[]}, decisions[1]]
    call(url+"/annotate", {"expected_version":0,"decisions":other}, token="b"*32)
    result = call(url)
    assert len(result["annotations"]) == 2 and result["disagreements"] == ["hard.1"]
    payload = {"expected_version":0,"decisions":decisions[:1],"adjudication_key":"incorrect"}
    expect_http(403, lambda: call(url+"/adjudicate",payload))
    entry = call(url+"/adjudicate", {**payload,"adjudication_key":"fixture-secret"})
    assert entry["version"] == 1 and entry["kind"] == "adjudication"
    assert "adjudication_key" not in entry and len(call(url)["annotations"]) == 2


def test_parallel_review_submissions_allow_only_one_revision(http):
    call, _, _, _ = http
    base = f"/api/runs/{RUN}"
    detail = call(base+"/candidates/mock-02")["candidate"]
    payload = {"candidate_id":"mock-02","expected_version":0,"evidence_revision":detail["evidence_revision"],
               "review_status":"IN_REVIEW","reason":"并发模拟","condition_ids":[],"evidence_ids":[]}

    def send(_):
        try:
            call(base+"/reviews",payload)
            return 200
        except HTTPError as error:
            error.close()
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send,range(2))) == [200,409]
    assert len(call(base+"/reviews")["reviews"]) == 1


def test_shadow_preview_cannot_enable_without_mock(tmp_path, monkeypatch):
    module = load_server(monkeypatch)
    with pytest.raises(ValueError,match="模拟数据"):
        module.Workbench(tmp_path,tmp_path,"http://example.invalid",shadow_preview=True)
