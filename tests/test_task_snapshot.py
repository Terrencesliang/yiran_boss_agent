from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.semantic_task_parser import parse_task_with_metadata
from boss_agent.task_snapshot import TaskSnapshots, digest
from test_browser_recruitment_preview import write_batch_fixture
from test_semantic_task_parser import REQUEST, fixed_model


def records(store):
    return store.sync_jobs([{"text": "测试岗位", "jobId": "A"}, {"text": "测试岗位", "jobId": "B"},
                            {"text": "无身份"}])


def make_draft(store, job, generation=1, raw="本科"):
    parsed = parse_task_with_metadata(raw, enabled=False)
    return store.draft("owner", "tab", generation, job, raw, replace(parsed.task, job=job["job_name"]), parsed.metadata)


def test_job_identity_survives_sync_and_duplicate_names(tmp_path):
    store = TaskSnapshots(tmp_path / "plans.db")
    jobs = {job["source_identity"]: job for job in records(store)}
    assert set(jobs) == {"A", "B"}
    assert jobs["A"]["job_id"] != jobs["B"]["job_id"]
    next_sync = {job["source_identity"]: job for job in records(store)}
    assert next_sync["A"]["job_id"] == jobs["A"]["job_id"]
    assert next_sync["A"]["sync_version"] != jobs["A"]["sync_version"]
    changed = store.sync_jobs([{"text": "其他岗位", "jobId": "A"}])[0]
    assert changed["job_id"] not in {record["job_id"] for record in jobs.values()}
    for job in jobs.values():
        with pytest.raises(ValueError, match="无法核验"):
            store.job(job["job_id"])
    assert not store.sync_jobs([{"text": "岗位一", "jobId": "ambiguous"}, {"text": "岗位二", "jobId": "ambiguous"}])
    assert not store.sync_jobs([{"text": "测试岗位", "jobId": "A"}, {"text": "测试岗位"}])


def test_switch_same_name_by_platform_id_without_browser(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"url": "https://example.test/web/chat/recommend",
                                                         "webSocketDebuggerUrl": "ws://mock"}])
    monkeypatch.setattr("boss_agent.chrome_debug.time.sleep", lambda seconds: None)
    states = iter([{"selected": "测试岗位", "jobId": "A", "opened": True, "duplicateLabels": ["测试岗位"],
                    "options": [{"text": "测试岗位", "jobId": "A", "x": 1, "y": 2},
                                {"text": "测试岗位", "jobId": "B", "x": 3, "y": 4}]},
                   {"selected": "测试岗位", "jobId": "B"}])
    monkeypatch.setattr(client, "_recommend_job_filter_state", lambda ws: next(states))
    clicks = []
    monkeypatch.setattr(client, "_dispatch_mouse_click", lambda ws, x, y: clicks.append((x, y)))
    result = client.switch_recommend_job_filter("测试岗位", exact=True, expected_job_id="B")
    assert result["switched"] and result["jobId"] == "B" and clicks == [(3, 4)]


def test_lifecycle_cross_owner_revision_and_idempotency(tmp_path, monkeypatch):
    file = tmp_path / "plans.db"
    store = TaskSnapshots(file)
    job, other = records(store)
    row = make_draft(store, job)
    key = row["plan_id"]
    assert make_draft(store, job)["plan_id"] == key
    with pytest.raises(ValueError, match="已确认"):
        store.reserve("owner", key, job["job_id"], "early")
    with pytest.raises(ValueError, match="不存在"):
        store.confirm("another-owner", key, row["task_hash"])
    with pytest.raises(ValueError, match="哈希"):
        store.confirm("owner", key, "incorrect")

    with ThreadPoolExecutor(max_workers=3) as pool:
        confirmations = list(pool.map(lambda _: TaskSnapshots(file).confirm("owner", key, row["task_hash"]), range(3)))
    assert len({item["confirmed_at"] for item in confirmations}) == 1
    with pytest.raises(ValueError, match="属于该岗位"):
        store.reserve("owner", key, other["job_id"], "cross-job")
    with ThreadPoolExecutor(max_workers=3) as pool:
        reservations = list(pool.map(lambda index: TaskSnapshots(file).reserve("owner", key, job["job_id"], str(index)), range(3)))
    assert len({item[0] for item in reservations}) == 1
    assert sum(item[1] is not None for item in reservations) == 1
    run = reservations[0][0]
    with pytest.raises(ValueError, match="不支持"):
        store.revise("owner", key, row["task_hash"], [{"field": "unrecognized", "operator": "equals", "value": "x"}], [], "检查")
    revision = store.revise("owner", key, row["task_hash"],
                            [{"field": "education_level", "operator": "gte", "value": "bachelor"}], [], "学历要求改为最低门槛")
    assert revision["status"] == "draft" and revision["snapshot_revision"] == 2
    assert revision["parent_plan_id"] == key and revision["task_hash"] != row["task_hash"]
    assert revision["override"]["before"] == row["task"]
    with pytest.raises(ValueError, match="替代"):
        store.confirm("owner", key, row["task_hash"])
    monkeypatch.setattr("boss_agent.task_snapshot.time.time", lambda: row["expires_at"] + 1)
    snapshot, task = TaskSnapshots(file).execution("owner", key, run, row["task_hash"])
    assert snapshot["task"] == task.to_dict() == row["task"]
    with pytest.raises(ValueError, match="领取"):
        store.execution("owner", key, run, row["task_hash"])


def test_reserved_plan_cannot_execute_after_job_identity_is_removed(tmp_path):
    file = tmp_path / "plans.db"
    store = TaskSnapshots(file)
    job, _ = records(store)
    row = make_draft(store, job)
    store.confirm("owner", row["plan_id"], row["task_hash"])
    store.reserve("owner", row["plan_id"], job["job_id"], "worker-run")
    restarted = TaskSnapshots(file)
    assert restarted.pending()[0][0] == "worker-run"
    restarted.sync_jobs([])
    with pytest.raises(ValueError, match="无法核验"):
        restarted.execution("owner", row["plan_id"], "worker-run", row["task_hash"])


def test_workbench_reconstructs_an_unclaimed_queue_record_after_restart(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "recruitment-workbench"))
    import integration

    directory = tmp_path / "data"
    directory.mkdir()
    store = TaskSnapshots(directory / "task-snapshots.sqlite3")
    job, _ = records(store)
    row = make_draft(store, job)
    store.confirm("owner", row["plan_id"], row["task_hash"])
    store.reserve("owner", row["plan_id"], job["job_id"], "recovered-run")
    seen = []

    def preview(self, identifier, action, payload, progress=None):
        snapshot, task = self.snapshots.execution(payload["owner"], payload["plan_id"], identifier, payload["task_hash"])
        seen.append((identifier, action, snapshot["task_hash"], digest(task.to_dict())))
        return {}

    monkeypatch.setattr(integration.Workbench, "operation", preview)
    for _ in range(2):
        desk = integration.Workbench(directory, tmp_path, "http://example.invalid", tmp_path)
        desk.executor.shutdown(wait=True)
        run, = desk.overview(owner="owner")["runs"]
        assert run["status"] == "completed"
        assert not desk.overview(owner="somebody-else")["runs"]
    assert seen == [("recovered-run", "preview", row["task_hash"], row["task_hash"])]


def test_expiration_revocation_cleanup_tamper_and_invalidation(tmp_path, monkeypatch):
    import boss_agent.task_snapshot as module

    clock = [50000]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    store = TaskSnapshots(tmp_path / "plans.db")
    job, _ = records(store)
    old = make_draft(store, job)
    store.invalidate("owner", "tab", 2)
    with pytest.raises(ValueError, match="替代"):
        store.confirm("owner", old["plan_id"], old["task_hash"])
    with pytest.raises(ValueError, match="更新"):
        make_draft(store, job)
    revoked = make_draft(store, job, 2)
    store.revoke("owner", revoked["plan_id"])
    with pytest.raises(ValueError, match="撤销"):
        store.confirm("owner", revoked["plan_id"], revoked["task_hash"])
    expired = make_draft(store, job, 3)
    clock[0] += module.TTL + 1
    with pytest.raises(ValueError, match="过期"):
        store.confirm("owner", expired["plan_id"], expired["task_hash"])
    clock[0] += module.TTL + 1
    row = make_draft(store, job, 4)
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
        row["task"]["hard_filters"][0]["value"] = "doctorate"
        store._save(db, row)
    with pytest.raises(ValueError, match="完整性"):
        store.confirm("owner", row["plan_id"], row["task_hash"])


@pytest.mark.parametrize("checks", [20, 30])
def test_http_snapshot_preview_uses_confirmed_ai_task(tmp_path, monkeypatch, checks):
    _exercise_http_snapshot_preview(tmp_path, monkeypatch, checks, shadow=False)


def test_http_confirmed_snapshot_offline_shadow_result(tmp_path, monkeypatch):
    _exercise_http_snapshot_preview(tmp_path, monkeypatch, 30, shadow=True)


def _exercise_http_snapshot_preview(tmp_path, monkeypatch, checks, *, shadow):
    monkeypatch.setenv("BOSS_TASK_AI_MODEL", "fixture-model")
    monkeypatch.setenv("BOSS_TASK_AI_TIMEOUT_SECONDS", "2")
    workbench = Path(__file__).resolve().parents[1] / "recruitment-workbench"
    monkeypatch.syspath_prepend(str(workbench))
    spec = spec_from_file_location("snapshot_test_workbench", workbench / "server.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    import integration

    fixtures = tmp_path / "mock"
    fixtures.mkdir()
    write_batch_fixture(fixtures)
    desk = module.Workbench(tmp_path / "data", tmp_path, "http://example.invalid", fixtures)
    job, _ = records(desk.snapshots)
    original_parser = parse_task_with_metadata
    calls = []
    request = REQUEST + ("最多检查30份简历。" if checks == 30 else "")

    def fixture_model(system, prompt, model, *, timeout):
        payload = json.loads(prompt)
        assert payload["raw_text"] == request
        calls.append("parsed")
        return fixed_model(system, json.dumps({"raw_text": REQUEST}), model, timeout=timeout)

    monkeypatch.setattr(module, "parse_task_with_metadata",
                        lambda raw: original_parser(raw, enabled=True, client=fixture_model))
    monkeypatch.setattr(integration, "parse_recruitment_task", lambda raw: pytest.fail("worker reparsed source"))
    prepared_tasks = []
    original_preview = integration.run_recommend_preview

    def observe(client, task, **options):
        prepared_tasks.append(task.to_dict())
        return original_preview(client, task, shadow_evaluation=shadow, **options)

    monkeypatch.setattr(integration, "run_recommend_preview", observe)
    opener = build_opener(ProxyHandler({}))
    with module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler) as server:
        server.workbench = desk
        thread = Thread(target=server.serve_forever)
        thread.start()
        root = f"http://127.0.0.1:{server.server_port}"
        try:
            with opener.open(root, timeout=3) as response:
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]

            def post(path, payload, *, credentials=cookie):
                headers = {"Content-Type": "application/json", "X-Workbench": "local-ui", "Cookie": credentials}
                with opener.open(Request(root + path, json.dumps(payload).encode(), headers), timeout=5) as response:
                    return json.load(response)

            parsed = post("/api/plan", {"request": request, "job_id": job["job_id"], "tab": "0" * 32, "generation": 1})
            draft = parsed["snapshot"]
            assert draft["status"] == "draft" and parsed["parsing_metadata"]["mode"] == "ai"
            assert not parsed["plan"]["blockers"]
            payload = {"action": "preview", "plan_id": draft["plan_id"], "job_id": job["job_id"]}
            with pytest.raises(HTTPError):
                post("/api/runs", payload)
            confirm = "/api/plans/" + draft["plan_id"] + "/confirm"
            with pytest.raises(HTTPError):
                post(confirm, {"task_hash": draft["task_hash"]}, credentials="workbench_owner=" + "f" * 32)
            approved = post(confirm, {"task_hash": draft["task_hash"]})
            assert approved["snapshot"]["status"] == "confirmed"
            with pytest.raises(HTTPError):
                post("/api/runs", {**payload, "request": "博士"})
            with pytest.raises(HTTPError):
                post("/api/runs", {"action": "preview", "request": REQUEST, "job": job["job_name"]})
            run = post("/api/runs", payload)
            assert post("/api/runs", payload)["id"] == run["id"]
            desk.executor.shutdown(wait=True)
            report = json.loads((desk.directory / (run["id"] + "-preview.json")).read_text(encoding="utf-8"))
            assert report["summary"]["checked"] == checks
            assert calls == ["parsed"]
            assert report["snapshot_execution"]["task_hash"] == digest(report["task"]) == draft["task_hash"]
            assert prepared_tasks == [approved["task"]] == [report["task"]]
            if shadow:
                assert draft["task_hash"] == "7f26fa4704b49ff0abad341d8481fb5715bb0517873ec1beb7f481c650e5da58"
                comparisons = report["shadow_evaluation"]["rows"]
                assert len(comparisons) == 30
                assert not any(item["qualification_changed"] for item in comparisons)
                assert not report["shadow_evaluation"]["shadow_ranked_candidate_ids"]
            else:
                assert "shadow_evaluation" not in report
            revised = post("/api/plans/" + draft["plan_id"] + "/revise", {
                "task_hash": draft["task_hash"], "reason": "本次只核对最低学历",
                "hard_filters": [{"field": "education_level", "operator": "gte", "value": "bachelor"}],
                "soft_preferences": [],
            })
            assert revised["snapshot"]["status"] == "draft"
            assert revised["snapshot"]["snapshot_revision"] == 2
            assert revised["parsing_metadata"]["mode"] == "human_override"
            with pytest.raises(HTTPError):
                post("/api/runs", {**payload, "plan_id": revised["snapshot"]["plan_id"]})
            with pytest.raises(HTTPError):
                post("/api/runs", payload)
        finally:
            server.shutdown()
            thread.join()
            desk.executor.shutdown(wait=True)
