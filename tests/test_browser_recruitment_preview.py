import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from boss_agent.candidate_adapter import adapt_recommend_candidate
from boss_agent.chrome_debug import (
    ChromeDebugClient,
    build_capture_recommend_candidates_expression_v2,
    build_verified_recommend_detail_expression,
)
from boss_agent.cli import main
from boss_agent.job_profile import load_job_profile
from boss_agent.recommend_preview import run_recommend_preview
from boss_agent.task_parser import parse_recruitment_task
from test_generic_recruitment import CASES, semantic_judge


def detail(key, text, **extra):
    return {"detailCandidateId": key, "detailOpen": True, "captureStatus": "ready",
            "fullDomText": text, "canvasExpected": False, **extra}


def write_mock_data(folder, cards, records):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "recommend_candidates.json").write_text(
        json.dumps({"candidates": cards, "url": "mock://recommend"}), encoding="utf-8",
    )
    (folder / "candidate_resumes.json").write_text(json.dumps(records), encoding="utf-8")


def write_batch_fixture(folder):
    cards, records = [], {}
    for i in range(1, 31):
        key = f"mock-{i:02}"
        cards.append({"candidateId": key, "cardIndex": i, "candidateName": f"模拟人{i:02}"})
        text = f"模拟样本 {i:02}\n"
        if i <= 12:
            text += "学历：全日制本科\n" + ("工具：Figma" if i % 3 == 0 else "工具：Excel")
        elif i <= 21:
            text += "学历：全日制大专\n工具：Figma"
        elif i <= 25:
            text += "学历：本科\n工具：Figma"
        else:
            text += "学历：全日制本科\n工具：Figma"
        records[key] = detail(key, text)
    records["mock-26"]["canvasExpected"] = True
    records["mock-27"]["detailCandidateId"] = "mock-26"
    records["mock-28"]["captureStatus"] = "detail_loading"
    del records["mock-29"]
    records["mock-30"]["fullDomText"] = records["mock-26"]["fullDomText"]
    cards.insert(1, dict(cards[0], cardIndex=99))
    write_mock_data(folder, cards, records)


def dom_semantic_judge(rule, sources):
    outcome = json.loads(semantic_judge(rule, {"resume": sources["dom"]}))
    for evidence in outcome["evidence"]:
        evidence["source"] = "dom"
    return json.dumps(outcome)


def adapt(record):
    return adapt_recommend_candidate(
        {"candidateId": "geek-1", "candidateName": "样本姓名", "age": 30, "cardIndex": 1},
        {"opened": True, "candidateId": "geek-1"}, record, job_id="job-native-1",
    )


def test_adapter_preserves_full_text_sections_and_masks_private_fields():
    text = ("简介填充" * 1600 + "\n姓名：样本姓名\n电话：13812345678\n"
            "教育经历\n学历：本科\n工作经历\n工作经验：3年\n项目经历\n交付产品")
    row = adapt(detail("geek-1", text, rawTextPreview="old truncated preview",
                       ocrText="学习形式：全日制\n专业：产品设计", ocrStatus="ok"))
    assert row.candidate_id == "geek-1" and row.job_id == "job-native-1"
    assert row.card_data == {"candidate_id": "geek-1", "candidate_name": "样本姓名"}
    assert row.capture_status == "complete"
    assert "学历：本科" in row.education[0]["text"]
    assert row.education[0]["start"] > 6000
    assert "工作经验：3年" in row.work_experiences[0]["text"]
    assert "交付产品" in row.project_experiences[0]["text"]
    assert row.evidence_sources["ocr"].startswith("学习形式")
    assert "old truncated preview" not in row.resume_text
    assert "13812345678" not in row.resume_text and "样本姓名" not in row.resume_text


@pytest.mark.parametrize(("record", "message"), [
    (detail("other-id", "学历：本科"), "detail_identity_mismatch"),
    ({**detail("geek-1", "学历：本科"), "captureStatus": "detail_loading"}, "detail_not_ready"),
    (detail("geek-1", ""), "resume_evidence_missing"),
])
def test_adapter_blocks_invalid_detail(record, message):
    with pytest.raises(ValueError, match=message):
        adapt(record)


def test_adapter_rejects_index_only_and_unverified_open():
    with pytest.raises(ValueError, match="candidate_id_required"):
        adapt_recommend_candidate({"cardIndex": 1}, {}, {})
    with pytest.raises(ValueError, match="card_open_failed"):
        adapt_recommend_candidate({"candidateId": "x"}, {"opened": True, "candidateId": "y"}, {})


@pytest.mark.parametrize(("record", "warning"), [
    (detail("geek-1", "", ocrText="项目经历：完整可用的离线练习", ocrStatus="ok"), "dom_text_missing"),
    (detail("geek-1", "学历：本科", ocrStatus="canvas_not_found"), "ocr_missing_or_low_quality"),
    (detail("geek-1", "学历：本科", ocrText="???", ocrStatus="ok"), "ocr_missing_or_low_quality"),
    (detail("geek-1", "", rawTextPreview="学历：本科"), "dom_preview_only"),
    (detail("geek-1", "学历：本科", domTruncated=True), "dom_text_truncated"),
    (detail("geek-1", "学历：本科", ocrText="教育经历：完整项目资料", ocrStatus="ok", ocrTruncated=True),
     "ocr_capture_truncated"),
])
def test_incomplete_sources_are_explicit(record, warning):
    row = adapt(record)
    assert row.capture_status == "partial"
    assert warning in row.capture_warnings


@pytest.mark.parametrize(("job", "query", "resume", "_field", "_pref"), CASES)
def test_five_jobs_use_the_same_browser_adapter(tmp_path, job, query, resume, _field, _pref):
    cards, records = [], {}
    for index, (other_job, _, other_resume, _, _) in enumerate(CASES):
        key = f"geek-{index}"
        cards.append({"candidateId": key, "cardIndex": index})
        records[key] = detail(key, other_resume)
    write_mock_data(tmp_path, cards, records)
    task = parse_recruitment_task(f"岗位：{job}，{query}")
    result = run_recommend_preview(ChromeDebugClient("http://127.0.0.1:9222", tmp_path), task, judge=dom_semantic_judge)
    index = next(i for i, case in enumerate(CASES) if case[0] == job)
    assert [(row["candidate_id"], row["score"]) for row in result["ranked_candidates"]] == [(f"geek-{index}", 100)]
    assert result["summary"]["checked"] == 5
    assert result["summary"]["actual_successful_contacts"] == 0


def test_conflicting_source_evidence_and_profile_addition(tmp_path):
    cards = [{"candidateId": "geek-1"}, {"candidateId": "geek-2"}]
    records = {
        "geek-1": detail("geek-1", "学历：本科\n工具：Figma、Sketch", ocrText="学历：大专\n工作经验：3年", ocrStatus="ok"),
        "geek-2": detail("geek-2", "学历：本科\n工具：Figma\n记录：另一个样本"),
    }
    write_mock_data(tmp_path, cards, records)
    task = parse_recruitment_task("岗位：设计师，本科，工具：Figma")
    profile = load_job_profile({"name": "设计师", "requirements": [
        {"field": "tools", "operator": "equals", "value": "Sketch", "source": "明确选用的岗位配置"}
    ]})
    result = run_recommend_preview(ChromeDebugClient("http://127.0.0.1:9222", tmp_path), task, profile=profile)
    assert result["page_filters"] == {"学历": "本科"}
    assert not result["ranked_candidates"]
    first, second = result["review_candidates"]
    assert first["hard_filters"][1]["status"] == "UNKNOWN"
    assert {item["source"] for item in first["hard_filters"][1]["evidence"]} == {"dom", "ocr"}
    assert second["hard_filters"][0]["status"] == "UNKNOWN"
    assert second["hard_filters"][2]["status"] == "PASS"


def test_30_candidate_cli_end_to_end(tmp_path, capsys, monkeypatch):
    write_batch_fixture(tmp_path)
    for method in ("greet_candidate", "click_recommend_card_greet", "click_recommend_detail_greet", "list_pages"):
        monkeypatch.setattr(ChromeDebugClient, method, Mock(side_effect=AssertionError("unexpected live operation")))
    report = tmp_path / "report.json"
    request = "岗位：UI 设计师，全日制本科，工具：Figma优先，联系10人"
    args = ["action", "screen-recommend-detail", "--request", request, "--mock-dir", str(tmp_path),
            "--report-path", str(report), "--max-checks", "30"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(report.read_text(encoding="utf-8"))
    assert result["data_source"] == "simulated_browser"
    assert result["original_request"] == request
    assert result["summary"] == {
        "checked": 30, "qualified": 12, "unqualified": 9, "needs_review": 5, "target_contact_count": 10,
        "capture_failed": 4, "recommended_contacts": 10, "actual_successful_contacts": 0,
        "duplicate_cards_skipped": 1,
    }
    assert result["contact_preview"] == [f"mock-{index:02}" for index in (3, 6, 9, 12, 1, 2, 4, 5, 7, 8)]
    assert [row["rank"] for row in result["ranked_candidates"]] == list(range(1, 13))
    assert [row["score"] for row in result["ranked_candidates"]] == [100] * 4 + [0] * 8
    assert result["review_candidates"][-1]["review_reason"] == "capture_incomplete"
    assert result["capture_failed_candidates"][-1]["reason"] == "repeated_detail_content_identity_unverified"
    assert "contact" not in {step["id"] for step in result["plan"]["steps"]}


def test_plan_file_and_explicit_profile_round_trip(tmp_path, capsys):
    request = "岗位：设计师，本科，工具：Figma优先"
    assert main(["plan-recruitment", "--request", request]) == 0
    plan_output = capsys.readouterr().out
    task_file = tmp_path / "task.json"
    task_file.write_text(plan_output, encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "设计师", "description": "设计", "templates": {"初筛": {}}}), encoding="utf-8")
    write_mock_data(tmp_path, [{"candidateId": "one"}], {
        "one": detail("one", "教育经历\n" + "说明" * 3100 + "\n学历：本科\n工具：Figma")
    })
    assert main(["action", "screen-recommend-detail", "--task-file", str(task_file), "--job-profile", str(profile),
                 "--template", "初筛", "--mock-dir", str(tmp_path), "--report-path", str(tmp_path / "out.json")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["original_request"] == request and len(result["profile_version"]) == 64
    assert result["ranked_candidates"][0]["score"] == 100
    assert result["ranked_candidates"][0]["hard_filters"][0]["evidence"][0]["start"] > 6000


@pytest.mark.parametrize("query", ["本科", "岗位：设计师，本科，未知限制"])
def test_blockers_precede_browser_operations(query, tmp_path, monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222", tmp_path)
    pin = Mock(side_effect=AssertionError("browser touched"))
    monkeypatch.setattr(client, "pin_recommend_page", pin)
    with pytest.raises(ValueError):
        run_recommend_preview(client, parse_recruitment_task(query))
    pin.assert_not_called()


def test_live_preview_requires_explicit_opt_in(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "pin_recommend_page", Mock(side_effect=AssertionError("browser touched")))
    with pytest.raises(ValueError, match="live_preview_requires_explicit_authorization"):
        run_recommend_preview(client, parse_recruitment_task("岗位：设计师，本科"))


def test_strict_open_does_not_use_coordinate_fallback(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"webSocketDebuggerUrl": "mock"}])

    def simulate(_, expression, **kwargs):
        if "const strict" in expression:
            assert "const strict = true" in expression
            assert "matches.length === 1" in expression
            assert "actualId !==" in expression
            return {"domClicked": True, "opened": True, "candidateId": "id", "point": {"x": 5, "y": 6}}
        return {"detailOpen": False}
    monkeypatch.setattr(client, "_evaluate_json", simulate)
    monkeypatch.setattr(client, "_dispatch_mouse_click", Mock(side_effect=AssertionError("coordinate fallback")))
    assert client.open_recommend_candidate("id", strict_identity=True)["opened"] is False


@pytest.mark.skipif(not shutil.which("node"), reason="需要 Node 执行模拟 DOM")
def test_verified_dom_expression_reads_full_visible_owned_detail():
    expression = build_verified_recommend_detail_expression()
    script = """
const style = {getComputedStyle: () => ({visibility: 'visible'})};
const ownerDocument = {defaultView: style};
const owner = {getClientRects: () => [1], ownerDocument, getAttribute: name => name === 'data-geekid' ? 'stable' : null};
const el = {getClientRects: () => [1], ownerDocument, innerText: 'x'.repeat(6500) + '\\n学历：本科',
  closest: () => owner, contains: () => false, querySelectorAll: () => []};
globalThis.document = {querySelector: () => null, querySelectorAll: selector => selector.includes('iframe') ? [] : [el]};
""" + f"console.log({expression});"
    result = subprocess.run(["node"], input=script, encoding="utf-8", capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data["detailCandidateId"] == "stable"
    assert data["captureStatus"] == "ready"
    assert data["fullDomText"].endswith("学历：本科") and len(data["fullDomText"]) > 6500


@pytest.mark.skipif(not shutil.which("node"), reason="需要 Node 执行模拟 DOM")
@pytest.mark.parametrize("minimize", [False, True])
@pytest.mark.parametrize("nested_identity", [False, True])
def test_candidate_capture_handles_minimized_cards_without_preview_text(minimize, nested_identity):
    expression = build_capture_recommend_candidates_expression_v2(5, minimize=minimize)
    script = """
const style = {getComputedStyle: () => ({display:'block', visibility:'visible'}),
  innerHeight:800, innerWidth:1200};
globalThis.window = style;
globalThis.location = {href:'https://www.zhipin.com/web/chat/recommend'};
const makeCard = (id, isGuide=false) => ({
  innerText:'候选人\\n计算机科学与技术本科，具有项目实践经历',
  getAttribute:key => !nestedIdentity && key === 'data-geekid' ? id : null,
  getBoundingClientRect:() => ({width:100,height:100,top:0,bottom:100,left:0,right:100}),
  matches:() => true,
  querySelector:selector => selector === '.anonymous-geek-guide-card' && isGuide ? {} :
    selector.includes('.name') ? {innerText:'候选人'} : null,
  querySelectorAll:selector => nestedIdentity && selector === '[data-geekid], [data-geek-id]'
    ? [{getAttribute:key => key === 'data-geekid' ? id : null}] : []
});
globalThis.document = {defaultView:style, documentElement:{}, querySelector:() => null,
  querySelectorAll:() => [makeCard('stable-a'),makeCard(null,true),makeCard('stable-b')]};
"""
    script = f"const nestedIdentity = {json.dumps(nested_identity)};\n" + script + f"console.log({expression});"
    result = subprocess.run(["node"], input=script, encoding="utf-8", capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert [item["candidateId"] for item in data["candidates"]] == ["stable-a", "stable-b"]
    if minimize:
        assert all(set(item) == {"candidateId", "candidateName"} for item in data["candidates"])

def test_detected_prior_page_filters_stop_collection(tmp_path, monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222", tmp_path)
    monkeypatch.setattr(client, "has_recommend_filter_tags", lambda *_: True)
    monkeypatch.setattr(client, "capture_recommend_candidates", Mock(side_effect=AssertionError("unrequested filters")))
    result = run_recommend_preview(client, parse_recruitment_task("岗位：设计师，本科"))
    assert result["stop_reason"] == "existing_page_filters_require_reset"
    assert result["summary"]["checked"] == 0
