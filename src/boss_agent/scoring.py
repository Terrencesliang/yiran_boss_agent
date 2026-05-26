from __future__ import annotations

from typing import Any


def score_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    score = 40
    reasons: list[str] = []

    if conversation.get("jobTitle"):
        score += 15
        reasons.append("已识别岗位信息，可进行基础匹配判断")

    if conversation.get("salary"):
        score += 10
        reasons.append("已识别薪资信息，可辅助判断职位质量")

    if conversation.get("city"):
        score += 5
        reasons.append("已识别城市信息，可辅助判断地域匹配")

    messages = conversation.get("messages", [])
    if messages:
        reasons.append("已抓取最近消息，可判断沟通阶段")

    latest_inbound = next(
        (message.get("text", "") for message in reversed(messages) if message.get("direction") == "inbound"),
        "",
    )
    latest_text = latest_inbound or (messages[-1]["text"] if messages else "")

    if any(
        keyword in latest_text
        for keyword in [
            "不想继续沟通",
            "不继续沟通",
            "不考虑了",
            "先不考虑",
            "暂时不考虑",
            "岗位不合适",
            "不太合适",
            "不合适",
            "投错了",
            "更适合做",
        ]
    ):
        reply_priority = "low"
        risk_level = "low"
        recommended_action = "close_conversation"
        reasons.append("候选人表达了不继续沟通或岗位不匹配，应结束当前推进，不再索要简历")
    elif any(keyword in latest_text for keyword in ["发送附件简历", "点击预览附件简历", "附件简历"]):
        reply_priority = "high"
        risk_level = "low"
        recommended_action = "resume_review"
        reasons.append("候选人已进入简历处理阶段，应优先同意、下载并确认收到")
    elif any(
        keyword in latest_text
        for keyword in [
            "还在招聘",
            "还招吗",
            "还在招",
            "还要人吗",
            "还缺人吗",
            "薪资是多少",
            "薪资范围",
            "上班地点",
            "工作地点",
            "公司是做什么",
            "公司做什么",
        ]
    ):
        reply_priority = "high"
        risk_level = "low"
        recommended_action = "answer_question"
        reasons.append("候选人提出了明确问题，应优先针对问题回复")
    elif any(keyword in latest_text for keyword in ["应聘", "感兴趣", "盼望回复", "聊聊", "了解一下", "想了解", "方便沟通"]):
        reply_priority = "medium"
        risk_level = "low"
        recommended_action = "request_resume_or_experience"
        reasons.append("候选人表达了明确求职意向，适合引导其提供简历或相关经验")
    elif latest_text:
        reply_priority = "medium"
        risk_level = "low"
        recommended_action = "continue_conversation"
        reasons.append("候选人已有有效沟通内容，应结合上下文继续回复")
    else:
        reply_priority = "low"
        risk_level = "low"
        recommended_action = "review"
        reasons.append("当前对话信号较弱，建议先人工查看")

    return {
        "match_score": score,
        "reply_priority": reply_priority,
        "risk_level": risk_level,
        "reasons": reasons,
        "missing_info": [],
        "recommended_action": recommended_action,
    }
