from __future__ import annotations

from boss_agent.resume_agent import analyze_resume_against_criteria


def test_analyze_resume_against_criteria_matches_bachelor_and_three_years(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-7K\n"
            "何婷婷\n"
            "25岁 3年 本科 在职-月内到岗\n"
            "2019 2023\n"
            "湖南应用技术学院 行政管理 本科"
        ),
        criteria="本科学历，三年工作经验",
    )

    assert result["meetsCriteria"] is True
    assert result["name"] == "何婷婷"
    assert "何婷婷" in result["candidateSummary"]
    assert result["educationLevel"] == "本科"
    assert result["workYears"] == 3


def test_analyze_resume_against_criteria_rejects_fresh_graduate(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "面议\n"
            "刘女士\n"
            "22岁 25年应届生 本科\n"
            "2021 2025\n"
            "广州城市理工学院 人力资源管理 本科"
        ),
        criteria="本科学历，三年工作经验",
    )

    assert result["meetsCriteria"] is False


def test_analyze_resume_against_age_degree_years_and_keyword(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-8K\n"
            "林清梅\n"
            "31岁 10年以上 本科 离职-随时到岗\n"
            "期望\n"
            "珠海 行政专员/助理\n"
            "2016.07 2026.02\n"
            "格力钛新能源 行政专员/助理\n"
            "2022 2025\n"
            "广州航海学院 国际商务 本科"
        ),
        criteria="28-35岁，本科，有五年前台或行政经验",
    )

    assert result["meetsCriteria"] is True
    assert result["age"] == 31
    assert result["workYears"] == 10


def test_analyze_resume_rejects_age_outside_range(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-6K\n"
            "欧阳明晶\n"
            "22岁 3年 本科 离职-随时到岗\n"
            "期望\n"
            "珠海 前台"
        ),
        criteria="28-35岁，本科，有五年前台或行政经验",
    )

    assert result["meetsCriteria"] is False


def test_analyze_resume_accepts_zhuanke_as_dazhuan(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-6K\n"
            "李女士\n"
            "31岁 6年 大专 离职-随时到岗\n"
            "期望\n"
            "珠海 行政专员/助理\n"
            "2020.01 2026.01\n"
            "某公司 行政前台"
        ),
        criteria="30-32岁，专科，有五年前台或行政经验",
    )

    assert result["meetsCriteria"] is True


def test_analyze_resume_accepts_age_and_work_years_range(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-6K\n"
            "张琪\n"
            "24岁 3年 大专 离职-随时到岗\n"
            "期望\n"
            "珠海 行政专员/助理\n"
            "2024.12 至今\n"
            "横琴黄同学传媒 人事行政主管/专员"
        ),
        criteria="22-25岁，有1-3年前台或行政经验",
    )

    assert result["meetsCriteria"] is True
    assert result["age"] == 24
    assert result["workYears"] == 3


def test_analyze_resume_rejects_work_years_above_range(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "5-6K\n"
            "刘敏婧\n"
            "28岁 7年 本科 离职-随时到岗\n"
            "期望\n"
            "珠海 前台"
        ),
        criteria="22-25岁，有1-3年前台或行政经验",
    )

    assert result["meetsCriteria"] is False


def test_analyze_resume_matches_explicit_keyword_synonym_group(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "王女士\n"
            "28岁 5年 本科\n"
            "曾负责淘宝店铺运营，包含商品上下架、活动策划和店铺日常数据分析。"
        ),
        criteria="关键词：天猫、投放",
    )

    assert result["meetsCriteria"] is True
    assert result["keywordMatch"]["matched"] is True
    assert result["keywordMatch"]["matches"][0]["keyword"] == "天猫"
    assert result["keywordMatch"]["matches"][0]["matchedTerm"] == "淘宝"
    assert "淘宝店铺运营" in result["keywordMatch"]["matches"][0]["evidenceText"]
    assert result["keywordMatch"]["matches"][0]["evidenceSummary"]
    assert "淘宝" in result["keywordMatch"]["matches"][0]["evidenceSummary"]


def test_analyze_resume_matches_explicit_keyword_delivery_synonym(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "陈先生\n"
            "30岁 6年 本科\n"
            "负责直通车、千川推广和信息流广告优化，提升店铺成交。"
        ),
        criteria="关键词：投放",
    )

    assert result["meetsCriteria"] is True
    assert result["keywordMatch"]["matches"][0]["matchedTerm"] in {"直通车", "千川", "推广", "信息流"}


def test_analyze_resume_rejects_when_explicit_keyword_missing(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "李女士\n"
            "27岁 4年 本科\n"
            "主要负责线下门店陈列、客户接待和库存盘点。"
        ),
        criteria="关键词：天猫",
    )

    assert result["meetsCriteria"] is False
    assert result["keywordMatch"]["matched"] is False
    assert result["keywordMatch"]["matches"] == []
    assert any("未识别到关键词或同义词" in risk for risk in result["risks"])


def test_analyze_resume_accepts_any_explicit_keyword_group(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "周先生\n"
            "29岁 5年 本科\n"
            "HRBP 背景，负责招聘配置、岗位需求沟通和人才盘点。"
        ),
        criteria="关键词：天猫、招聘",
    )

    assert result["meetsCriteria"] is True
    assert result["keywordMatch"]["matched"] is True
    assert any(match["keyword"] == "招聘" for match in result["keywordMatch"]["matches"])


def test_analyze_resume_keyword_match_does_not_override_other_failed_criteria(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "赵女士\n"
            "26岁 4年 大专\n"
            "负责淘宝店铺运营和平台活动。"
        ),
        criteria="本科，关键词：天猫",
    )

    assert result["keywordMatch"]["matched"] is True
    assert result["meetsCriteria"] is False
