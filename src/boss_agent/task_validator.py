"""Validation for the offline recruitment task contract."""

import json
import re

from boss_agent.task_schema import MULTI_VALUE_FIELDS, ConditionGroup, Criterion, RecruitmentTask, iter_criteria


SENSITIVE = re.compile(
    r"性别|女生|男生|女性|男性|女士|男士|限男|限女|男女|只要[男女]|仅限[男女]|[男女](?:优先|不限|性|士|生)|"
    r"(?<![\w])[男女](?![\w])|年龄|\d+\s*岁|婚育|婚姻|已婚|未婚|未育|生育|孕|民族|种族|宗教|"
    r"残疾|健康状况|户籍|籍贯|gender|sex\b|\bage\b|male\b|marital",
    re.IGNORECASE,
)


class RecruitmentTaskBlocked(ValueError):
    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__(", ".join(blockers))


def task_blockers(task: RecruitmentTask) -> list[str]:
    blockers = list(task.ambiguous_fields)
    if not task.job:
        blockers.append("job_required")
    if not task.hard_filters and not task.soft_preferences:
        blockers.append("criteria_required")
    if task.unsupported_conditions:
        blockers.append("unsupported_conditions")
    if task.action == "greet" and task.target_success_count is None:
        blockers.append("target_success_count_required")
    for field in ("max_checks", "target_success_count"):
        value = getattr(task, field)
        if value is not None and (type(value) is not int or value < 1):
            blockers.append(f"invalid_{field}")
    if any(is_sensitive(rule) for condition in (*task.hard_filters, *task.soft_preferences)
           for rule in iter_criteria(condition)):
        blockers.append("sensitive_criteria")
    blockers.extend(_conjunction_conflicts(task.hard_filters))
    if (
        type(task.target_success_count) is int and type(task.max_checks) is int
        and task.target_success_count > task.max_checks and not task.contact_limit_only
    ):
        blockers.append("target_success_count_exceeds_max_checks")
    return list(dict.fromkeys(blockers))


def is_sensitive(rule: Criterion) -> bool:
    return bool(SENSITIVE.search(" ".join((rule.field.replace("_", " "), rule.source, str(rule.value)))))


def _conjunction_conflicts(conditions: list) -> list[str]:
    allowed_by_field = {}
    duration_limits = {}
    blockers = []
    pending = list(conditions)
    while pending:
        rule = pending.pop(0)
        if isinstance(rule, ConditionGroup):
            if rule.operator == "AND":
                pending[0:0] = rule.conditions
            continue
        field = rule.field
        if field in {"hr_experience_months", "experience_months"} or field.startswith("experience_months:"):
            low, exclusive, high = duration_limits.get(field, (0, False, None))
            if rule.operator == "between" and (not isinstance(rule.value, list) or len(rule.value) != 2):
                blockers.append(f"invalid_{field}")
                continue
            lower = rule.value[0] if rule.operator == "between" else rule.value
            upper = rule.value[1] if rule.operator == "between" else rule.value
            if rule.operator in {"between", "equals", "gt", "gte"} and type(lower) is int:
                if lower > low:
                    low, exclusive = lower, rule.operator == "gt"
                elif lower == low:
                    exclusive |= rule.operator == "gt"
            if rule.operator in {"between", "equals", "lte"} and type(upper) is int:
                high = upper if high is None else min(high, upper)
            if high is not None and (low > high or low == high and exclusive):
                blockers.append(f"conflicting_{field}")
            duration_limits[field] = (low, exclusive, high)
        if rule.operator not in {"equals", "one_of"} or rule.field in MULTI_VALUE_FIELDS:
            continue
        values = set(rule.value) if isinstance(rule.value, list) else {rule.value}
        if rule.field in allowed_by_field:
            values &= allowed_by_field[rule.field]
        allowed_by_field[rule.field] = values
        if not values:
            blockers.append(f"conflicting_{rule.field}")
    return blockers


def hard_filters_pass(task: RecruitmentTask, findings: list[str]) -> bool:
    """Each hard filter requires an affirmative, positional evidence finding."""
    return len(findings) == len(task.hard_filters) and all(result == "supported" for result in findings)


def validate_approval(task: RecruitmentTask, candidate_ids: list[str], approval: object) -> bool:
    """Compare an explicit approval record with the task and ordered stable-ID shortlist.

    The future execution layer must establish the provenance of the approval.
    This function validates scope only and never enables an action.
    """
    if task_blockers(task) or task.action != "greet" or type(approval) is not dict:
        return False
    count = len(candidate_ids)
    if not count or task.target_success_count is None or count > task.target_success_count:
        return False
    if any(type(value) is not str or not value.strip() or value != value.strip()
           for value in candidate_ids) or len(set(candidate_ids)) != count:
        return False
    try:
        same_task = json.dumps(approval.get("task"), sort_keys=True) == json.dumps(task.to_dict(), sort_keys=True)
    except (TypeError, ValueError):
        return False
    return (
        approval.get("approved") is True
        and same_task
        and approval.get("candidate_ids") == candidate_ids
        and type(approval.get("count")) is int
        and approval["count"] == count
    )


def decode_model_clauses(raw: str, source: str) -> list[str]:
    """Accept only literal excerpts that preserve the entire original source."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_model_output") from exc
    if type(payload) is not dict or set(payload) != {"clauses"}:
        raise ValueError("invalid_model_output")
    clauses = payload["clauses"]
    if (
        type(clauses) is not list
        or not clauses
        or any(type(clause) is not str or not clause.strip() for clause in clauses)
        or "".join(clauses) != source
    ):
        raise ValueError("invalid_model_output")
    return clauses
