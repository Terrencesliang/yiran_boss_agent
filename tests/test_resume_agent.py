from __future__ import annotations

import json

from boss_agent import resume_agent
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
            "曾负责淘宝店铺运营，包含商品上下架、活动策划、直通车投放和店铺日常数据分析。"
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


def test_analyze_resume_requires_all_default_explicit_keyword_groups(monkeypatch) -> None:
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

    assert result["meetsCriteria"] is False
    assert result["keywordMatch"]["matched"] is False
    assert result["keywordMatch"]["missing"] == ["天猫"]
    assert any(match["keyword"] == "招聘" for match in result["keywordMatch"]["matches"])


def test_analyze_resume_accepts_any_keyword_when_explicitly_requested(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "周先生\n"
            "29岁 5年 本科\n"
            "HRBP 背景，负责招聘配置、岗位需求沟通和人才盘点。"
        ),
        criteria="关键词任一：天猫、招聘",
    )

    assert result["meetsCriteria"] is True
    assert result["keywordMatch"]["matched"] is True
    assert result["keywordMatch"]["mode"] == "any"


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


def test_analyze_resume_rejects_total_years_with_unrelated_tmall_mention(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "李女士\n"
            "28岁 5年 本科\n"
            "2019.01-2024.01 某制造公司 行政专员，负责考勤、会议和办公用品管理。\n"
            "个人兴趣：关注天猫店铺活动。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is False
    assert result["relatedExperience"]["required"] is True


def test_analyze_resume_accepts_three_years_tmall_store_operation(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "王女士\n"
            "29岁 4年 本科\n"
            "2021.01-2024.01 某品牌 天猫店铺运营，负责活动策划、商品运营和GMV增长。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is True
    assert result["relatedExperience"]["matched"] is True


def test_analyze_resume_accepts_taobao_as_tmall_related_experience(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "陈女士\n"
            "30岁 5年 本科\n"
            "2020.03-2023.03 淘宝店铺运营，负责商品上下架、活动策划和推广投放。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is True
    assert result["relatedExperience"]["matched"] is True


def test_analyze_resume_accepts_direct_tmall_ecommerce_experience_phrase(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "钱女士\n"
            "28岁 4年 本科\n"
            "具备3年天猫电商经验，熟悉店铺活动、商品运营和数据分析。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is True
    assert result["relatedExperience"]["matched"] is True


def test_analyze_resume_records_company_business_as_weak_evidence_only(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "company_business_aliases.json").write_text(
        """
{
  "companies": [
    {
      "company": "样例品牌公司",
      "platformTags": ["天猫"],
      "businessTags": ["美妆电商"],
      "description": "测试映射"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = analyze_resume_against_criteria(
        resume_text=(
            "赵女士\n"
            "31岁 6年 本科\n"
            "2021.01-2024.01 样例品牌公司 商务专员，负责客户资料整理和合同归档。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is False
    assert result["weakCompanyEvidence"]
    assert result["weakCompanyEvidence"][0]["hasRoleEvidence"] is False


def test_analyze_resume_accepts_company_business_when_role_and_years_exist(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "company_business_aliases.json").write_text(
        """
{
  "companies": [
    {
      "company": "样例品牌公司",
      "platformTags": ["天猫"],
      "businessTags": ["美妆电商"],
      "description": "测试映射"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = analyze_resume_against_criteria(
        resume_text=(
            "赵女士\n"
            "31岁 6年 本科\n"
            "2021.01-2024.01 样例品牌公司 店铺运营，负责活动策划、推广投放和GMV增长。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["meetsCriteria"] is True
    assert result["relatedExperience"]["matched"] is True
    assert any(item["weak"] for item in result["relatedExperience"]["evidence"])


def test_llm_result_uses_model_judgment_with_keyword_evidence_gate(monkeypatch) -> None:
    monkeypatch.setenv("BOSS_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "name": "李女士",
                                        "candidateSummary": "5年行政经验",
                                        "educationLevel": "本科",
                                        "workYears": 5,
                                        "meetsCriteria": True,
                                        "reasons": ["模型认为符合"],
                                        "risks": [],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

    monkeypatch.setattr(resume_agent, "urlopen", lambda request, timeout=30: FakeResponse())

    result = analyze_resume_against_criteria(
        resume_text=(
            "李女士\n"
            "28岁 5年 本科\n"
            "2019.01-2024.01 某制造公司 行政专员，负责考勤、会议和办公用品管理。\n"
            "个人兴趣：关注天猫店铺活动。"
        ),
        criteria="有3年天猫电商经验",
    )

    assert result["agent"] == "llm"
    assert result["meetsCriteria"] is True
    assert "relatedExperience" not in result
    assert result["keywordMatch"]["matched"] is True
    assert result["confidenceLevel"] == "high"


def test_llm_rejection_is_not_overridden_by_local_related_experience(monkeypatch) -> None:
    monkeypatch.setenv("BOSS_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "name": "杨先生",
                                        "candidateSummary": "5年品牌运营经验",
                                        "educationLevel": "本科",
                                        "workYears": 5,
                                        "meetsCriteria": False,
                                        "reasons": ["模型判断没有明确天猫电商经验"],
                                        "risks": ["主要是品牌运营和小红书推广"],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

    monkeypatch.setattr(resume_agent, "urlopen", lambda request, timeout=30: FakeResponse())

    result = analyze_resume_against_criteria(
        resume_text=(
            "杨先生\n"
            "28岁 5年 本科\n"
            "2021.01-2026.01 某品牌公司 运营经理，负责小红书推广、活动策划、用户运营。\n"
            "求职期望：天猫运营"
        ),
        criteria="有两年天猫电商经验",
    )

    assert result["agent"] == "llm"
    assert result["meetsCriteria"] is False
    assert "relatedExperience" not in result
    assert result["confidenceLevel"] == "low"


def test_analyze_resume_accepts_month_level_tmall_operation_with_high_confidence(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "王女士\n"
            "30岁 6年 本科\n"
            "工作经历\n"
            "某品牌公司，天猫运营 - 运营部 2021.05 - 2024.09\n"
            "负责天猫店铺运营、直通车投放、活动策划、生意参谋数据分析和GMV增长。"
        ),
        criteria="有3年天猫电商运营经验",
    )

    assert result["meetsCriteria"] is True
    assert result["confidenceLevel"] == "high"
    assert result["confidenceScore"] >= 80
    assert result["relatedExperience"]["totalRelatedMonths"] >= 36
    assert result["experienceEvidence"]


def test_analyze_resume_accepts_accumulated_related_months(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "陈先生\n"
            "工作经历\n"
            "A公司，天猫运营 2021.05 - 2022.06\n"
            "负责店铺运营、活动策划和推广投放。\n"
            "B公司，淘宝运营 2022.06 - 2024.09\n"
            "负责淘宝店铺运营、标题优化、主图详情页和转化率提升。"
        ),
        criteria="有3年天猫电商运营经验",
    )

    assert result["meetsCriteria"] is True
    assert result["confidenceLevel"] == "high"
    assert result["relatedExperience"]["totalRelatedMonths"] >= 36
    assert len(result["experienceEvidence"]) >= 2


def test_analyze_resume_rejects_chat_job_title_only_with_confidence(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "收藏\n继续沟通\n未处理，05月27日 向您发起沟通，沟通职位 天猫运营（服饰）\n"
            "工作经历\n"
            "某制造公司，行政专员 2021.01 - 2025.01\n"
            "负责考勤、会议和办公用品管理。"
        ),
        criteria="有3年天猫电商运营经验",
    )

    assert result["meetsCriteria"] is False
    assert result["confidenceLevel"] == "low"
    assert result["experienceEvidence"] == []


def test_analyze_resume_low_confidence_when_related_terms_have_no_time(monkeypatch) -> None:
    monkeypatch.delenv("BOSS_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = analyze_resume_against_criteria(
        resume_text=(
            "候选人熟悉天猫店铺运营、活动策划和推广投放，做过电商相关工作，"
            "但简历没有明确起止时间。"
        ),
        criteria="有3年天猫电商运营经验",
    )

    assert result["meetsCriteria"] is False
    assert result["confidenceLevel"] in {"low", "medium"}
    assert result["confidenceScore"] < 80
