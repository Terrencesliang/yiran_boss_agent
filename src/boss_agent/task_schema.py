"""Task and condition types shared by planning and offline evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Literal
from boss_agent.boss_major_filters import BOSS_MAJOR_CATEGORY, BOSS_MAJOR_SHORTCUTS


@dataclass(frozen=True)
class Criterion:
    field: str
    operator: Literal["equals", "one_of", "between", "has_any", "gt", "gte", "lte", "verify"]
    value: str | int | list[str] | list[int]
    source: str


@dataclass(frozen=True)
class ConditionGroup:
    operator: Literal["AND", "OR", "NOT"]
    conditions: list[Condition]


type Condition = Criterion | ConditionGroup

# Retain the field emitted by Phase 01 for previously saved task JSON.
LEGACY_EXPERIENCE_FIELDS = {"人力资源": "hr_experience_months"}
MULTI_VALUE_FIELDS = {"tools", "skills", "certificates", "industry_experience", "hands_on_tools"}
BOSS_PAGE_FILTERS = {
    "年龄": (),
    "活跃度": ("刚刚活跃", "今日活跃", "3日内活跃", "本周活跃", "本月活跃"),
    "性别": ("男", "女"),
    "近期没有看过": ("近14天没有",),
    "是否与同事交换简历": ("近一个月没有",),
    "院校": ("985", "211", "双一流院校", "留学", "公办本科", "QS 100", "QS 500"),
    "牛人关键词": ("Office", "数据分析", "人力资源", "人事行政", "员工关系", "行政管理", "人力资源管理师", "猎头"),
    "专业": (*BOSS_MAJOR_CATEGORY, *BOSS_MAJOR_SHORTCUTS),
    "跳槽频率": ("5年少于3份", "平均每份工作大于1年"),
}


def validate_boss_page_filters(value):
    if not isinstance(value, dict):
        raise ValueError("BOSS 页面筛选包含不支持的选项")
    for key, option in value.items():
        if key == "年龄":
            match = re.fullmatch(r"([0-9]{2})-([0-9]{2})", option) if isinstance(option, str) else None
            if not match or not 16 <= int(match[1]) <= int(match[2]) <= 99:
                raise ValueError("年龄请填写 16–99 之间的区间，例如 25-35")
        elif key == "专业":
            names = option.split(",") if isinstance(option, str) else []
            if not 1 <= len(names) <= 5 or len(set(names)) != len(names) or any(
                name not in BOSS_PAGE_FILTERS[key] for name in names
            ):
                raise ValueError("BOSS 专业最多选择 5 项，且须使用平台选项")
        elif key not in BOSS_PAGE_FILTERS or option not in BOSS_PAGE_FILTERS[key]:
            raise ValueError("BOSS 页面筛选包含不支持的选项")
    return value


def iter_criteria(condition: Condition):
    if isinstance(condition, Criterion):
        yield condition
    else:
        for child in condition.conditions:
            yield from iter_criteria(child)


@dataclass(frozen=True)
class RecruitmentTask:
    job: str | None
    hard_filters: list[Condition]
    soft_preferences: list[Condition]
    max_checks: int | None
    target_success_count: int | None
    action: Literal["screen", "greet"]
    ambiguous_fields: list[str] = field(default_factory=list)
    unsupported_conditions: list[str] = field(default_factory=list)
    platform: Literal["boss_zhipin"] = "boss_zhipin"
    entry_page: Literal["recommend"] = "recommend"
    approval_required: bool = True
    dry_run: bool = True
    contact_limit_only: bool = False
    boss_page_filters: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = asdict(self)
        if not self.boss_page_filters:
            result.pop("boss_page_filters")
        return result
