from datetime import date

import pytest

from boss_agent.candidate_evidence import (EvidenceIndex, evidence_report, extract_education, extract_experiences, fuse_experiences,
                                           hr_totals)
from boss_agent.major_catalog import BusinessMajorAlias, BusinessMajorScope, describe_major
from boss_agent.resume_structure import extract_resume_observations, report_major_observations
from boss_agent.work_timeline import extract_work_records, report_day_windows


@pytest.mark.parametrize(("raw", "code", "category", "method"), [
    ("计算机科学与技术", "080901", "计算机类", "exact_name"),
    ("080902", "080902", "计算机类", "exact_code"),
    ("计科", "080901", "计算机类", "verified_alias"),
    ("人工智能", "080717T", "电子信息类", "exact_name"),
    ("电子科学与技术", "080702", "电子信息类", "exact_name"),
    ("通信工程", "080703", "电子信息类", "exact_name"),
    ("信息安全", "080904K", "计算机类", "exact_name"),
    ("网络工程", "080903", "计算机类", "exact_name"),
    ("数据科学与大数据技术", "080910T", "计算机类", "exact_name"),
])
def test_verified_major_mapping(raw, code, category, method):
    item = describe_major(raw)
    assert (item["major_code"], item["major_category"], item["mapping_method"]) == (code, category, method)


def test_major_missing_typo_business_scope_and_version(monkeypatch):
    import boss_agent.major_catalog as catalog

    monkeypatch.setattr(catalog, "CATALOG_VERSION", "catalog-next")
    scope = BusinessMajorScope("计算机相关专业", "hiring-2", frozenset({"080717T"}), "经业务确认")
    item = describe_major("人工智能", scopes=(scope,))
    assert item["catalog_version"] == "catalog-next"
    assert item["major_category"] == "电子信息类" and item["business_scopes"][0]["matches"]
    assert describe_major("计算机科孒与技术")["status"] == "UNMAPPED"
    assert describe_major("自定义专业")["status"] == "UNMAPPED"
    assert describe_major(None)["status"] == "MISSING"
    assert describe_major("计算机类")["status"] == "GROUP_ONLY"
    custom = BusinessMajorAlias("岗位专用专业名", "080717T", "hiring-2", "经审核的业务名称")
    mapped = describe_major("岗位专用专业名", aliases=(custom,))
    assert mapped["mapping_method"] == "business_alias" and mapped["major_category"] == "电子信息类"
    assert mapped["business_mapping"]["version"] == "hiring-2"
    assert describe_major("岗位专用专业名", aliases=(custom, custom))["status"] == "UNMAPPED"


def test_education_references_and_untraceable_observation():
    from dataclasses import replace

    sources = {"dom": "教育经历\n示例大学 计算机科学与技术 学士\n其他大学 软件工程 硕士"}
    observations = extract_resume_observations(sources)
    index = EvidenceIndex(sources, "batch", "candidate", [])
    forged = replace(observations[0], quote="虚构引文", value="博士")
    records, facts = extract_education(index, [forged, *observations])
    assert len(records) == 2
    assert {fact["normalized_value"] for fact in facts if fact["field"] == "major"} == {"计算机科学与技术", "软件工程"}
    assert not any(fact["raw_value"] == "博士" for fact in facts)
    assert any(fact["field"] == "degree" and fact["raw_value"] == "学士" for fact in facts)
    assert not any(fact["field"] == "education_level" and fact["record_id"] == records[0]["record_id"] for fact in facts)
    for ref in index.refs.values():
        assert sources["dom"][ref["start"]:ref["end"]] == ref["quote"]


def test_record_pipeline_keeps_source_conflict_and_rejects_model_invention():
    import json
    from boss_agent.candidate_adapter import adapt_recommend_candidate

    standard = adapt_recommend_candidate(
        {"candidateId": "sample"}, {"opened": True, "candidateId": "sample"},
        {"detailCandidateId": "sample", "detailOpen": True, "captureStatus": "ready",
         "fullDomText": "教育经历\n蛮:@」 广东药科大学人力资源管理，本科 2021 - 2025\n示例大学 软件工程 学士",
         "ocrText": "教育经历\n广州华立学院 人力资源管理 本科 2021 - 2025", "ocrStatus": "ok"})

    def model(records):
        return json.dumps({"fields": [{"record_id": row["record_id"], "field": "education_mode",
                                      "raw": "全日制", "evidence_id": next(iter(row["citations"]))}
                                     for row in records]})

    result = evidence_report(standard.to_ranking_input(), "batch", date(2026, 1, 1), education_model=model)
    records = result["education_records"]
    assert len(records) == 2
    first, second = [row["fields"] for row in records]
    assert first["school"]["status"] == "conflict" and first["school"]["value"] is None
    assert first["major"]["value"] == "人力资源管理"
    assert first["education_start_year"]["value"] == "2021"
    assert second["degree"]["value"] == "学士" and second["education_level"]["status"] == "missing"
    assert all(row["fields"]["education_mode"]["status"] == "missing" for row in records)


def test_split_school_uses_one_record_and_original_offsets():
    sources = {"ocr": "教育经历\n广东药科\n大学 人力资源管理 本科 2021 - 2025"}
    index = EvidenceIndex(sources, "batch", "candidate", [])
    records, facts = extract_education(index, extract_resume_observations(sources))
    assert len(records) == 1
    school = next(fact for fact in facts if fact["field"] == "school")
    assert school["raw_value"] == "广东药科大学"
    ref = index.refs[school["evidence_ids"][0]]
    assert ref["quote"] == sources["ocr"][ref["start"]:ref["end"]]
    assert "广东药科\n大学" in ref["quote"]


def test_ai_resume_requires_traceable_fields_and_does_not_fall_back():
    import json
    from boss_agent.resume_model import with_recognized_resume
    from boss_agent.rule_engine import extract_resume_evidence

    education = "示例大学 软件工程 本科 2020-2024"
    work = "2024.06 - 2024.09 负责人员档案归档和整理"
    resume = extract_resume_evidence({"dom": f"教育经历\n{education}\n工作经历\n{work}"})
    payload = {"education":[{"source":"dom","quote":education,"fields":{"major":"软件工程","education_level":"本科",
                                                                          "school":"虚构大学"}}],
               "work":[{"source":"dom","quote":work,"hr":"unclear"}]}
    recognized, findings, status = with_recognized_resume(resume,"sample",lambda sources:json.dumps(payload,ensure_ascii=False))
    assert status == "completed"
    assert [fact.value for fact in recognized.facts["major"]] == ["软件工程"]
    assert not any(item.record_school for item in recognized.observations if item.field=="major")
    assert recognized.work_records[0].hr_work is None
    missing, findings, status = with_recognized_resume(resume,"sample",lambda sources:"unavailable")
    assert status == "failed" and "major" not in missing.facts and not missing.work_records


def test_ai_judges_all_requirements_including_preferences_and_rejects_forged_quotes():
    import json
    from boss_agent.recommendation import rank_recruitment_preview
    from boss_agent.resume_model import with_recognized_resume
    from boss_agent.rule_engine import extract_resume_evidence
    from boss_agent.task_schema import Criterion, RecruitmentTask

    degree = Criterion("education_level", "gte", "bachelor", "要求本科及以上")
    preference = Criterion("hands_on_tools", "has_any", ["Excel"], "熟悉 Excel 优先")
    task = RecruitmentTask("测试岗位", [degree], [preference], 1, None, "screen")
    sources = {"dom": "学历：本科\n使用 Excel 整理报表"}
    resume = extract_resume_evidence(sources)

    def model(payload):
        assert [item["field"] for item in payload["requirements"]] == ["education_level", "hands_on_tools"]
        return json.dumps({"education": [], "work": [], "decisions": [
            {"id":0, "status":"PASS", "evidence":[{"source":"dom","quote":"学历：硕士"}]},
            {"id":1, "status":"PASS", "evidence":[{"source":"dom","quote":"使用 Excel 整理报表"}]},
        ]})

    resume, findings, _ = with_recognized_resume(resume, "sample", model, [degree, preference])
    result = rank_recruitment_preview(task, [{"candidate_id":"sample", "sources":sources,
                                            "structured_evidence":resume, "model_findings":findings}])
    row = result["review_candidates"][0]
    assert row["hard_filters"][0]["status"] == "UNKNOWN"
    assert row["score_breakdown"]["preference_1"]["finding"]["status"] == "PASS"


def test_boss_dom_education_cards_without_section_heading():
    sources = {"dom": "项目角色\n3个月\n示例大学\n2024 - 2026\n\n软件工程 • 本科\n本科\n"
                      "其他学院\n2021 - 2024\n计算机科学与技术 • 大专\n大专"}
    index = EvidenceIndex(sources, "batch", "candidate", [])
    records, facts = extract_education(index, extract_resume_observations(sources))
    assert len(records) == 2
    for record, expected in zip(records, [("示例大学", "软件工程", "本科"), ("其他学院", "计算机科学与技术", "大专")]):
        values = {fact["field"]:fact["raw_value"] for fact in facts if fact["record_id"] == record["record_id"]}
        assert (values["school"], values["major"], values["education_level"]) == expected


def work(sources, as_of=date(2026, 1, 1), capture_complete=True):
    index = EvidenceIndex(sources, "batch", "candidate", [])
    rows = fuse_experiences(index, extract_experiences(index, extract_work_records(sources), as_of))
    return rows, hr_totals(rows, as_of, capture_complete=capture_complete), index


def test_work_roles_and_hr_union():
    sources = {"dom": ("工作经历\n2025.01–2025.06 公司：甲公司；岗位：招聘实习生\n职责：简历筛选\n"
                       "2025.04–2025.09 公司：乙公司；岗位：HR 助理\n职责：候选人邀约\n"
                       "2025.10–2026.01 公司：丙公司；岗位：行政助理\n"
                       "2025.01–2025.12 公司：丁公司；岗位：软件工程师")}
    rows, summary, _ = work(sources)
    assert [row["hr_relevance"] for row in rows] == ["CONFIRMED", "CONFIRMED", "POSSIBLE", "EXCLUDED"]
    assert [row["company"] for row in rows] == ["甲公司", "乙公司", "丙公司", "丁公司"]
    assert rows[0]["experience_type"] == "internship"
    assert summary["confirmed_hr_days"] == 213
    assert summary["possible_hr_days_max"] == 364
    assert summary["overlap_detected"]


def test_role_context_and_uncertain_dates():
    sources = {"dom": ("工作经历\n2025-01-01 至 2025-03-01 岗位：行政助理\n职责：整理档案、简历筛选\n"
                       "2025-04-01 至 2025-05-01 岗位：销售专员\n职责：销售产品；候选人邀约\n"
                       "岗位：薪酬绩效专员\n入职日期：2025-06\n"
                       "岗位：招聘专员\n结束日期：2025-11\n"
                       "2025-02-31 至 2025-03-01 岗位：人事助理")}
    rows, summary, _ = work(sources)
    assert rows[0]["hr_reason"] == "hr_duties"
    assert rows[1]["hr_reason"] == "mixed_role_without_hr_time_allocation"
    assert any(row["start_date"] == "2025-06" and row["end_date"] is None for row in rows)
    assert any(row["end_date"] == "2025-11" and row["start_date"] is None for row in rows)
    assert any(row["date_windows"]["status"] == "INVALID_DATE" for row in rows)
    assert summary["confirmed_hr_days"] == 59
    assert summary["possible_hr_days_max"] is None


def test_same_title_distinct_companies_and_cross_source_date_conflict():
    dom = ("工作经历\n2025-01-01 至 2025-04-01 公司：甲公司；岗位：HR 助理\n职责：简历筛选\n"
           "2025-07-01 至 2025-08-01 公司：甲公司；岗位：HR 助理\n")
    ocr = ("工作经历\n2025-01-01 至 2025-04-01 公司：甲公司；岗位：HR 助理\n职责：简历筛选\n"
           "2025-07-01 至 2025-09-01 公司：甲公司；岗位：HR 助理\n"
           "2025-10-01 至 2025-11-01 公司：乙公司；岗位：HR 助理")
    rows, summary, index = work({"dom": dom, "ocr": ocr})
    assert len(rows) == 4
    merged = next(row for row in rows if row["fusion"] == "same_company_position_and_period")
    assert {index.refs[item]["source_type"] for item in merged["evidence_ids"]} == {"DOM", "OCR"}
    assert sum(bool(row["conflicts"]) for row in rows) == 2
    assert summary["confirmed_hr_days"] == 121
    assert not next(row for row in rows if row["company"] == "乙公司")["conflicts"]


@pytest.mark.parametrize(("start", "end", "status"), [
    ("2025-01", "2025-06", "BOUNDED"), ("2025-02-31", "2025-03-01", "INVALID_DATE"),
    ("2025-06", "2025-01", "REVERSED_DATES"), ("2025-01", None, "MISSING_DATE"),
    (None, "2025-01", "MISSING_DATE"),
])
def test_calendar_endpoints(start, end, status):
    assert report_day_windows(start, end, date(2026, 1, 1))["status"] == status


def test_as_of_full_overlap_and_no_evidence():
    text = "工作经历\n2025-01-01 至今 公司：甲公司；岗位：招聘专员\n2025-01-01 至今 公司：乙公司；岗位：招聘专员"
    _, first, _ = work({"dom": text}, date(2025, 2, 1))
    _, second, _ = work({"dom": text}, date(2025, 3, 1), capture_complete=False)
    assert first["confirmed_hr_days"] == 31 and first["possible_hr_days_max"] == 31
    assert second["confirmed_hr_days"] == 59 and second["possible_hr_days_max"] is None
    assert first["overlap_detected"]
    _, empty, _ = work({"dom": "学历：本科"})
    assert empty["confirmed_hr_days"] is None and empty["possible_hr_days_min"] is None


@pytest.mark.parametrize(("second", "conflict"), [("计科", False), ("软件工程", True)])
def test_cross_source_education_fusion_preserves_both_references(second, conflict):
    sources = {"dom": "教育经历\n示例大学 计算机科学与技术 本科 2020-2024",
               "ocr": "教育经历\n示例大学 " + second + " 本科 2020-2024"}
    index = EvidenceIndex(sources, "batch", "candidate", [])
    records, facts = extract_education(index, extract_resume_observations(sources))
    assert len(records) == 1
    majors = [fact for fact in facts if fact["field"] == "major"]
    assert len(majors) == (2 if conflict else 1)
    assert all((fact["extraction_status"] == "CONFLICT") == conflict for fact in majors)
    assert {index.refs[key]["source_type"] for fact in majors for key in fact["evidence_ids"]} == {"OCR", "DOM"}


def test_report_distinguishes_missing_major_from_known_outside_scope_and_masks_privacy():
    from boss_agent.candidate_adapter import adapt_recommend_candidate
    from boss_agent.task_schema import Criterion, RecruitmentTask

    task = RecruitmentTask("示例岗位", [Criterion("major_group", "equals", "计算机类", "计算机类")], [],
                           None, None, "screen")

    def build(text, **extra):
        standard = adapt_recommend_candidate(
            {"candidateId": "candidate", "candidateName": "模拟姓名"},
            {"opened": True, "candidateId": "candidate"},
            {"detailCandidateId": "candidate", "detailOpen": True, "captureStatus": "ready",
             "fullDomText": text, **extra},
        )
        return evidence_report(standard.to_ranking_input(), "batch", date(2026, 1, 1), task=task)

    missing = build("学历：本科", canvasExpected=True, ocrText="a", ocrStatus="failed")
    assert missing["major_scope_checks"][0]["missing_evidence"]
    assert "major_evidence_missing" in missing["issues"] and "capture_incomplete" in missing["issues"]
    result = build("姓名：模拟姓名\n年龄：25\n电话：13800000000\n学历：本科\n专业：通信工程")
    assert result["major_scope_checks"][0]["observations"][0]["status"] == "OUTSIDE_SCOPE"
    assert all("模拟姓名" not in ref["quote"] and "13800000000" not in ref["quote"]
               and "年龄" not in ref["quote"] for ref in result["evidence"].values())


def test_employment_types_headings_and_stable_identity():
    as_of = date(2026, 1, 1)
    sources = {"dom": ("实习经历\n2025.01–2025.06 公司：甲公司；岗位：招聘助理\n"
                       "正式工作经历\n2025.07–2025.09 公司：乙公司；岗位：招聘助理\n"
                       "兼职经历\n2025.09–2025.12 公司：丙公司；岗位：招聘助理")}
    first, _, index = work(sources, as_of)
    second_index = EvidenceIndex({"ocr": sources["dom"]}, "another-run", "candidate", [])
    second = fuse_experiences(second_index, extract_experiences(second_index, extract_work_records(second_index.sources), as_of))
    assert [item["experience_type"] for item in first] == ["internship", "full_time", "part_time"]
    assert [item["experience_id"] for item in first] == [item["experience_id"] for item in second]
    assert any(ref["quote"] == "实习经历" for ref in index.refs.values())


@pytest.mark.parametrize(("code", "conflict"), [("080902", False), ("080903", True)])
def test_inline_code_and_major_name_are_cited_and_reconciled(code, conflict):
    sources = {"dom": f"学历：本科；专业名称：软件工程；专业代码：{code}"}
    observations = extract_resume_observations(sources)
    index = EvidenceIndex(sources, "batch", "candidate", [])
    records, facts = extract_education(index, [*observations, *report_major_observations(sources, observations)])
    assert len(records) == 1
    majors = [fact for fact in facts if fact["field"] == "major"]
    assert len(majors) == (2 if conflict else 1)
    assert all((fact["extraction_status"] == "CONFLICT") == conflict for fact in majors)
    assert any(fact["major"]["mapping_method"] == "exact_code" for fact in majors)
