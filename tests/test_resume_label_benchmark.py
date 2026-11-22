"""Hand-labeled, synthetic realistic-format fixtures. No real resumes."""

import json
from collections import Counter
from dataclasses import asdict, replace
from itertools import combinations

import pytest

from boss_agent.chrome_debug import ChromeDebugClient
from boss_agent.job_profile import load_job_profile
from boss_agent.recommend_preview import run_recommend_preview
from boss_agent.task_parser import parse_recruitment_task
from test_browser_recruitment_preview import detail, write_mock_data


CASES = [
    (
        "AI 工程师", "项目成果：部署检索服务，Python 实操经验优先",
        [
            ("PASS", 100, "Projects\n独立部署检索服务，使用 Python 编写评测。", ""),
            ("PASS", 0, "项目经历\n独立部署检索服务，使用 Rust 编写评测。", ""),
            ("FAIL", None, "项目经历\n未完成部署检索服务。", ""),
            ("UNKNOWN", None, "个人简介\n了解检索和 Python，完成原型研究。", ""),
            ("UNKNOWN", None, "项目经历\n独立部署检索服务。", "项目经历\n未完成部署检索服务。"),
        ],
    ),
    (
        "人事专员", "人力资源相关经验不超过12个月，Excel 实操经验优先",
        [
            ("PASS", 100, "Work Experience\n2024-01 至 2024-06 岗位：人力资源专员\n2024/04 至 2024/09 岗位：人力资源助理，实习\n使用 Excel 汇总招聘。", ""),
            ("PASS", 0, "工作经历\n2024年02月 至 2024年09月 岗位：人力资源专员\n使用 SQL 整理数据。", ""),
            ("FAIL", None, "工作经历\n2023-01 至 2024-06 岗位：人力资源专员", ""),
            ("UNKNOWN", None, "工作经历\n曾担任人力资源专员，起止时间缺失。", ""),
            ("UNKNOWN", None, "工作经历\n2024-01 至 2024-06 岗位：人力资源专员",
             "工作经历\n2023-01 至 2024-06 岗位：人力资源专员"),
        ],
    ),
    (
        "电商运营", "跨境电商经验至少12个月，Shopify 实操经验优先",
        [
            ("PASS", 100, "工作经历\n2024-01 至 2024-12 行业：跨境电商\n使用 Shopify 运营站点。", ""),
            ("PASS", 0, "Employment\n2023/01 至 2024/01 行业：跨境电商\n使用 Excel 做复盘。", ""),
            ("FAIL", None, "工作经历\n2024-01 至 2024-04 行业：跨境电商", ""),
            ("UNKNOWN", None, "工作经历\n2024-01 至 2024-12 岗位：运营助理，负责投放。", ""),
            ("UNKNOWN", None, "工作经历\n2024-01 至 2024-12 行业：跨境电商", "工作经历\n2024-01 至 2024-02 行业：跨境电商"),
        ],
    ),
    (
        "财务会计", "证书：初级会计资格证书，金蝶 实操经验优先",
        [
            ("PASS", 100, "Certifications\n持有初级会计资格证书。\n使用金蝶 完成月结。", ""),
            ("PASS", 0, "教育背景\n2020年毕业于样例大学，获本科学位。\n2023年毕业于样例学院，获硕士学位。\n持有初级会计资格证书。", ""),
            ("FAIL", None, "证书：无", ""),
            ("UNKNOWN", None, "计划报考初级会计资格证书。", ""),
            ("UNKNOWN", None, "Certifications\n持有初级会计资格证书。", "简历补充说明\n证书：无"),
        ],
    ),
    (
        "UI 设计师", "全日制本科，Figma 实操经验优先",
        [
            ("PASS", 100, "Education\n毕业于样例大学，获全日制本科学位。\n使用 Figma 交付界面原型。", ""),
            ("PASS", 0, "教育背景\n某大学全日制本科。\n仅了解 Figma，使用 Sketch 绘制界面。", ""),
            ("FAIL", None, "教育经历\n毕业于某学院，获非全日制本科学位。", ""),
            ("UNKNOWN", None, "教育背景\n获本科学位，毕业于某大学。", ""),
            ("UNKNOWN", None, "教育背景\n某大学全日制本科。", "教育背景\n某大学非全日制本科。"),
        ],
    ),
]


def _simulated_judge(rule, sources):
    # A deterministic test double, not a validation of a production model.
    for source, text in sources.items():
        for quote, status in (("未完成部署检索服务", "FAIL"), ("独立部署检索服务", "PASS")):
            if quote in text:
                return json.dumps({"status": status, "evidence": [{"source": source, "quote": quote}]})
    return '{"status":"UNKNOWN","evidence":[]}'


def compare_hand_labels(folder, job, request, samples):
    records, cards, expected = {}, [], {}
    for index, (label, score, dom, ocr) in enumerate(samples, start=1):
        candidate_id = f"sample-{index:02}"
        extra = {"ocrText": ocr, "ocrStatus": "ok"} if ocr else {}
        records[candidate_id] = detail(candidate_id, dom, **extra)
        cards.append({"candidateId": candidate_id, "cardIndex": index % 3})
        expected[candidate_id] = (label, score)
    write_mock_data(folder, cards, records)
    task = parse_recruitment_task(f"岗位：{job}，{request}")
    profile = load_job_profile({
        "name": job, "scoring": [
            {"name": "evidenced_practice", "condition": asdict(task.soft_preferences[0]), "weight": 100}
        ],
    })
    result = run_recommend_preview(
        ChromeDebugClient("http://127.0.0.1:9222", folder),
        replace(task, soft_preferences=[]), profile=profile, judge=_simulated_judge,
    )
    rows = result["ranked_candidates"] + result["rejected_candidates"] + result["review_candidates"]
    by_id = {row["candidate_id"]: row for row in rows}
    matrix = Counter(f"{label}->{by_id[key]['hard_filter_status']}" for key, (label, _) in expected.items())
    expected_order = sorted((key for key, (_, score) in expected.items() if score is not None),
                            key=lambda key: (-expected[key][1], key))
    ranked_ids = [row["candidate_id"] for row in result["ranked_candidates"]]
    pairs = list(combinations(expected_order, 2))
    matched_pairs = sum(a in ranked_ids and b in ranked_ids and ranked_ids.index(a) < ranked_ids.index(b)
                        for a, b in pairs)
    return {"job": job, "confusion": dict(matrix), "ranked": ranked_ids, "expected_order": expected_order,
            "pairwise_matches": matched_pairs, "pairwise_total": len(pairs),
            "score_matches": sum(by_id[key]["score"] == score for key, (_, score) in expected.items()),
            "actual_successful_contacts": result["summary"]["actual_successful_contacts"],
            "sample_count": len(samples)}


@pytest.mark.parametrize(("job", "request_text", "samples"), CASES, ids=[item[0] for item in CASES])
def test_realistic_synthetic_resume_labels(tmp_path, job, request_text, samples):
    report = compare_hand_labels(tmp_path, job, request_text, samples)
    assert report["confusion"] == {"PASS->PASS": 2, "FAIL->FAIL": 1, "UNKNOWN->UNKNOWN": 2}
    assert report["score_matches"] == 5
    assert report["ranked"] == report["expected_order"]
    assert report["pairwise_matches"] == report["pairwise_total"] == 1
    assert report["actual_successful_contacts"] == 0
