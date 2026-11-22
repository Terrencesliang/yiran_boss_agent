"""Build a reviewable plan without opening a browser or running steps."""

from boss_agent.task_schema import RecruitmentTask
from boss_agent.task_validator import task_blockers


def plan_recruitment_task(task: RecruitmentTask, *, preview_only: bool = False) -> dict:
    steps = [
        ("confirm_job", "确认岗位"),
        ("open_recommend", "进入推荐牛人"),
        ("apply_page_filters", "应用页面筛选"),
        ("capture_candidates", "获取候选人"),
        ("verify_hard_filters", "核实硬性条件"),
        ("evaluate_preferences", "分析优先条件"),
        ("build_candidate_pool", "建立候选池"),
        ("rank_candidates", "按匹配度排序"),
    ]
    if task.action == "greet" and not preview_only:
        steps.extend([
            ("wait_for_approval", "等待授权"),
            ("contact", "执行联系"),
            ("verify_contact", "验证结果"),
        ])
    steps.append(("report", "输出报告"))
    blockers = task_blockers(task)
    if preview_only:
        blockers = [item for item in blockers if item != "target_success_count_exceeds_max_checks"]
    return {
        "executor": "action screen-recommend-detail",
        "mode": "preview_only" if preview_only else "plan_only",
        "status": "needs_clarification" if blockers else "planned",
        "blockers": blockers,
        "steps": [
            {"id": step, "label": label, "depends_on": [steps[index - 1][0]] if index else []}
            for index, (step, label) in enumerate(steps)
        ],
    }
