import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "boss_agent.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_health_command_reports_ok() -> None:
    result = run_cli("health")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["service"] == "boss-agent"


def test_debug_version_reads_remote_debugging_endpoint() -> None:
    result = run_cli(
        "debug",
        "version",
        "--endpoint",
        "http://127.0.0.1:9222",
        "--mock-file",
        "tests/fixtures/version.json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["browser"].startswith("Chrome/")
    assert payload["webSocketDebuggerUrl"].startswith("ws://")


def test_debug_launch_command_reports_launch_configuration() -> None:
    result = run_cli(
        "debug",
        "launch-chrome",
        "--chrome-path",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "--user-data-dir",
        r"D:\BOSS直聘\boss-agent\.chrome-profile",
        "--port",
        "9333",
        "--dry-run",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "launch"
    assert payload["port"] == 9333
    assert "--remote-debugging-port=9333" in payload["command"]
    assert "--remote-allow-origins=*" in payload["command"]
    assert "--user-data-dir=D:\\BOSS直聘\\boss-agent\\.chrome-profile" in payload["command"]


def test_debug_detect_page_reports_boss_page_metadata() -> None:
    result = run_cli(
        "debug",
        "detect-page",
        "--endpoint",
        "http://127.0.0.1:9222",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["pageCount"] == 2
    assert payload["matchedCount"] == 2
    assert payload["pages"][0]["platform"] == "boss"
    assert payload["pages"][0]["pageType"] == "chat"


def test_capture_conversation_returns_page_text_summary() -> None:
    result = run_cli(
        "capture",
        "conversation",
        "--endpoint",
        "http://127.0.0.1:9222",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["pageType"] == "chat"
    assert payload["platform"] == "boss"
    assert payload["title"] == "BOSS直聘"
    assert "您好" in payload["text"]
    assert payload["preview"][0].startswith("在线沟通")


def test_capture_conversation_structured_returns_chat_fields() -> None:
    result = run_cli(
        "capture",
        "conversation-structured",
        "--endpoint",
        "http://127.0.0.1:9222",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["candidateName"] == "徐先生"
    assert payload["company"] == "欧琳集团有限公司"
    assert payload["jobTitle"] == "AI 产品落地经理"
    assert payload["salary"] == "20-30K"
    assert payload["city"] == "深圳"
    assert payload["messages"][0]["direction"] == "outbound"


def test_score_conversation_returns_rule_based_decision() -> None:
    result = run_cli(
        "score",
        "conversation",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["match_score"] >= 60
    assert payload["reply_priority"] == "medium"
    assert payload["risk_level"] == "low"
    assert payload["recommended_action"] == "request_resume_or_experience"


def test_draft_reply_returns_rule_based_message() -> None:
    result = run_cli(
        "draft",
        "reply",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["tone"] == "professional"
    assert payload["intent"] == "continue_conversation"
    assert "您好" in payload["body"]
    assert "相关经验" in payload["body"] or "平台" in payload["body"] or "类目" in payload["body"]


def test_draft_fill_returns_fill_result() -> None:
    result = run_cli(
        "draft",
        "fill",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["filled"] is True
    assert payload["selector"] in {".chat-input", ".boss-chat-editor-input", "[contenteditable=\"true\"]"}
    assert "您好" in payload["body"]


def test_draft_reply_uses_knowledge_dir_for_job_question() -> None:
    result = run_cli(
        "draft",
        "reply",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "简历我这边先看一下" in payload["body"]


def test_action_resume_consent_returns_click_result() -> None:
    result = run_cli(
        "action",
        "resume-consent",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["clicked"] is True
    assert payload["selector"] == ".op a.btn"
    assert payload["text"] == "同意"


def test_capture_attachment_link_returns_preview_url() -> None:
    result = run_cli(
        "capture",
        "attachment-link",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "preview4boss" in payload["previewUrl"]
    assert payload["type"] == "attachment_preview"


def test_capture_inbox_unread_returns_unread_conversations() -> None:
    result = run_cli(
        "capture",
        "inbox-unread",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["activeFilter"] == "未读"
    assert payload["count"] == 3
    assert payload["conversations"][1]["candidateName"] == "马庆成"


def test_capture_job_filter_options_returns_jobs() -> None:
    result = run_cli(
        "capture",
        "job-filter-options",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["selected"] == "全部职位"
    assert "文员 _ 珠海 5-6K" in payload["options"]


def test_action_switch_job_filter_returns_selected_job() -> None:
    result = run_cli(
        "action",
        "switch-job-filter",
        "--mock-dir",
        "tests/fixtures",
        "--job-text",
        "销售 _ 广州 8-12K",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["switched"] is True
    assert payload["selected"] == "销售 _ 广州 8-12K"


def test_action_filter_recommendations_returns_analysis() -> None:
    result = run_cli(
        "action",
        "filter-recommendations",
        "--mock-dir",
        "tests/fixtures",
        "--criteria",
        "本科学历，三年工作经验",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["criteria"] == "本科学历，三年工作经验"
    assert payload["checkedCount"] >= 1
    assert "analyzed" in payload


def test_action_screen_and_greet_recommendations_dry_run_returns_matches() -> None:
    result = run_cli(
        "action",
        "screen-and-greet-recommendations",
        "--mock-dir",
        "tests/fixtures",
        "--criteria",
        "本科学历，三年工作经验",
        "--dry-run",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["dryRun"] is True
    assert "matchedNames" in payload
    assert payload["greetResult"]["dryRun"] is True


def test_action_open_conversation_returns_selected_conversation() -> None:
    result = run_cli(
        "action",
        "open-conversation",
        "--mock-dir",
        "tests/fixtures",
        "--data-id",
        "5682470-0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["opened"] is True
    assert payload["candidateName"] == "兰先生"
    assert payload["jobTitle"] == "电商运营经理"


def test_action_download_resume_returns_downloaded_file() -> None:
    result = run_cli(
        "action",
        "download-resume",
        "--mock-dir",
        "tests/fixtures",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["clicked"] is True
    assert payload["downloaded"] is True
    assert payload["file"].endswith(".docx")


def test_action_prepare_reply_runs_resume_flow_and_fills_draft() -> None:
    result = run_cli(
        "action",
        "prepare-reply",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["conversation"]["candidateName"] == "兰先生"
    assert payload["resumePrompt"]["visible"] is True
    assert payload["resumeConsent"]["clicked"] is True
    assert payload["attachmentPreview"]["type"] == "attachment_preview"
    assert payload["resumeDownload"]["downloaded"] is True
    assert payload["draftFill"]["filled"] is True
    assert "简历我这边先看一下" in payload["draft"]["body"]


def test_action_prepare_reply_uses_visible_prompt_even_when_message_text_is_missing(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "hr_conversation_structured.json").write_text(
        json.dumps(
            {
                "title": "BOSS直聘",
                "url": "https://www.zhipin.com/web/chat/index",
                "candidateName": "兰先生",
                "company": "",
                "role": "",
                "jobTitle": "电商运营经理",
                "salary": "15-20K",
                "city": "深圳",
                "messages": [
                    {"direction": "system", "text": "3月23日 沟通的职位-电商运营经理"},
                    {"direction": "inbound", "text": "您好，对岗位很感兴趣"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "prepare-reply",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["resumePrompt"]["visible"] is True
    assert payload["resumeConsent"]["clicked"] is True


def test_action_close_attachment_preview_closes_visible_overlay() -> None:
    result = run_cli(
        "action",
        "close-attachment-preview",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["closed"] is True
    assert payload["selector"] == ".boss-popup__close"


def test_action_process_unread_switches_filter_and_processes_conversations() -> None:
    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "2",
        "--send",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["switchFilter"]["switched"] is True
    assert payload["switchFilter"]["label"] == "未读"
    assert payload["processedCount"] == 2
    assert payload["processed"][0]["openConversation"]["opened"] is True
    assert payload["processed"][0]["prepareReply"]["draftFill"]["filled"] is True
    assert payload["processed"][0]["send"]["sent"] is True


def test_action_send_unread_message_switches_job_and_sends_custom_message() -> None:
    result = run_cli(
        "action",
        "send-unread-message",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--job-text",
        "\u6587\u5458 _ \u73e0\u6d77 5-6K",
        "--message",
        "\u60a8\u7684\u6027\u683c\u662f\u5426\u5f00\u6717\u5462",
        "--max-count",
        "2",
        "--inbox-scrolls",
        "5",
        "--delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["jobText"] == "\u6587\u5458 _ \u73e0\u6d77 5-6K"
    assert payload["message"] == "\u60a8\u7684\u6027\u683c\u662f\u5426\u5f00\u6717\u5462"
    assert payload["inboxScrolls"] == 5
    assert payload["switchJobFilter"]["switched"] is True
    assert payload["switchUnreadFilter"]["label"] == "\u672a\u8bfb"
    assert payload["switchOpenFilter"]["label"] == "\u5168\u90e8"
    assert payload["inbox"]["scrollCapture"]["enabled"] is True
    assert payload["snapshotCount"] == 2
    assert payload["sentCount"] == 2
    assert payload["failedCount"] == 0
    assert payload["results"][0]["fill"]["body"] == "\u60a8\u7684\u6027\u683c\u662f\u5426\u5f00\u6717\u5462"
    assert payload["results"][0]["send"]["sent"] is True


def test_action_request_resume_new_greetings_defaults_to_dry_run() -> None:
    result = run_cli(
        "action",
        "request-resume-new-greetings",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--job-text",
        "\u6587\u5458 _ \u73e0\u6d77 5-6K",
        "--max-count",
        "2",
        "--inbox-scrolls",
        "5",
        "--wait-after-unread",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["dryRun"] is True
    assert payload["message"] == "\u60a8\u597d\uff0c\u65b9\u4fbf\u53d1\u4e00\u4efd\u7b80\u5386\u5417\uff1f\u6211\u8fd9\u8fb9\u5148\u770b\u4e00\u4e0b\u3002"
    assert payload["switchStatusTab"]["matchedText"] == "\u65b0\u62db\u547c(45)"
    assert payload["switchJobFilter"]["switched"] is True
    assert payload["switchUnreadFilter"]["label"] == "\u672a\u8bfb"
    assert payload["snapshotCount"] == 2
    assert payload["processedCount"] == 2
    assert payload["skippedCount"] == 2
    assert payload["results"][0]["reason"] == "dry_run"
    assert payload["results"][0]["send"] is None
    assert payload["results"][0]["requestResume"] is None


def test_action_reply_unread_with_knowledge_defaults_to_dry_run(tmp_path: Path) -> None:
    knowledge_file = tmp_path / "reply_faq.txt"
    knowledge_file.write_text(
        "Q: 我可以把我的简历发给您看看吗？\n"
        "A: 可以的，您直接发过来就好，我这边先看一下。\n",
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "reply-unread-with-knowledge",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--job-text",
        "\u6587\u5458 _ \u73e0\u6d77 5-6K",
        "--knowledge-file",
        str(knowledge_file),
        "--max-count",
        "1",
        "--inbox-scrolls",
        "5",
        "--operation-delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["dryRun"] is True
    assert payload["switchJobFilter"]["switched"] is True
    assert payload["switchUnreadFilter"]["label"] == "\u672a\u8bfb"
    assert payload["snapshotCount"] == 1
    assert payload["processedCount"] == 1
    assert payload["results"][0]["reply"] == "可以的，您直接发过来就好，我这边先看一下。"
    assert payload["results"][0]["reason"] == "dry_run"
    assert payload["results"][0]["send"] is None


def test_action_reply_unread_with_knowledge_uses_routed_knowledge_dir(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    faq_dir = knowledge_dir / "jobs" / "线上分销（美妆）" / "job"
    faq_dir.mkdir(parents=True)
    (faq_dir / "faq.txt").write_text(
        "[常见问答]\n"
        "Q: 我可以把我的简历发给您看看吗？\n"
        "A: 可以的，您直接发过来就好，我这边先看一下。\n",
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "reply-unread-with-knowledge",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--job-text",
        "文员 _ 珠海 5-6K",
        "--knowledge-dir",
        str(knowledge_dir),
        "--max-count",
        "1",
        "--inbox-scrolls",
        "5",
        "--operation-delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["knowledgeMode"] == "routed_dir"
    assert payload["results"][0]["reply"] == "可以的，您直接发过来就好，我这边先看一下。"
    assert payload["results"][0]["matchedIntent"] == "job"
    assert payload["results"][0]["jobFolder"] == "线上分销（美妆）"


def test_recommend_detail_local_filters_accept_multiselect_values() -> None:
    from boss_agent.cli import _recommend_detail_matches_local_filters_v2

    combined = "候选人 26岁 本科 4年经验 14-18K"

    assert _recommend_detail_matches_local_filters_v2(
        combined,
        {"学历": "大专,本科", "薪资": "10-13K,15-20K", "经验": "1-2年,3-5年"},
    )
    assert not _recommend_detail_matches_local_filters_v2(
        combined,
        {"学历": "硕士,博士"},
    )


def test_action_process_unread_with_fallback_today_processes_today_items_from_all() -> None:
    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "1",
        "--fallback-today",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processedCount"] == 1
    assert payload["fallbackToday"]["switchFilter"]["label"] == "全部"
    assert payload["fallbackToday"]["processedCount"] == 1
    assert payload["fallbackToday"]["processed"][0]["target"]["candidateName"] == "今日已读候选人"


def test_action_process_all_today_switches_to_all_and_processes_today_items() -> None:
    result = run_cli(
        "action",
        "process-all-today",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "2",
        "--min-delay-seconds",
        "0",
        "--max-delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["switchFilter"]["label"] == "全部"
    assert payload["processedCount"] == 2
    assert payload["processed"][0]["target"]["candidateName"] == "华嫣"
    assert payload["processed"][1]["target"]["candidateName"] == "今日已读候选人"


def test_action_process_all_today_can_send_when_allowed() -> None:
    result = run_cli(
        "action",
        "process-all-today",
        "--mock-dir",
        "tests/fixtures",
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "1",
        "--min-delay-seconds",
        "0",
        "--max-delay-seconds",
        "0",
        "--send",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["send"]["sent"] is True


def test_action_process_unread_skips_send_when_conversation_not_ready(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "conversation_ready.json").write_text(
        json.dumps(
            {
                "ready": False,
                "candidateMatched": False,
                "jobMatched": False,
                "hasMessages": False,
                "conversation": {
                    "candidateName": "在线简历",
                    "jobTitle": "",
                    "messages": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        str(fixture_dir / "knowledge"),
        "--max-count",
        "1",
        "--send",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["skipped"] is True
    assert payload["processed"][0]["reason"] == "conversation_not_ready"
    assert payload["processed"][0]["send"] is None


def test_action_process_unread_does_not_send_low_confidence_reply(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "hr_conversation_structured.json").write_text(
        json.dumps(
            {
                "title": "BOSS直聘",
                "url": "https://www.zhipin.com/web/chat/index",
                "candidateName": "华嫣",
                "company": "",
                "role": "",
                "jobTitle": "线上分销（美妆）",
                "salary": "",
                "city": "",
                "messages": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        str(fixture_dir / "knowledge"),
        "--max-count",
        "1",
        "--send",
        "--delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["skipped"] is True
    assert payload["processed"][0]["reason"] == "no_new_reply"
    assert payload["processed"][0]["prepareReply"] is None
    assert payload["processed"][0]["send"] is None


def test_action_process_unread_does_not_send_when_resume_download_fails(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "download_resume.json").write_text(
        json.dumps(
            {
                "clicked": True,
                "downloaded": False,
                "file": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        str(fixture_dir / "knowledge"),
        "--max-count",
        "1",
        "--send",
        "--delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["prepareReply"]["resumeDownload"]["downloaded"] is False
    assert payload["processed"][0]["send"] is None


def test_action_process_unread_does_not_send_duplicate_resume_reply(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "hr_conversation_structured.json").write_text(
        json.dumps(
            {
                "title": "BOSS直聘",
                "url": "https://www.zhipin.com/web/chat/index",
                "candidateName": "兰先生",
                "company": "",
                "role": "",
                "jobTitle": "电商运营经理",
                "salary": "15-20K",
                "city": "深圳",
                "messages": [
                    {"direction": "inbound", "text": "对方想发送附件简历给您，您是否同意\n拒绝\n同意"},
                    {"direction": "system", "text": "您可以在线预览牛人简历， 设置邮箱 后投递的简历会同时发送到您的邮箱。"},
                    {"direction": "inbound", "text": "兰先生简历.pdf\n点击预览附件简历"},
                    {"direction": "outbound", "text": "您好，简历我这边先看一下。我会结合 电商运营经理 的要求做个初步评估，稍后再和您继续沟通。"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        str(fixture_dir / "knowledge"),
        "--max-count",
        "1",
        "--send",
        "--delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["skipped"] is True
    assert payload["processed"][0]["reason"] == "no_new_reply"
    assert payload["processed"][0]["prepareReply"] is None
    assert payload["processed"][0]["send"] is None


def test_action_process_unread_skips_when_no_new_inbound_reply(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixture_dir)
    (fixture_dir / "hr_conversation_structured.json").write_text(
        json.dumps(
            {
                "title": "BOSS直聘",
                "url": "https://www.zhipin.com/web/chat/index",
                "candidateName": "华嫣",
                "company": "",
                "role": "",
                "jobTitle": "线上分销（美妆）",
                "salary": "10-12K",
                "city": "深圳",
                "messages": [
                    {"direction": "inbound", "text": "您好，我对这个岗位很感兴趣。"},
                    {"direction": "outbound", "text": "您好，方便发一份简历吗？"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "action",
        "process-unread",
        "--mock-dir",
        str(fixture_dir),
        "--url-contains",
        "/web/chat/index",
        "--knowledge-dir",
        str(fixture_dir / "knowledge"),
        "--max-count",
        "1",
        "--send",
        "--delay-seconds",
        "0",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["skipped"] is True
    assert payload["processed"][0]["reason"] == "no_new_reply"
    assert payload["processed"][0]["prepareReply"] is None
    assert payload["processed"][0]["send"] is None


def test_capture_recommend_candidates_returns_cards() -> None:
    result = run_cli(
        "capture",
        "recommend-candidates",
        "--mock-dir",
        "tests/fixtures",
        "--max-count",
        "1",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["candidates"][0]["candidateName"] == "周女士"


def test_score_recommendation_returns_greet_decision() -> None:
    result = run_cli(
        "score",
        "recommendation",
        "--mock-dir",
        "tests/fixtures",
        "--job-title",
        "电商运营经理",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["match_score"] >= 70
    assert payload["decision"] == "greet"
    assert payload["greeting"]


def test_action_search_recommendations_applies_filters() -> None:
    result = run_cli(
        "action",
        "search-recommendations",
        "--mock-dir",
        "tests/fixtures",
        "--job-title",
        "电商运营经理",
        "--city",
        "深圳",
        "--filter",
        "学历=本科",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["openRecommendPage"]["opened"] is True
    assert payload["search"]["searched"] is True
    assert payload["search"]["filters"]["学历"] == "本科"


def test_action_process_recommendations_prepares_greeting_without_send() -> None:
    result = run_cli(
        "action",
        "process-recommendations",
        "--mock-dir",
        "tests/fixtures",
        "--job-title",
        "电商运营经理",
        "--city",
        "深圳",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "1",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processedCount"] == 1
    assert payload["processed"][0]["score"]["decision"] == "greet"
    assert payload["processed"][0]["greet"]["prepared"] is True
    assert payload["processed"][0]["greet"]["sent"] is False


def test_action_process_recommendations_can_send_when_allowed() -> None:
    result = run_cli(
        "action",
        "process-recommendations",
        "--mock-dir",
        "tests/fixtures",
        "--job-title",
        "电商运营经理",
        "--city",
        "深圳",
        "--knowledge-dir",
        "tests/fixtures/knowledge",
        "--max-count",
        "1",
        "--send",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"][0]["greet"]["sent"] is True
