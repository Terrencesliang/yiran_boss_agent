from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent.knowledge_base import answer_question_from_knowledge
from boss_agent.knowledge_base import load_knowledge_base
from boss_agent.knowledge_base import suggest_follow_up_question
from boss_agent.knowledge_base import summarize_job_from_knowledge


def draft_reply(
    conversation: dict[str, Any],
    score: dict[str, Any],
    knowledge_dir: Path | None = None,
) -> dict[str, Any]:
    salutation = "您好"
    job_title = conversation.get("jobTitle") or "这个岗位"
    messages = conversation.get("messages", [])
    message_texts = [message.get("text", "") for message in messages]
    latest_message = message_texts[-1] if message_texts else ""
    latest_direction = messages[-1].get("direction", "") if messages else ""
    latest_inbound_message = next(
        (message.get("text", "") for message in reversed(messages) if message.get("direction") == "inbound"),
        "",
    )
    has_resume_consent_request = any("对方想发送附件简历给您，您是否同意" in text for text in message_texts)
    has_resume_attachment = any(
        "点击预览附件简历" in text or "附件简历" in text and "发送附件简历" not in text
        for text in message_texts
    )
    expresses_job_interest = bool(latest_inbound_message) and any(
        keyword in latest_inbound_message for keyword in ["应聘", "感兴趣", "盼望回复", "聊聊", "了解一下", "非常感兴趣"]
    )
    shares_relevant_experience = bool(latest_inbound_message) and any(
        keyword in latest_inbound_message for keyword in ["做过", "负责过", "经验", "运营", "天猫", "美妆", "类目", "店铺"]
    )
    asks_if_job_open = bool(latest_inbound_message) and any(
        keyword in latest_inbound_message for keyword in ["还在招聘", "还招吗", "还在招", "还要人吗", "还缺人吗"]
    )
    knowledge_answer: str | None = None
    knowledge_summary: str | None = None
    follow_up_prompt: str | None = None
    if knowledge_dir and knowledge_dir.exists():
        knowledge = load_knowledge_base(knowledge_dir)
        knowledge_answer = answer_question_from_knowledge(conversation, knowledge)
        knowledge_summary = summarize_job_from_knowledge(conversation, knowledge)
        follow_up_prompt = suggest_follow_up_question(conversation, knowledge)

    if knowledge_answer:
        if follow_up_prompt and "这边先看一下您发来的资料" not in follow_up_prompt:
            body = f"{salutation}，{knowledge_answer} {follow_up_prompt}"
        else:
            body = f"{salutation}，{knowledge_answer}"
        follow_up_questions = []
    elif score.get("recommended_action") == "close_conversation":
        body = (
            f"{salutation}，收到。"
            "感谢您说明当前情况，这边先不继续推进这个岗位了。"
            "后续如果您有更匹配的方向，也欢迎再沟通。"
        )
        follow_up_questions = []
    elif has_resume_consent_request or has_resume_attachment:
        body = (
            f"{salutation}，简历我这边先看一下。"
            f"我会结合 {job_title} 的要求做个初步评估，稍后再和您继续沟通。"
        )
        follow_up_questions: list[str] = []
    elif asks_if_job_open:
        body = (
            f"{salutation}，{job_title} 这个岗位目前还在招聘。"
            f"{follow_up_prompt or '方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？'}"
        )
        follow_up_questions = []
    elif expresses_job_interest:
        if knowledge_summary:
            body = (
                f"{salutation}，感谢关注 {job_title}。"
                f"这个岗位目前主要是 {knowledge_summary}。"
                f"{follow_up_prompt or '方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？'}"
            )
        else:
            body = (
                f"{salutation}，感谢关注 {job_title}。"
                f"{follow_up_prompt or '方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？'}"
            )
        follow_up_questions = []
    elif shares_relevant_experience:
        experience_summary = summarize_candidate_experience(latest_inbound_message)
        if knowledge_summary:
            body = (
                f"{salutation}，看到您提到{experience_summary}。"
                f"{job_title} 这边主要看重 {knowledge_summary}。"
                f"{follow_up_prompt or '方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？'}"
            )
        else:
            body = (
                f"{salutation}，看到您提到{experience_summary}。"
                f"{follow_up_prompt or '方便的话，您可以再具体说一下您最近两年的相关经验和项目情况吗？'}"
            )
        follow_up_questions = []
    elif score.get("recommended_action") in {"request_resume_or_experience", "continue_conversation"}:
        if follow_up_prompt and "这边先看一下您发来的资料" not in follow_up_prompt:
            body = f"{salutation}，{follow_up_prompt}"
            follow_up_questions = []
        elif "简历" in latest_inbound_message or "简历" in latest_message:
            body = (
                f"{salutation}，已收到您的消息。"
                f"如果方便的话，您可以先发送一份最新简历，我这边结合 {job_title} 的要求先帮您做初步评估。"
            )
            follow_up_questions = ["方便发一份最新简历吗？"]
        else:
            body = (
                f"{salutation}，感谢关注 {job_title}。"
                "如果方便的话可以先发一份最新简历，"
                "我这边先看一下您的背景和岗位匹配度，再和您继续沟通。"
            )
            follow_up_questions = ["方便发一份最新简历吗？"]
    else:
        body = (
            f"{salutation}，已收到您的消息。"
            "我先看一下当前沟通内容，稍后再给您更准确的回复。"
        )
        follow_up_questions = []

    return {
        "tone": "professional",
        "intent": "continue_conversation",
        "body": body,
        "follow_up_questions": follow_up_questions,
        "safety_notes": ["当前为规则生成草稿，发送前建议人工确认。"],
    }


def summarize_candidate_experience(message: str) -> str:
    cleaned = " ".join(part.strip() for part in message.replace("\n", " ").split() if part.strip())
    if not cleaned:
        return "您的相关经历"
    if len(cleaned) <= 28:
        return cleaned
    return f"{cleaned[:28].rstrip('，。；;、 ')}等经历"
