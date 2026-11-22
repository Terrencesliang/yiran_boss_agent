"""Independent, hand-authored expectations for Phase 03 deterministic intake."""

from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest

from boss_agent.cli import main
from boss_agent.chrome_debug import ChromeDebugClient
from test_browser_recruitment_preview import detail, write_mock_data


ORIGINAL = (
    "在「推荐牛人」筛选并向最多 10 位符合条件的人打招呼：2025 届或 2026 届、"
    "计算机大类全日制本科、有一年以内的人力资源经验，优先考虑人工智能或计算机科学与技术专业，"
    "以及有 WorkBuddy 或 Agent 实操经验的人。"
)
JOB = "电商人事AI助理 _ 深圳 5-7K"
DEGREE = ("education_level", "equals", "bachelor")
FULL_TIME = ("education_mode", "equals", "full_time")
YEARS = ("graduation_year", "one_of", [2025, 2026])
MAJOR_GROUP = ("major_group", "equals", "计算机类")
AI = ("major", "equals", "人工智能")
HR_POSITIVE = ("hr_experience_months", "gt", 0)
HR_LIMIT = ("hr_experience_months", "lte", 12)
PREFERRED_MAJOR = ("major", "one_of", ["人工智能", "计算机科学与技术"])
TOOLS = ("hands_on_tools", "has_any", ["WorkBuddy", "Agent"])
ORIGINAL_HARD = [YEARS, FULL_TIME, DEGREE, MAJOR_GROUP, HR_POSITIVE, HR_LIMIT]

# Each row declares the exact full set of hard criteria, preferences, action and limits.
CASES = [
    ("N01-original", ORIGINAL, ORIGINAL_HARD, [PREFERRED_MAJOR, TOOLS], "greet", 10, None, True, []),
    ("N02-quoted-entry", "在「推荐牛人」筛选本科", [DEGREE], [], "screen", None, None, False, []),
    ("N03-upper-cap", "本科，给不超过十人打招呼", [DEGREE], [], "greet", 10, None, True, []),
    ("N04-digit-count", "本科，给8人打招呼", [DEGREE], [], "greet", 8, None, False, []),
    ("N05-chinese-count", "本科，向十人打招呼", [DEGREE], [], "greet", 10, None, False, []),
    ("N06-preview-only", "本科，只预览", [DEGREE], [], "screen", None, None, False, []),
    ("N07-positive-hr", "有一年以内的人力资源经验", [HR_POSITIVE, HR_LIMIT], [], "screen", None, None, False, []),
    ("N08-cap-without-existence", "人力资源经验不超过12个月", [("hr_experience_months", "between", [0, 12])], [], "screen", None, None, False, []),
    ("N09-explicit-minimum", "至少1个月人力资源经验", [("hr_experience_months", "gte", 1)], [], "screen", None, None, False, []),
    ("N10-suffix-hr", "有人力资源经验至多12个月", [HR_POSITIVE, HR_LIMIT], [], "screen", None, None, False, []),
    ("N11-duration-conflict", "有一年以内的人力资源经验，至少13个月人力资源经验", [HR_POSITIVE, HR_LIMIT, ("hr_experience_months", "gte", 13)], [], "screen", None, None, False, ["conflicting_hr_experience_months"]),
    ("N12-undefined-criterion", "本科，综合素质符合团队预期", [DEGREE], [], "screen", None, None, False, ["unresolved_condition:综合素质符合团队预期"]),
    ("N13-ai-allowed", "计算机类或人工智能专业", [("OR", (MAJOR_GROUP, AI))], [], "screen", None, None, False, []),
    ("N14-major-degree", "计算机大类全日制本科", [FULL_TIME, DEGREE, MAJOR_GROUP], [], "screen", None, None, False, []),
    ("N15-study-mode", "全日制本科", [FULL_TIME, DEGREE], [], "screen", None, None, False, []),
    ("N16-major-options", "专业：软件工程或网络工程", [("major", "one_of", ["软件工程", "网络工程"])], [], "screen", None, None, False, []),
    ("N17-preference-scope", "本科，优先考虑人工智能或计算机科学与技术专业，以及有 WorkBuddy 或 Agent 实操经验的人", [DEGREE], [PREFERRED_MAJOR, TOOLS], "screen", None, None, False, []),
    ("N18-mandatory-plus-preference", "本科且优先有 WorkBuddy 实操经验", [DEGREE], [("hands_on_tools", "has_any", ["WorkBuddy"])], "screen", None, None, False, []),
    ("N19-short-cohorts", "25届26届", [YEARS], [], "screen", None, None, False, []),
    ("N20-single-cohort", "2026 届", [("graduation_year", "one_of", [2026])], [], "screen", None, None, False, []),
    ("N21-ambiguous-range", "本科，2025至2026年", [DEGREE], [], "screen", None, None, False, ["unresolved_condition:2025至2026年"]),
    ("N22-independent-budgets", "本科，检查3份简历，给最多10人打招呼", [DEGREE], [], "greet", 10, 3, True, []),
    ("N23-action-conflict", "本科，给10人打招呼，不要打招呼", [DEGREE], [], "screen", None, None, False, ["conflicting_action"]),
    ("N24-sensitive-block", "本科，只要女生", [DEGREE], [], "screen", None, None, False, ["unsupported_conditions"]),
    ("N25-missing-job", "本科", [DEGREE], [], "screen", None, None, False, ["job_required"]),
    ("N26-conflicting-job", "岗位：运营，职位：设计，本科", [DEGREE], [], "screen", None, None, False, ["conflicting_job", "job_required"]),
    ("N27-explicit-job", "岗位：人事助理，本科", [DEGREE], [], "screen", None, None, False, []),
    ("N28-unrecognized-field", "本科，星座：火象", [DEGREE], [], "screen", None, None, False, ["unresolved_condition:星座:火象"]),
    ("N29-negation", "本科，排除非全日制", [DEGREE, ("NOT", (("education_mode", "equals", "part_time"),))], [], "screen", None, None, False, []),
    ("N30-new-request", "岗位：独立测试岗，硕士，只预览", [("education_level", "equals", "master")], [], "screen", None, None, False, []),
]


def shape(condition):
    if "conditions" in condition:
        return condition["operator"], tuple(map(shape, condition["conditions"]))
    assert condition["source"].strip()
    return condition["field"], condition["operator"], condition["value"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSS_RECRUITMENT_AUDIT_DIR", str(tmp_path / "intake-audit"))

    def forbidden(*args, **kwargs):
        pytest.fail("Phase 03 must not use a real connection or send a greeting")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ChromeDebugClient, "click_recommend_card_greet", forbidden)


@pytest.mark.parametrize(
    "case_id,request_text,hard,soft,action,cap,budget,limit_only,blockers",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_deterministic_intake(case_id, request_text, hard, soft, action, cap, budget, limit_only, blockers, capsys):
    cmd = ["plan-recruitment", "--request", request_text]
    if case_id not in {"N25-missing-job", "N26-conflicting-job", "N27-explicit-job", "N30-new-request"}:
        cmd += ["--job-context", JOB]
    assert main(cmd) == 0
    data = json.loads(capsys.readouterr().out)
    task = data["task"]
    assert list(map(shape, task["hard_filters"])) == hard
    assert list(map(shape, task["soft_preferences"])) == soft
    assert (task["action"], task["target_success_count"], task["max_checks"], task["contact_limit_only"]) == (
        action, cap, budget, limit_only,
    )
    assert data["plan"]["blockers"] == blockers
    audit = json.loads(Path(data["audit_file"]).read_text(encoding="utf-8"))
    assert audit["request"] == request_text and audit["parser_kind"] == "deterministic"
    assert audit["blockers"] == blockers and audit["status"] == ("blocked" if blockers else "planned")


@pytest.mark.parametrize("qualified", [0, 1, 9, 11])
def test_original_request_to_mock_candidate_preview(qualified, tmp_path, capsys):
    fixtures = tmp_path / "browser"
    candidates = {}
    for i in range(qualified + 1):
        identifier = f"mock-{i:02}"
        years = 2027 if i == qualified else 2026
        candidates[identifier] = detail(
            identifier, f"样本编号 {i:02}\n学历：全日制本科\n专业：计算机科学与技术\n毕业届别：{years}\n人力资源经验：2个月\n实操工具：Agent",
        )
    write_mock_data(fixtures, [{"candidateId": identifier} for identifier in candidates], candidates)
    report = tmp_path / "preview.json"
    assert main([
        "action", "screen-recommend-detail", "--request", ORIGINAL, "--job-context", JOB,
        "--mock-dir", str(fixtures), "--report-path", str(report),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["original_request"] == ORIGINAL
    assert output["summary"]["qualified"] == qualified
    assert len(output["contact_preview"]) == min(qualified, 10)
    assert output["summary"]["actual_successful_contacts"] == 0
    audit = json.loads(Path(output["audit_file"]).read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert audit["execution_task"] == output["task"]
    assert audit["input_kind"] == "natural_language"


@pytest.mark.parametrize("input_kind", ["request", "conflicting-context", "invalid-file", "blocked-file"])
def test_blocked_tasks_never_construct_candidate_client(input_kind, tmp_path, monkeypatch):
    request_text = "本科，星座：火象"
    flags = ["--request", request_text, "--job-context", JOB]
    if input_kind == "conflicting-context":
        flags = ["--request", "岗位：另一个岗位，本科", "--job-context", JOB]
    elif input_kind.endswith("file"):
        task = {
            "job": JOB, "hard_filters": [], "soft_preferences": [], "max_checks": None,
            "target_success_count": True if input_kind == "invalid-file" else None, "action": "greet",
        }
        file = tmp_path / "import.json"
        file.write_text(json.dumps(task), encoding="utf-8")
        flags = ["--task-file", str(file)]

    def forbidden(*args, **kwargs):
        pytest.fail("blocked input reached the candidate client")

    monkeypatch.setattr("boss_agent.cli.ChromeDebugClient", forbidden)
    with pytest.raises(SystemExit, match="2"):
        main(["action", "screen-recommend-detail", *flags, "--mock-dir", str(tmp_path)])
    audit, = (tmp_path / "intake-audit").glob("*.json")
    data = json.loads(audit.read_text(encoding="utf-8"))
    assert data["status"] == "blocked" and data["blockers"]
    assert "execution_task" not in data
    if input_kind.endswith("file"):
        assert data["task_file_sha256"] == sha256(file.read_bytes()).hexdigest()
        assert data["submitted_task"] == task


def test_blocked_offline_request_does_not_read_candidate_file(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        main([
            "evaluate-recruitment", "--request", "本科，星座：火象", "--job-context", JOB,
            "--candidates", str(tmp_path / "does-not-exist.json"),
        ])
    audit, = (tmp_path / "intake-audit").glob("*.json")
    data = json.loads(audit.read_text(encoding="utf-8"))
    assert data["blockers"] == ["unresolved_condition:星座:火象"]


def test_multiple_requests_keep_separate_audits(tmp_path):
    assert main(["plan-recruitment", "--request", ORIGINAL, "--job-context", JOB]) == 0
    assert main(["plan-recruitment", "--request", "岗位：设计，硕士"]) == 0
    data = [json.loads(file.read_text(encoding="utf-8")) for file in (tmp_path / "intake-audit").glob("*.json")]
    assert len(data) == 2
    second = next(record for record in data if record["request"] == "岗位：设计，硕士")
    assert list(map(shape, second["parsed_task"]["hard_filters"])) == [("education_level", "equals", "master")]
    assert second["parsed_task"]["soft_preferences"] == []


@pytest.mark.parametrize("sample_kind", ["synthetic", "real-deidentified"])
def test_offline_intake_keeps_sample_provenance(sample_kind, tmp_path, capsys):
    source = tmp_path / "offline.json"
    source.write_text("[]", encoding="utf-8")
    assert main([
        "evaluate-recruitment", "--request", "岗位：设计，本科", "--candidates", str(source),
        "--sample-kind", sample_kind,
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["sample_kind"] == sample_kind
    assert result["summary"]["checked"] == 0
