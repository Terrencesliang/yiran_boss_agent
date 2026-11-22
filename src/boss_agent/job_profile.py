"""Editable, file-backed job profiles and recruitment templates."""

from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Any

from boss_agent.task_schema import Condition, ConditionGroup, Criterion, RecruitmentTask, iter_criteria
from boss_agent.task_validator import SENSITIVE, is_sensitive


@dataclass(frozen=True)
class ScoreDimension:
    name: str
    condition: Condition
    weight: float


@dataclass(frozen=True)
class RecruitmentTemplate:
    requirements: list[Condition]
    priority_skills: list[Condition]
    scoring: list[ScoreDimension]


@dataclass(frozen=True)
class JobProfile:
    name: str
    description: str
    defaults: RecruitmentTemplate
    templates: dict[str, RecruitmentTemplate] = field(default_factory=dict)

    def select(self, template: str | None) -> RecruitmentTemplate:
        if template is None:
            return self.defaults
        if type(template) is not str or template not in self.templates:
            raise ValueError("unknown_recruitment_template")
        return self.templates[template]


def load_condition(value: Any, depth: int = 0) -> Condition:
    if depth > 16 or type(value) is not dict:
        raise ValueError("invalid_condition")
    if set(value) == {"operator", "conditions"}:
        operator, children = value["operator"], value["conditions"]
        if (
            operator not in ("AND", "OR", "NOT") or type(children) is not list
            or (len(children) != 1 if operator == "NOT" else len(children) < 2)
        ):
            raise ValueError("invalid_condition_group")
        return ConditionGroup(operator, [load_condition(child, depth + 1) for child in children])
    if set(value) != {"field", "operator", "value", "source"}:
        raise ValueError("invalid_condition")
    name, operator, expected, source = (value[key] for key in ("field", "operator", "value", "source"))
    if not all(type(part) is str and part.strip() for part in (name, operator, source)):
        raise ValueError("invalid_condition")
    def scalar(item: Any) -> bool:
        return type(item) is int or (type(item) is str and bool(item.strip()))

    if operator in ("one_of", "has_any"):
        valid = type(expected) is list and bool(expected) and all(map(scalar, expected))
    elif operator == "between":
        valid = (type(expected) is list and len(expected) == 2
                 and all(type(item) is int for item in expected) and expected[0] <= expected[1])
    elif operator == "verify":
        valid = type(expected) is str and bool(expected.strip())
    else:
        valid = operator in ("equals", "gte", "lte") and scalar(expected)
        if operator == "gt":
            valid = type(expected) is int
    if not valid:
        raise ValueError("invalid_condition_value")
    if name in {"experience_months", "hr_experience_months"} or name.startswith("experience_months:"):
        amounts = expected if type(expected) is list else [expected]
        if operator not in {"equals", "gt", "gte", "lte", "between"} or any(
            type(amount) is not int or amount < 0 for amount in amounts
        ):
            raise ValueError("invalid_experience_duration")
    if name == "graduation_year":
        years = expected if type(expected) is list else [expected]
        if operator not in {"equals", "one_of"} or any(type(year) is not int or not 1900 <= year <= 2099 for year in years):
            raise ValueError("invalid_graduation_year")
    return Criterion(name, operator, expected, source)


def _condition_list(items: Any) -> list[Condition]:
    if type(items) is not list:
        raise ValueError("invalid_condition_list")
    result = [load_condition(item) for item in items]
    if any(is_sensitive(rule) for condition in result for rule in iter_criteria(condition)):
        raise ValueError("unsupported_sensitive_recruitment_condition")
    return result


def _template(data: Any, defaults: RecruitmentTemplate) -> RecruitmentTemplate:
    if type(data) is not dict or set(data) - {"requirements", "priority_skills", "scoring"}:
        raise ValueError("invalid_recruitment_template")
    requirements = _condition_list(data["requirements"]) if "requirements" in data else defaults.requirements
    skills = _condition_list(data["priority_skills"]) if "priority_skills" in data else defaults.priority_skills
    dimensions = defaults.scoring
    if "scoring" in data:
        if type(data["scoring"]) is not list:
            raise ValueError("invalid_scoring")
        dimensions = []
        names = set()
        for item in data["scoring"]:
            if type(item) is not dict or set(item) != {"name", "condition", "weight"}:
                raise ValueError("invalid_scoring_dimension")
            name, weight = item["name"], item["weight"]
            if (
                type(name) is not str or not name.strip() or name in names or SENSITIVE.search(name)
                or type(weight) not in (int, float) or not 0 < weight <= 100 or not isfinite(weight)
            ):
                raise ValueError("invalid_scoring_dimension")
            names.add(name)
            dimensions.append(ScoreDimension(name, _condition_list([item["condition"]])[0], weight))
    return RecruitmentTemplate(requirements, skills, dimensions)


def load_job_profile(data: Any) -> JobProfile:
    if type(data) is not dict or set(data) - {"name", "description", "requirements", "priority_skills", "scoring", "templates"}:
        raise ValueError("invalid_job_profile")
    name, description = data.get("name"), data.get("description", "")
    if type(name) is not str or not name.strip() or SENSITIVE.search(name) or type(description) is not str:
        raise ValueError("invalid_job_profile")
    defaults = _template(
        {key: data[key] for key in ("requirements", "priority_skills", "scoring") if key in data},
        RecruitmentTemplate([], [], []),
    )
    templates = data.get("templates", {})
    if type(templates) is not dict or any(type(key) is not str or not key.strip() for key in templates):
        raise ValueError("invalid_template_catalog")
    return JobProfile(name.strip(), description, defaults, {
        key: _template(value, defaults) for key, value in templates.items()
    })


def apply_job_profile(
    task: RecruitmentTask, profile: JobProfile, template: str | None = None,
) -> RecruitmentTask:
    if task.job is not None and task.job != profile.name:
        raise ValueError("job_profile_mismatch")
    selected = profile.select(template)
    return replace(
        task, job=profile.name, hard_filters=[*selected.requirements, *task.hard_filters],
        soft_preferences=[*selected.priority_skills, *task.soft_preferences],
    )
