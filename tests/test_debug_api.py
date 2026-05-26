import json
import argparse
from pathlib import Path

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.chrome_debug import build_attachment_preview_probe_expression
from boss_agent.chrome_debug import build_apply_recommend_filters_expression
from boss_agent.chrome_debug import build_capture_recommend_candidates_expression_v2
from boss_agent.chrome_debug import build_click_recommend_card_greet_expression
from boss_agent.chrome_debug import build_capture_conversation_expression
from boss_agent.chrome_debug import build_dismiss_recommend_popup_cards_expression
from boss_agent.chrome_debug import build_download_click_probe_expression
from boss_agent.chrome_debug import build_chrome_launch_command
from boss_agent.chrome_debug import classify_page
from boss_agent.cli import _load_screen_recommend_detail_config
from boss_agent.cli import _screen_recommend_detail
from boss_agent.drafting import draft_reply
from boss_agent.knowledge_base import answer_question_from_knowledge
from boss_agent.knowledge_base import load_knowledge_base
from boss_agent.knowledge_base import suggest_follow_up_question
from boss_agent.knowledge_base import summarize_job_from_knowledge
from boss_agent.recommendation import score_recommendation
from boss_agent.scoring import score_conversation


FIXTURES = Path(__file__).parent / "fixtures"


def test_list_pages_uses_json_list_payload() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    pages = client.list_pages()

    assert len(pages) == 2
    assert pages[0]["title"] == "Boss直聘"
    assert pages[0]["type"] == "page"


def test_version_returns_browser_metadata() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    version = client.version()

    assert version["Browser"] == "Chrome/136.0.0.0"
    assert version["Protocol-Version"] == "1.3"


def test_build_chrome_launch_command_uses_remote_debugging_flags() -> None:
    command = build_chrome_launch_command(
        chrome_path=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        user_data_dir=Path(r"D:\BOSS直聘\boss-agent\.chrome-profile"),
        port=9222,
    )

    assert command[0].endswith("chrome.exe")
    assert "--remote-debugging-port=9222" in command
    assert "--remote-allow-origins=*" in command
    assert "--user-data-dir=D:\\BOSS直聘\\boss-agent\\.chrome-profile" in command


def test_classify_page_detects_boss_chat_page() -> None:
    page = {
        "title": "Boss直聘",
        "url": "https://www.zhipin.com/web/geek/chat",
        "type": "page",
    }

    classified = classify_page(page)

    assert classified["platform"] == "boss"
    assert classified["pageType"] == "chat"


def test_capture_text_from_mock_page_returns_visible_text() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    captured = client.capture_visible_text(url_contains="/web/geek/chat")

    assert captured["title"] == "BOSS直聘"
    assert "您好" in captured["text"]
    assert captured["url"].startswith("https://www.zhipin.com/web/geek/chat")


def test_capture_structured_conversation_from_mock_page_returns_chat_fields() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    captured = client.capture_conversation(url_contains="/web/geek/chat")

    assert captured["candidateName"] == "徐先生"
    assert captured["company"] == "欧琳集团有限公司"
    assert captured["role"] == "招聘专员"
    assert captured["jobTitle"] == "AI 产品落地经理"
    assert captured["messages"][0]["text"].startswith("占用您百忙之中")


def test_capture_structured_hr_conversation_from_mock_page_returns_hr_fields() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    captured = client.capture_conversation(url_contains="/web/chat/index")

    assert captured["candidateName"] == "兰先生"
    assert captured["jobTitle"] == "电商运营经理"
    assert captured["salary"] == "15-20K"
    assert captured["city"] == "深圳"
    assert captured["messages"][2]["direction"] == "outbound"
    assert captured["messages"][3]["direction"] == "inbound"


def test_score_conversation_marks_intro_message_as_medium_priority() -> None:
    conversation = json.loads((FIXTURES / "conversation_structured.json").read_text(encoding="utf-8"))

    score = score_conversation(conversation)

    assert score["match_score"] == 70
    assert score["reply_priority"] == "medium"
    assert score["risk_level"] == "low"
    assert "已识别岗位信息" in score["reasons"][0]


def test_score_conversation_marks_not_fit_message_as_close_conversation() -> None:
    conversation = {
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "抱歉应该是投错了，我更适合做直播运营这一块"},
        ],
    }

    score = score_conversation(conversation)

    assert score["recommended_action"] == "close_conversation"


def test_draft_reply_generates_resume_request_message() -> None:
    conversation = json.loads((FIXTURES / "conversation_structured.json").read_text(encoding="utf-8"))
    score = score_conversation(conversation)

    draft = draft_reply(conversation, score)

    assert draft["tone"] == "professional"
    assert draft["intent"] == "continue_conversation"
    assert "您好" in draft["body"]
    assert draft["body"]


def test_draft_reply_for_hr_resume_flow_does_not_request_resume_again() -> None:
    conversation = json.loads((FIXTURES / "hr_conversation_structured.json").read_text(encoding="utf-8"))
    score = score_conversation(conversation)

    draft = draft_reply(conversation, score)

    assert draft["tone"] == "professional"
    assert draft["body"].startswith("您好，")
    assert "简历我这边先看一下" in draft["body"]
    assert draft["follow_up_questions"] == []


def test_draft_reply_for_not_fit_message_does_not_request_resume() -> None:
    conversation = {
        "candidateName": "邬先生",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "抱歉应该是投错了，我更适合做直播运营这一块"},
        ],
    }
    score = score_conversation(conversation)

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "不继续推进这个岗位" in draft["body"]
    assert "简历" not in draft["body"]


def test_draft_reply_answers_candidate_question_before_requesting_resume() -> None:
    conversation = {
        "candidateName": "周先生",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "outbound", "text": "你好，你主要是做什么品类呢？"},
            {"direction": "inbound", "text": "主要做美妆和个护，请问这个岗位目前还在招聘吗？"},
        ],
    }
    score = {
        "recommended_action": "review_and_reply",
    }

    draft = draft_reply(conversation, score)

    assert "岗位目前还在招聘" in draft["body"]
    assert "简历" not in draft["body"]
    assert draft["follow_up_questions"] == []


def test_draft_reply_for_job_interest_message_requests_resume_or_experience() -> None:
    conversation = {
        "candidateName": "油条",
        "jobTitle": "高级天猫运营（美妆）",
        "messages": [
            {"direction": "inbound", "text": "我想应聘贵公司的高级天猫运营（美妆），盼望回复，谢谢！"},
        ],
    }
    score = {
        "recommended_action": "review",
    }

    draft = draft_reply(conversation, score)

    assert "感谢关注 高级天猫运营（美妆）" in draft["body"]
    assert "简历" in draft["body"] or "经验" in draft["body"]
    assert "我先看一下当前沟通内容" not in draft["body"]
    assert "油条" not in draft["body"]


def test_draft_reply_uses_latest_inbound_message_even_if_last_message_is_system() -> None:
    conversation = {
        "candidateName": "油条",
        "jobTitle": "高级天猫运营（美妆）",
        "messages": [
            {"direction": "inbound", "text": "我想应聘贵公司的高级天猫运营（美妆），盼望回复，谢谢！"},
            {"direction": "system", "text": "你撤回了一条消息 重新编辑"},
        ],
    }
    score = {
        "recommended_action": "review",
    }

    draft = draft_reply(conversation, score)

    assert "感谢关注 高级天猫运营（美妆）" in draft["body"]
    assert "我先看一下当前沟通内容" not in draft["body"]
    assert "油条" not in draft["body"]


def test_load_knowledge_base_reads_job_and_company_txt_files() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")

    assert "电商运营经理" in knowledge["jobs"]
    assert knowledge["jobs"]["电商运营经理"]["faq"][0]["question"] == "这个岗位还在招聘吗？"
    assert knowledge["company"]["faq"][0]["question"] == "公司是做什么的？"


def test_answer_question_from_knowledge_matches_job_faq() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")
    conversation = {
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "请问这个岗位还在招聘吗？"},
        ],
    }

    answer = answer_question_from_knowledge(conversation, knowledge)

    assert answer == "这个岗位目前还在招聘。"


def test_answer_question_from_knowledge_requires_exact_job_title_match() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")
    conversation = {
        "jobTitle": "电商运营经理（美妆）",
        "messages": [
            {"direction": "inbound", "text": "请问这个岗位还在招聘吗？"},
        ],
    }

    answer = answer_question_from_knowledge(conversation, knowledge)

    assert answer is None


def test_answer_question_from_knowledge_matches_company_faq() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")
    conversation = {
        "company": "欧琳集团有限公司",
        "messages": [
            {"direction": "inbound", "text": "请问公司是做什么的？"},
        ],
    }

    answer = answer_question_from_knowledge(conversation, knowledge)

    assert answer == "公司目前重点布局电商相关业务，团队在持续扩张。"


def test_answer_question_from_knowledge_matches_company_location_faq() -> None:
    knowledge = load_knowledge_base(Path(r"D:\BOSS直聘\boss-agent\knowledge"))
    conversation = {
        "company": "深圳市依然电商科技有限公司",
        "messages": [
            {"direction": "inbound", "text": "请问公司在哪里？"},
        ],
    }

    answer = answer_question_from_knowledge(conversation, knowledge)

    assert answer == "公司办公地点在深圳南山。"


def test_answer_question_from_knowledge_matches_job_location_faq() -> None:
    knowledge = load_knowledge_base(Path(r"D:\BOSS直聘\boss-agent\knowledge"))
    conversation = {
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "这个岗位工作地点在哪里？"},
        ],
    }

    answer = answer_question_from_knowledge(conversation, knowledge)

    assert answer == "这个岗位工作地点在深圳南山。"


def test_summarize_job_from_knowledge_extracts_first_job_line() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")
    conversation = {
        "jobTitle": "电商运营经理",
    }

    summary = summarize_job_from_knowledge(conversation, knowledge)

    assert summary is not None
    assert "统筹与电商平台" in summary


def test_draft_reply_uses_txt_knowledge_for_job_question() -> None:
    conversation = {
        "candidateName": "周先生",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "请问这个岗位还在招聘吗？"},
        ],
    }
    score = {"recommended_action": "review_and_reply"}

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "这个岗位目前还在招聘" in draft["body"]
    assert draft["follow_up_questions"] == []


def test_draft_reply_uses_job_knowledge_for_interest_message() -> None:
    conversation = {
        "candidateName": "张先生",
        "jobTitle": "线上分销（美妆）",
        "messages": [
            {"direction": "inbound", "text": "您好，我对这个岗位很感兴趣，想进一步了解一下。"},
        ],
    }
    score = {"recommended_action": "request_resume_or_experience"}

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "这个岗位目前主要是" in draft["body"]
    assert "负责公司美妆产品" in draft["body"]
    assert "分销渠道" in draft["body"] or "客户类型" in draft["body"]


def test_draft_reply_for_experience_message_reuses_candidate_details() -> None:
    conversation = {
        "candidateName": "邬先生",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "做过达播账号徐嘿嘿，0-1孵化起来的"},
        ],
    }
    score = {"recommended_action": "continue_conversation"}

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "看到您提到做过达播账号徐嘿嘿，0-1孵化起来的" in draft["body"]
    assert "您提到的相关经历和" not in draft["body"]
    assert "平台" in draft["body"] or "类目" in draft["body"]


def test_draft_reply_for_long_experience_message_shortens_summary() -> None:
    conversation = {
        "candidateName": "孙蔚",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "12年电商全链路实战经验，现任电商运营总监，操盘随身WiFi品类京东天猫拼多多抖音全平台业务，带领团队从0到1实现年销超5000万元"},
        ],
    }
    score = {"recommended_action": "continue_conversation"}

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "看到您提到12年电商全链路实战经验" in draft["body"]
    assert "等经历" in draft["body"]
    assert "平台" in draft["body"] or "类目" in draft["body"]


def test_suggest_follow_up_question_prefers_job_gap_from_knowledge() -> None:
    knowledge = load_knowledge_base(FIXTURES / "knowledge")
    conversation = {
        "jobTitle": "线上分销（美妆）",
        "messages": [
            {"direction": "inbound", "text": "您好，我对这个岗位很感兴趣，想进一步了解一下。"},
        ],
    }

    question = suggest_follow_up_question(conversation, knowledge)

    assert question is not None
    assert "分销渠道" in question or "客户类型" in question


def test_draft_reply_for_job_question_can_answer_then_follow_up_with_knowledge_gap() -> None:
    conversation = {
        "candidateName": "周先生",
        "jobTitle": "电商运营经理",
        "messages": [
            {"direction": "inbound", "text": "请问这个岗位还在招聘吗？"},
        ],
    }
    score = {"recommended_action": "answer_question"}

    draft = draft_reply(conversation, score, knowledge_dir=FIXTURES / "knowledge")

    assert "这个岗位目前还在招聘" in draft["body"]
    assert "平台" in draft["body"] or "类目" in draft["body"] or "渠道" in draft["body"]


def test_fill_chat_input_mock_returns_selector_and_body() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.fill_chat_input(
        body="测试消息",
        url_contains="/web/geek/chat",
    )

    assert result["filled"] is True
    assert result["selector"] == ".chat-input"
    assert result["body"] == "测试消息"


def test_click_resume_consent_mock_returns_click_result() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.click_resume_consent(url_contains="/web/chat/index")

    assert result["clicked"] is True
    assert result["selector"] == ".op a.btn"
    assert result["text"] == "同意"


def test_detect_resume_consent_prompt_from_mock_returns_visible_prompt() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.detect_resume_consent_prompt(url_contains="/web/chat/index")

    assert result["visible"] is True
    assert result["hasAgreeButton"] is True
    assert result["hasRejectButton"] is True


def test_capture_attachment_preview_link_from_mock_returns_preview_url() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_attachment_preview_link(url_contains="/web/chat/index")

    assert result["type"] == "attachment_preview"
    assert "preview4boss" in result["previewUrl"]


def test_capture_unread_inbox_from_mock_returns_unread_conversations() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_unread_inbox(url_contains="/web/chat/index")

    assert result["activeFilter"] == "未读"
    assert result["count"] == 3
    assert result["conversations"][0]["candidateName"] == "华嫣"
    assert result["conversations"][0]["unreadCount"] == "2"


def test_capture_unread_inbox_scrolled_from_mock_marks_scroll_capture() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_unread_inbox_scrolled(
        url_contains="/web/chat/index",
        max_items=100,
        max_scrolls=12,
    )

    assert result["count"] == 3
    assert result["scrollCapture"]["enabled"] is True
    assert result["scrollCapture"]["maxItems"] == 100
    assert result["scrollCapture"]["maxScrolls"] == 12


def test_capture_job_filter_options_from_mock_returns_jobs() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_job_filter_options(url_contains="/web/chat/index")

    assert result["selected"] == "全部职位"
    assert "文员 _ 珠海 5-6K" in result["options"]


def test_switch_job_filter_from_mock_returns_selected_job() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.switch_job_filter(job_text="销售 _ 广州 8-12K", url_contains="/web/chat/index")

    assert result["switched"] is True
    assert result["selected"] == "销售 _ 广州 8-12K"


def test_capture_recommend_resume_cards_from_mock_returns_cards() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_recommend_resume_cards(url_contains="/web/chat/recommend")

    assert result["count"] > 0
    assert result["cards"][0]["name"]


def test_open_conversation_from_mock_returns_selected_conversation() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.open_conversation(data_id="5682470-0", url_contains="/web/chat/index")

    assert result["opened"] is True
    assert result["dataId"] == "5682470-0"
    assert result["candidateName"] == "兰先生"


def test_click_download_icon_mock_returns_downloaded_file() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.download_resume(
        url_contains="/web/chat/index",
        downloads_dir=Path(r"C:\Users\华为\Downloads"),
    )

    assert result["clicked"] is True
    assert result["downloaded"] is True
    assert result["file"].endswith(".docx")


def test_find_latest_downloaded_file_prefers_most_recent_matching_file(tmp_path: Path) -> None:
    older = tmp_path / "old.docx"
    newer = tmp_path / "new.docx"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")

    result = ChromeDebugClient(endpoint="http://127.0.0.1:9222").find_latest_downloaded_file(
        downloads_dir=tmp_path,
        not_before=older.stat().st_mtime - 1,
    )

    assert result == newer


def test_build_download_click_probe_expression_targets_download_icon() -> None:
    expression = build_download_click_probe_expression()

    assert "#icon-attacthment-download" in expression
    assert "getBoundingClientRect" in expression


def test_build_capture_conversation_expression_supports_hr_page_parsing() -> None:
    expression = build_capture_conversation_expression()

    assert "/web/chat/index" in expression
    assert ".chat-conversation" in expression
    assert ".item-myself" in expression


def test_build_attachment_preview_probe_expression_supports_existing_overlay_or_preview_button() -> None:
    expression = build_attachment_preview_probe_expression()

    assert "点击预览附件简历" in expression
    assert "resume-common-dialog" in expression


def test_capture_recommend_candidates_from_mock_returns_candidate_cards() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    payload = client.capture_recommend_candidates(max_count=1, url_contains="/web/boss/recommend")

    assert payload["count"] == 1
    assert payload["candidates"][0]["candidateName"] == "周女士"
    assert payload["candidates"][0]["expectedCity"] == "深圳"


def test_capture_candidate_resume_from_mock_returns_summary_fields() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    resume = client.capture_candidate_resume(url_contains="/web/boss/recommend")

    assert resume["name"] == "周女士"
    assert resume["expectedTitle"] == "电商运营经理"
    assert "天猫" in resume["rawTextPreview"]


def test_clean_ocr_text_removes_spaces_between_chinese_chars() -> None:
    text = ChromeDebugClient._clean_ocr_text(
        "\u5de5 \u4f5c \u7ecf \u5386\n"
        "\u6df1 \u5733 \u6bdb \u53cb \u8bb0 \u5ba0 \u7269 \u7528 \u54c1 \u6709 \u9650 \u516c \u53f8"
    )

    assert "\u5de5\u4f5c\u7ecf\u5386" in text
    assert "\u6df1\u5733\u6bdb\u53cb\u8bb0\u5ba0\u7269\u7528\u54c1\u6709\u9650\u516c\u53f8" in text


def test_augment_resume_with_canvas_ocr_merges_ocr_text() -> None:
    class OcrClient(ChromeDebugClient):
        def _capture_recommend_canvas_ocr(self, websocket_url: str) -> dict:
            return {
                "status": "ok",
                "text": "\u5de5\u4f5c\u7ecf\u5386\n\u6df1\u5733\u6bdb\u53cb\u8bb0\u5ba0\u7269\u7528\u54c1\u6709\u9650\u516c\u53f8 \u8fd0\u8425\u7ecf\u7406/\u4e3b\u7ba1",
                "sections": {
                    "\u5de5\u4f5c\u7ecf\u5386": "\u5de5\u4f5c\u7ecf\u5386\n\u6df1\u5733\u6bdb\u53cb\u8bb0\u5ba0\u7269\u7528\u54c1\u6709\u9650\u516c\u53f8 \u8fd0\u8425\u7ecf\u7406/\u4e3b\u7ba1"
                },
            }

    client = OcrClient(endpoint="http://127.0.0.1:9222")
    resume = client._augment_resume_with_canvas_ocr(
        "ws://example",
        {"detailOpen": True, "rawTextPreview": "\u9ec4\u6e58\n\u4e2a\u4eba\u4f18\u52bf"},
    )

    assert resume["resumeSource"] == "detail+canvas_ocr"
    assert resume["ocrStatus"] == "ok"
    assert "\u4e2a\u4eba\u4f18\u52bf" in resume["rawTextPreview"]
    assert "\u6df1\u5733\u6bdb\u53cb\u8bb0\u5ba0\u7269\u7528\u54c1\u6709\u9650\u516c\u53f8" in resume["rawTextPreview"]


def test_score_recommendation_matches_job_knowledge() -> None:
    resume = json.loads((FIXTURES / "candidate_resume.json").read_text(encoding="utf-8"))

    score = score_recommendation(
        resume,
        job_title="电商运营经理",
        knowledge_dir=FIXTURES / "knowledge",
    )

    assert score["match_score"] >= 70
    assert score["decision"] == "greet"
    assert "电商运营经理" in score["greeting"]


def test_score_recommendation_skips_weak_match() -> None:
    resume = {
        "name": "李先生",
        "expectedTitle": "直播运营",
        "expectedCity": "广州",
        "yearsOfExperience": "1年",
        "education": "大专",
        "rawTextPreview": "主要做主播排班和直播间场控",
    }

    score = score_recommendation(
        resume,
        job_title="电商运营经理",
        knowledge_dir=FIXTURES / "knowledge",
    )

    assert score["match_score"] < 70
    assert score["decision"] in {"skip", "review"}


def test_capture_recommend_job_filter_options_from_mock_returns_jobs() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.capture_recommend_job_filter_options(url_contains="/web/chat/recommend")

    assert result["selected"]
    assert result["options"]


def test_switch_recommend_job_filter_from_mock_returns_selected_job() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.switch_recommend_job_filter(
        job_text="鐢靛晢杩愯惀缁忕悊锛堢編濡嗭級",
        url_contains="/web/chat/recommend",
    )

    assert result["switched"] is True
    assert result["selected"] == "鐢靛晢杩愯惀缁忕悊锛堢編濡嗭級"


def test_apply_recommend_filters_supports_salary_alias_and_case_insensitive_value() -> None:
    expression = build_apply_recommend_filters_expression(
        json.dumps(""),
        json.dumps(""),
        json.dumps({"\u85aa\u8d44": "10-20k"}, ensure_ascii=False),
    )

    assert "\u85aa\u8d44\u5f85\u9047[\u5355\u9009]" in expression
    assert "normalizedValue" in expression
    assert ".toLowerCase()" in expression


def test_apply_recommend_filters_confirms_once_after_all_filters() -> None:
    expression = build_apply_recommend_filters_expression(
        json.dumps(""),
        json.dumps(""),
        json.dumps(
            {"\u5b66\u5386": "\u672c\u79d1", "\u5e74\u9f84": "24-31", "\u85aa\u8d44": "10-20k"},
            ensure_ascii=False,
        ),
    )

    age_section = expression[
        expression.index("const applyAgeFilter = async")
        : expression.index("const captureFilterEcho = () =>")
    ]

    assert "clickElement(confirmButton)" not in age_section
    assert expression.count("clickElement(confirmButton)") == 1
    assert "unverifiedFilters" in expression
    assert "verification" in expression


def test_apply_recommend_filters_tracks_verified_tasks_and_clicked_filters() -> None:
    expression = build_apply_recommend_filters_expression(
        json.dumps(""),
        json.dumps(""),
        json.dumps(
            {"学历": "本科", "薪资": "10-20k", "经验要求": "3-5年"},
            ensure_ascii=False,
        ),
    )

    assert "filterTasks" in expression
    assert "scanPanelCapabilities" in expression
    assert "similarPanelItems" in expression
    assert "clickedFilters: appliedFilters" in expression
    assert "appliedFilters: verifiedAppliedFilters" in expression
    assert "panelScanBefore" in expression
    assert "panelScanAfter" in expression


def test_apply_recommend_filters_mock_returns_task_evidence() -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = client.apply_recommend_filters(
        job_title="",
        city="",
        filters={"薪资": "10-20k", "经验": "3-5年"},
        url_contains="/web/chat/recommend",
    )

    assert result["filterVerificationPassed"] is True
    assert result["clickedFilters"] == ["薪资", "经验"]
    assert result["appliedFilters"] == ["薪资", "经验"]
    assert result["filterTasks"][0]["normalizedKey"] == "薪资待遇"
    assert result["filterTasks"][0]["status"] == "verified"
    assert result["filterTasks"][0]["evidence"] == ["mock"]


def _age_slider_state(left: int, right: int) -> dict:
    return {
        "frameUrl": "mock",
        "rail": {"x": 100, "y": 50, "w": 300, "left": 100, "right": 400},
        "dots": [
            {"x": 100 + (left - 16) * 10, "y": 50, "label": str(left), "value": left},
            {"x": 100 + (right - 16) * 10, "y": 50, "label": str(right), "value": right},
        ],
    }


def _age_slider_unlimited_state(left: int, right: int | None) -> dict:
    right_label = "\u4e0d\u9650" if right is None else str(right)
    right_x = 400 if right is None else 100 + (right - 16) * 10
    return {
        "frameUrl": "mock",
        "rail": {"x": 100, "y": 50, "w": 300, "left": 100, "right": 400},
        "dots": [
            {"x": 100 + (left - 16) * 10, "y": 50, "label": str(left), "value": left},
            {
                "x": right_x,
                "y": 50,
                "label": right_label,
                "value": right,
                "isUnlimited": right is None,
            },
        ],
    }


def test_apply_recommend_age_slider_closed_loop_reaches_target() -> None:
    class AgeClient(ChromeDebugClient):
        def __init__(self) -> None:
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.states = [
                _age_slider_state(27, 31),
                _age_slider_state(27, 31),
                _age_slider_state(27, 31),
                _age_slider_state(27, 31),
                _age_slider_state(26, 31),
                _age_slider_state(26, 31),
                _age_slider_state(25, 31),
                _age_slider_state(25, 31),
            ]

        def _recommend_filter_panel_state(self, websocket_url: str) -> dict:
            return {"panelOpen": True, "confirmButton": None}

        def _read_age_values(self, websocket_url: str) -> dict:
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]

        def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
            return None

    result = AgeClient()._apply_recommend_age_slider("ws://mock", "25-31", confirm=False)

    assert result["verified"] is True
    assert result["actual"] == ["25", "31"]
    assert len(result["ageAttempts"]) >= 3
    assert result["ageAttempts"][-1]["hit"] is True


def test_apply_recommend_age_slider_reports_unverified_when_stuck() -> None:
    class StuckAgeClient(ChromeDebugClient):
        def __init__(self) -> None:
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.drag_count = 0

        def _recommend_filter_panel_state(self, websocket_url: str) -> dict:
            return {"panelOpen": True, "confirmButton": None}

        def _read_age_values(self, websocket_url: str) -> dict:
            return _age_slider_state(27, 31)

        def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
            self.drag_count += 1

    client = StuckAgeClient()
    result = client._apply_recommend_age_slider("ws://mock", "25-31", confirm=False)

    assert result["verified"] is False
    assert result["actual"] == ["27", "31"]
    assert result["reason"].startswith("年龄左滑块目标25")
    assert client.drag_count == 8


def test_apply_recommend_age_slider_drags_right_handle_from_unlimited() -> None:
    class UnlimitedAgeClient(ChromeDebugClient):
        def __init__(self) -> None:
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.states = [
                _age_slider_unlimited_state(25, None),
                _age_slider_unlimited_state(25, None),
                _age_slider_unlimited_state(25, None),
                _age_slider_unlimited_state(25, 35),
                _age_slider_unlimited_state(25, 35),
                _age_slider_unlimited_state(25, 31),
                _age_slider_unlimited_state(25, 31),
            ]

        def _recommend_filter_panel_state(self, websocket_url: str) -> dict:
            return {"panelOpen": True, "confirmButton": None}

        def _read_age_values(self, websocket_url: str) -> dict:
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]

        def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
            return None

    result = UnlimitedAgeClient()._apply_recommend_age_slider("ws://mock", "25-31", confirm=False)

    assert result["verified"] is True
    assert result["actual"] == ["25", "31"]
    assert any(item["phase"] == "leave_unlimited" for item in result["ageAttempts"])


def test_drag_age_handle_treats_chinese_unlimited_as_draggable() -> None:
    class ChineseUnlimitedAgeClient(ChromeDebugClient):
        def __init__(self) -> None:
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.drag_count = 0

        def _read_age_values(self, websocket_url: str) -> dict:
            return _age_slider_unlimited_state(28, None)

        def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
            self.drag_count += 1

    client = ChineseUnlimitedAgeClient()
    result = client._drag_age_handle_until_value("ws://mock", 1, 31, max_attempts=1)

    assert result["reason"] == "age_unlimited_not_resolved"
    assert result["attempts"][0]["phase"] == "leave_unlimited"
    assert result["attempts"][0]["beforeLabel"] == "\u4e0d\u9650"
    assert client.drag_count == 1


def test_apply_recommend_age_slider_reports_when_unlimited_stays_unlimited() -> None:
    class StuckUnlimitedAgeClient(ChromeDebugClient):
        def __init__(self) -> None:
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.drag_count = 0

        def _recommend_filter_panel_state(self, websocket_url: str) -> dict:
            return {"panelOpen": True, "confirmButton": None}

        def _read_age_values(self, websocket_url: str) -> dict:
            return _age_slider_unlimited_state(25, None)

        def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
            self.drag_count += 1

    client = StuckUnlimitedAgeClient()
    result = client._apply_recommend_age_slider("ws://mock", "25-31", confirm=False)

    assert result["verified"] is False
    assert result["actual"] == ["25", "\u4e0d\u9650"]
    assert "\u4ece\u4e0d\u9650\u5411\u5de6\u62d6\u52a8" in result["reason"]
    assert client.drag_count == 8


def test_click_recommend_card_greet_expression_targets_card_button() -> None:
    expression = build_click_recommend_card_greet_expression(
        escaped_candidate_id=json.dumps("abc", ensure_ascii=False),
        card_index=2,
    )

    assert "button.btn-greet" in expression
    assert "cardIndex = 2" in expression
    assert "card_greet_button_not_found" in expression
    assert "card_dom_click" in expression


def test_dismiss_recommend_popup_cards_expression_skips_similar_recommend_cards() -> None:
    expression = build_dismiss_recommend_popup_cards_expression()

    assert "li.card-item" in expression
    assert "div.title" in expression
    assert "div.geek-card" in expression
    assert ("\u4e3a\u4f60\u63a8\u8350" in expression) or ("\\u4e3a\\u4f60\\u63a8\\u8350" in expression)
    assert ("\u76f8\u4f3c" in expression) or ("\\u76f8\\u4f3c" in expression)
    assert "i.close.iboss-close" in expression
    assert "similar_recommend_close_click" in expression
    assert "closedCount" in expression
    assert "similar_recommend_close_button_not_found" in expression
    assert "scroll_past_similar_recommend_cards" not in expression
    assert "nextMainCard" not in expression
    assert "scrollDelta" not in expression
    assert "maxScrollDelta" not in expression
    assert "KeyboardEvent" not in expression


def test_recommend_candidate_expressions_exclude_similar_recommend_cards() -> None:
    capture_expression = build_capture_recommend_candidates_expression_v2(max_count=5)
    greet_expression = build_click_recommend_card_greet_expression(
        escaped_candidate_id=json.dumps("abc", ensure_ascii=False),
        card_index=2,
    )

    for expression in (capture_expression, greet_expression):
        assert "isSimilarRecommendItem" in expression
        assert "li.card-item" in expression
        assert "div.title" in expression
        assert "div.geek-card" in expression
        assert ("\u4e3a\u4f60\u63a8\u8350" in expression) or ("\\u4e3a\\u4f60\\u63a8\\u8350" in expression)
        assert ("\u76f8\u4f3c" in expression) or ("\\u76f8\\u4f3c" in expression)


def test_screen_recommend_detail_config_target_resumes_sets_max_checks(tmp_path: Path) -> None:
    config_path = tmp_path / "recommend_detail.json"
    config_path.write_text(
        json.dumps(
            {
                "criteria": "本科",
                "targetResumes": 7,
                "maxChecks": 99,
                "filters": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        config=str(config_path),
        endpoint="http://127.0.0.1:9222",
        mock_dir=None,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filter=[],
        criteria=None,
        model=None,
        max_greetings=30,
        max_checks=200,
        target_resumes=None,
        post_filter_wait=5.0,
        operation_delay_seconds=None,
        max_filter_retries=None,
        report_path=str(Path.cwd() / "reports" / "recommend_detail_greeted.xlsx"),
        dry_run=False,
    )

    loaded = _load_screen_recommend_detail_config(args)

    assert loaded["max_checks"] == 7


def test_screen_recommend_detail_config_operation_delay_supports_cli_override(tmp_path: Path) -> None:
    config_path = tmp_path / "recommend_detail.json"
    config_path.write_text(
        json.dumps(
            {
                "criteria": "\u5929\u732b",
                "operationDelaySeconds": 2,
                "maxGreetings": 5,
                "filters": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        config=str(config_path),
        endpoint="http://127.0.0.1:9222",
        mock_dir=None,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filter=[],
        criteria=None,
        model=None,
        max_greetings=None,
        max_checks=200,
        target_resumes=None,
        post_filter_wait=5.0,
        operation_delay_seconds=1.25,
        max_filter_retries=None,
        report_path=str(Path.cwd() / "reports" / "recommend_detail_greeted.xlsx"),
        dry_run=False,
    )

    loaded = _load_screen_recommend_detail_config(args)

    assert loaded["operation_delay_seconds"] == 1.25
    assert loaded["max_greetings"] == 5


def test_screen_recommend_detail_mock_greets_matches_and_reports_all(tmp_path: Path) -> None:
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )
    report_path = tmp_path / "recommend_detail.csv"

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="鐢靛晢杩愯惀缁忕悊锛堢編濡嗭級",
        city="",
        filters={"瀛﹀巻": "鏈"},
        criteria="本科",
        model=None,
        max_greetings=30,
        max_checks=10,
        post_filter_wait_seconds=0,
        report_path=report_path,
        dry_run=False,
    )

    assert result["checkedCount"] == 2
    assert result["matchedCount"] == 1
    assert result["greetedCount"] == 1
    assert result["report"]["rowCount"] == 2
    assert report_path.exists()


def test_screen_recommend_detail_closes_previous_detail_before_each_open(tmp_path: Path) -> None:
    class TrackingClient(ChromeDebugClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events: list[str] = []

        def close_recommend_detail(self, url_contains="/web/chat/recommend") -> dict:
            self.events.append("close")
            return {"closed": True, "verifiedClosed": True}

        def open_recommend_candidate(self, candidate_id=None, card_index=None, url_contains="/web/chat/recommend") -> dict:
            self.events.append("open")
            return super().open_recommend_candidate(candidate_id, card_index, url_contains)

    client = TrackingClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filters={},
        criteria="鏈",
        model=None,
        max_greetings=30,
        max_checks=2,
        post_filter_wait_seconds=0,
        report_path=tmp_path / "recommend_detail.csv",
        dry_run=False,
    )

    assert result["checkedCount"] == 2
    open_indexes = [index for index, event in enumerate(client.events) if event == "open"]
    assert len(open_indexes) == 2
    assert all(index > 0 and client.events[index - 1] == "close" for index in open_indexes)
    assert all(item["preCloseDetail"]["closed"] for item in result["processed"])


def test_screen_recommend_detail_applies_operation_delay_between_page_actions(tmp_path: Path, monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("boss_agent.cli.time.sleep", lambda seconds: sleeps.append(seconds))
    client = ChromeDebugClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filters={},
        criteria="鏈",
        model=None,
        max_greetings=30,
        max_checks=1,
        post_filter_wait_seconds=0,
        report_path=tmp_path / "recommend_detail.csv",
        dry_run=False,
        operation_delay_seconds=2,
    )

    assert result["checkedCount"] == 1
    assert result["operationDelaySeconds"] == 2
    assert 2 in sleeps
    assert sleeps.count(2) >= 5


def test_screen_recommend_detail_does_not_analyze_stale_detail(tmp_path: Path) -> None:
    class StaleDetailClient(ChromeDebugClient):
        def close_recommend_detail(self, url_contains="/web/chat/recommend") -> dict:
            return {
                "closed": True,
                "verifiedClosed": False,
                "before": {"textPreview": "old resume detail"},
            }

        def capture_candidate_resume(self, url_contains=None) -> dict:
            return {
                "detailOpen": True,
                "rawTextPreview": "old resume detail still visible",
            }

    client = StaleDetailClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filters={},
        criteria="鏈",
        model=None,
        max_greetings=30,
        max_checks=1,
        post_filter_wait_seconds=0,
        report_path=tmp_path / "recommend_detail.csv",
        dry_run=False,
    )

    assert result["checkedCount"] == 1
    assert result["processed"][0]["reason"] == "resume_detail_stale"
    assert result["processed"][0]["analysis"] == {}


def test_screen_recommend_detail_does_not_analyze_when_detail_resume_not_open(tmp_path: Path) -> None:
    class DetailClosedClient(ChromeDebugClient):
        def capture_candidate_resume(self, url_contains=None) -> dict:
            return {
                "detailOpen": False,
                "rawTextPreview": "card text should not be analyzed even if it mentions 本科 天猫 美妆 3年",
            }

    client = DetailClosedClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filters={},
        criteria="本科 天猫 美妆 3年",
        model=None,
        max_greetings=30,
        max_checks=1,
        post_filter_wait_seconds=0,
        report_path=tmp_path / "recommend_detail.csv",
        dry_run=False,
    )

    assert result["checkedCount"] == 1
    assert result["matchedCount"] == 0
    assert result["greetedCount"] == 0
    assert result["processed"][0]["reason"] == "resume_detail_not_open"
    assert result["processed"][0]["analysis"] == {}


def test_screen_recommend_detail_stops_when_filter_verification_fails(tmp_path: Path) -> None:
    class FilterFailedClient(ChromeDebugClient):
        def apply_recommend_filters(
            self,
            job_title="",
            city="",
            filters=None,
            url_contains="/web/chat/recommend",
            operation_delay_seconds=0,
            max_filter_retries=2,
        ) -> dict:
            return {
                "filters": filters or {},
                "appliedFilters": list((filters or {}).keys()),
                "missingFilters": ["\u5e74\u9f84"],
                "unverifiedFilters": ["\u5e74\u9f84"],
                "filterVerificationPassed": False,
            }

        def capture_recommend_candidates(self, max_count=20, url_contains="/web/chat/recommend") -> dict:
            raise AssertionError("candidate capture should not run after filter verification failure")

    client = FilterFailedClient(
        endpoint="http://127.0.0.1:9222",
        mock_dir=FIXTURES,
    )

    result = _screen_recommend_detail(
        client=client,
        url_contains="/web/chat/recommend",
        job_title="",
        city="",
        filters={"\u5b66\u5386": "\u672c\u79d1", "\u5e74\u9f84": "24-31"},
        criteria="\u62db\u8058",
        model=None,
        max_greetings=30,
        max_checks=3,
        post_filter_wait_seconds=0,
        report_path=tmp_path / "recommend_detail.csv",
        dry_run=False,
    )

    assert result["reason"] == "filter_verification_failed"
    assert result["checkedCount"] == 0
    assert result["search"]["unverifiedFilters"] == ["\u5e74\u9f84"]


def test_apply_recommend_filters_retries_only_failed_filter() -> None:
    class PartialRetryClient(ChromeDebugClient):
        def __init__(self):
            super().__init__(endpoint="http://127.0.0.1:9222")
            self.age_attempts = 0
            self.expressions: list[str] = []

        def list_pages(self) -> list[dict]:
            return [{"webSocketDebuggerUrl": "ws://mock", "url": "https://www.zhipin.com/web/chat/recommend"}]

        def _evaluate_json(self, websocket_url: str, expression: str, timeout: float = 5.0) -> dict:
            self.expressions.append(expression)
            has_salary = "10-20k" in expression
            verification = {}
            filter_tasks = []
            if has_salary:
                verification["\u85aa\u8d44"] = {
                    "applied": True,
                    "verified": True,
                    "expected": "10-20k",
                    "actual": ["10-20k"],
                }
                filter_tasks.append({"rawKey": "\u85aa\u8d44", "status": "verified", "verified": True})
            return {
                "searched": False,
                "skippedSearch": False,
                "applied": {"jobTitle": {"applied": False}, "city": {"applied": False}},
                "specialFilters": {},
                "filterTasks": filter_tasks,
                "clickedFilters": ["\u85aa\u8d44"] if has_salary else [],
                "appliedFilters": ["\u85aa\u8d44"] if has_salary else [],
                "missingFilters": [],
                "verification": verification,
                "unverifiedFilters": [],
                "needsConfirm": False,
                "confirmed": False,
            }

        def _apply_recommend_age_slider(self, websocket_url: str, raw_value: str, confirm: bool = True, operation_delay_seconds: float = 0) -> dict:
            self.age_attempts += 1
            verified = self.age_attempts == 2
            return {
                "applied": True,
                "method": "cdp_slider",
                "actual": ["28", "35" if verified else "\u4e0d\u9650"],
                "verified": verified,
            }

        def _verify_recommend_result_filters(self, websocket_url: str, filters: dict[str, str]) -> dict:
            return {"failedFilters": []}

    client = PartialRetryClient()

    result = client.apply_recommend_filters(
        job_title="",
        city="",
        filters={"\u85aa\u8d44": "10-20k", "\u5e74\u9f84": "28-35"},
        url_contains="/web/chat/recommend",
        max_filter_retries=2,
    )

    assert result["filterVerificationPassed"] is True
    assert result["appliedFilters"] == ["\u85aa\u8d44", "\u5e74\u9f84"]
    assert result["retryHistory"] == [{"attempt": 2, "filters": {"\u5e74\u9f84": "28-35"}}]
    assert client.age_attempts == 2
    assert "10-20k" in client.expressions[0]
    assert "10-20k" not in client.expressions[1]


def test_apply_recommend_filters_keeps_failed_filter_after_max_retries() -> None:
    base = {
        "filters": {"\u85aa\u8d44": "10-20k", "\u5e74\u9f84": "28-35"},
        "appliedFilters": ["\u85aa\u8d44"],
        "clickedFilters": ["\u85aa\u8d44", "\u5e74\u9f84"],
        "missingFilters": ["\u5e74\u9f84"],
        "verification": {
            "\u85aa\u8d44": {"verified": True},
            "\u5e74\u9f84": {"verified": False},
        },
        "filterTasks": [
            {"rawKey": "\u85aa\u8d44", "status": "verified"},
            {"rawKey": "\u5e74\u9f84", "status": "unverified"},
        ],
        "specialFilters": {"\u85aa\u8d44": {"verified": True}, "\u5e74\u9f84": {"verified": False}},
    }
    retry = {
        "filters": {"\u5e74\u9f84": "28-35"},
        "appliedFilters": [],
        "clickedFilters": ["\u5e74\u9f84"],
        "missingFilters": ["\u5e74\u9f84"],
        "unverifiedFilters": ["\u5e74\u9f84"],
        "verification": {"\u5e74\u9f84": {"verified": False}},
        "filterTasks": [{"rawKey": "\u5e74\u9f84", "status": "unverified"}],
        "specialFilters": {"\u5e74\u9f84": {"verified": False}},
        "retryHistory": [],
        "retryCount": 1,
    }

    result = ChromeDebugClient._merge_recommend_filter_retry_payloads(
        base=base,
        retry=retry,
        raw_filters={"\u85aa\u8d44": "10-20k", "\u5e74\u9f84": "28-35"},
        retried_filters={"\u5e74\u9f84": "28-35"},
        attempt=2,
    )

    assert result["filterVerificationPassed"] is False
    assert result["unverifiedFilters"] == ["\u5e74\u9f84"]
    assert result["missingFilters"] == ["\u5e74\u9f84"]
    assert result["retryHistory"] == [{"attempt": 2, "filters": {"\u5e74\u9f84": "28-35"}}]


def test_merge_ocr_segment_texts_deduplicates_repeated_lines() -> None:
    merged = ChromeDebugClient._merge_ocr_segment_texts(
        [
            "\u5de5\u4f5c\u7ecf\u5386\nA\u516c\u53f8 \u5929\u732b\u8fd0\u8425\n\u8d1f\u8d23\u5e97\u94fa\u589e\u957f",
            "\u5de5\u4f5c\u7ecf\u5386\n\u8d1f\u8d23\u5e97\u94fa\u589e\u957f\nB\u516c\u53f8 \u7535\u5546\u8fd0\u8425",
        ]
    )

    assert merged.count("\u5de5\u4f5c\u7ecf\u5386") == 1
    assert merged.count("\u8d1f\u8d23\u5e97\u94fa\u589e\u957f") == 1
    assert "A\u516c\u53f8 \u5929\u732b\u8fd0\u8425" in merged
    assert "B\u516c\u53f8 \u7535\u5546\u8fd0\u8425" in merged


def test_capture_recommend_canvas_ocr_scrolled_keeps_successful_segments(monkeypatch) -> None:
    client = ChromeDebugClient(endpoint="http://127.0.0.1:9222")

    def fake_find_tesseract() -> str:
        return "tesseract"

    def fake_evaluate_json(websocket_url, expression, timeout=10):
        return {
            "found": True,
            "scrollContainer": {"positions": [0, 500, 1000]},
            "segments": [
                {"dataUrl": "data:image/png;base64,aaa", "scrollTop": 0, "width": 10, "height": 10},
                {"dataUrl": "", "scrollTop": 500, "width": 10, "height": 10},
                {"dataUrl": "data:image/png;base64,bbb", "scrollTop": 1000, "width": 10, "height": 10},
            ],
        }

    def fake_segment(canvas, tesseract):
        if canvas.get("scrollTop") == 500:
            return {"status": "ocr_empty", "scrollTop": 500}
        return {
            "status": "ok",
            "scrollTop": canvas.get("scrollTop"),
            "text": f"\u5de5\u4f5c\u7ecf\u5386\n{canvas.get('scrollTop')}\u6bb5\u5929\u732b\u8fd0\u8425",
        }

    monkeypatch.setattr(client, "_find_tesseract", fake_find_tesseract)
    monkeypatch.setattr(client, "_evaluate_json", fake_evaluate_json)
    monkeypatch.setattr(client, "_ocr_canvas_data_url_segment", fake_segment)

    result = client._capture_recommend_canvas_ocr("ws://mock")

    assert result["status"] == "partial"
    assert result["resumeSource"] == "detail+canvas_ocr_scrolled"
    assert result["segmentCount"] == 3
    assert result["successfulSegmentCount"] == 2
    assert "0\u6bb5\u5929\u732b\u8fd0\u8425" in result["text"]
    assert "1000\u6bb5\u5929\u732b\u8fd0\u8425" in result["text"]
