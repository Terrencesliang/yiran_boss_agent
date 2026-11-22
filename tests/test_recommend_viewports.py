import json
from unittest.mock import Mock

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.recommend_preview import run_recommend_preview
from boss_agent.task_parser import parse_recruitment_task
from test_browser_recruitment_preview import detail, write_mock_data


def test_virtual_lists_deduplicate_reordered_cards_and_stop_on_stagnation(tmp_path, monkeypatch):
    def viewport(*ids):
        return {"candidates": [{"candidateId": key, "cardIndex": index} for index, key in enumerate(ids)]}

    views = [viewport("a", "b"), viewport("b", "a", "c"), viewport("c", "b"), viewport("a", "c"), viewport("d")]
    write_mock_data(tmp_path, views[0]["candidates"], {
        key: detail(key, f"学历：本科\n模拟样本 {key}") for key in "abcd"
    })
    (tmp_path / "recommend_viewports.json").write_text(json.dumps(views), encoding="utf-8")
    client = ChromeDebugClient("http://localhost:9222", tmp_path)
    opened = Mock(wraps=client.open_recommend_candidate)
    monkeypatch.setattr(client, "open_recommend_candidate", opened)
    result = run_recommend_preview(client, parse_recruitment_task("岗位：设计师，本科，联系10人"))
    assert [call.args[0] for call in opened.call_args_list] == ["a", "b", "c"]
    assert result["summary"]["duplicate_cards_skipped"] == 6
    assert result["scrolls"] == 3 and result["stop_reason"] == "no_new_candidates"
    assert result["contact_preview"] == ["a", "b", "c"]
    assert result["summary"]["actual_successful_contacts"] == 0


def test_zero_scroll_limit_never_advances(tmp_path, monkeypatch):
    write_mock_data(tmp_path, [{"candidateId": "a"}], {"a": detail("a", "学历：本科")})
    client = ChromeDebugClient("http://localhost:9222", tmp_path)
    monkeypatch.setattr(client, "advance_recommend_viewport", Mock(side_effect=AssertionError("scroll")))
    result = run_recommend_preview(client, parse_recruitment_task("岗位：设计师，本科"), max_scrolls=0)
    assert result["stop_reason"] == "scroll_limit_reached" and result["scrolls"] == 0

def test_challenge_prevents_job_switch_and_scroll(tmp_path, monkeypatch):
    client = ChromeDebugClient("http://localhost:9222", tmp_path)
    monkeypatch.setattr(client, "has_recommend_challenge", lambda _: True)
    monkeypatch.setattr(client, "switch_recommend_job_filter", Mock(side_effect=AssertionError("job switch")))
    monkeypatch.setattr(client, "advance_recommend_viewport", Mock(side_effect=AssertionError("scroll")))
    result = run_recommend_preview(client, parse_recruitment_task("岗位：设计师，本科"))
    assert result["stop_reason"] == "page_challenge_detected"
    assert result["summary"]["checked"] == 0
