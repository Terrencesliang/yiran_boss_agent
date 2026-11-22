"""Demo isolation and authenticated snapshot restoration contracts."""

from test_workbench_reviews import expect_http, http


def test_demo_blocks_platform_actions_and_forces_local_parser(http, monkeypatch):
    call, desk, _, _ = http
    desk.demo = True
    monkeypatch.setenv("BOSS_TASK_AI_ENABLED", "1")
    desk.operation("safe-sync", "sync", {})
    workspace = call("/api/workspace")
    assert workspace["mode"] == "mock" and workspace["configured_parser"] == "rule"
    assert len(workspace["job_records"]) == 2
    assert call("/api/browser")["mode"] == "demo"
    expect_http(400, lambda: call("/api/runs", {"action":"launch","page":"recommend"}))
    expect_http(400, lambda: call("/api/runs", {"action":"model-test"}))
    expect_http(400, lambda: call("/api/scheduler", {"enabled":True}))
    request = {"job_id":workspace["job_records"][0]["job_id"],"request":workspace["example_request"],
               "tab":"c"*32,"generation":1}
    parsed = call("/api/plan",request)
    assert parsed["parsing_metadata"]["mode"] == "rule"
    assert not parsed["plan"]["blockers"] and parsed["task"]["max_checks"] == 30


def test_restore_snapshot_is_read_only_and_rejects_other_sessions(http):
    call, desk, _, _ = http
    desk.operation("safe-sync","sync",{})
    job = call("/api/workspace")["job_records"][0]["job_id"]
    raw = "本科及以上"
    request = {"job_id":job,"request":raw,"tab":"d"*32,"generation":1}
    draft = call("/api/plan",request)
    url = "/api/plans/" + draft["snapshot"]["plan_id"]
    assert call(url)["snapshot"]["status"] == "draft"
    expect_http(403,lambda:call(url,token=None))
    expect_http(409,lambda:call(url,token="e"*32))
    confirmed = call(url+"/confirm",{"task_hash":draft["snapshot"]["task_hash"]})
    restored = call(url)
    assert restored["snapshot"] == confirmed["snapshot"]
    assert restored["job_id"] == job and restored["source_text"] == raw
    call("/api/plans/invalidate",{"tab":"d"*32,"generation":2})
    expect_http(409,lambda:call(url))
