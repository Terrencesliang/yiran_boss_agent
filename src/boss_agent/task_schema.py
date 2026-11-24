"""Task and condition types shared by planning and offline evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
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
def _graduation_years():
    """BOSS 的毕业年份选项随年份滚动，这里按当前年份生成。"""
    year = date.today().year % 100
    return (f"{year}年毕业", f"{year+1}年毕业", f"{year+1}年后毕业")


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
    "求职状态": ("离职-随时到岗", "在职-暂不考虑", "在职-考虑机会", "在职-月内到岗"),
    "学历要求": ("初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士"),
    "经验要求": ("在校/应届", *_graduation_years(), "1年以内", "1-3年", "3-5年", "5-10年", "10年以上"),
    "薪资待遇": ("3K以下", "3-5K", "5-10K", "10-20K", "20-50K", "50K以上"),
    "只看第一学历": ("是",),
}


def validate_boss_page_filters(value):
    if not isinstance(value, dict):
        raise ValueError("BOSS 页面筛选包含不支持的选项")
    for key, option in value.items():
        if key == "年龄":
            match = re.fullmatch(r"([0-9]{2})-([0-9]{2})", option) if isinstance(option, str) else None
            if not match or not 16 <= int(match[1]) <= int(match[2]) <= 99:
                raise ValueError("年龄请填写 16–99 之间的区间，例如 25-35")
        elif key == "只看第一学历":
            if option != "是" or value.get("院校") != "公办本科":
                raise ValueError("「只看第一学历」需要同时把院校设为「公办本科」")
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
    # 判定规则：strict_all=全部条件 100%；strict_core=核心全满足；weighted=核心达标 + 加分项补足
    reference_conditions: list[str] = field(default_factory=list)
    decision_mode: str = "strict_all"
    min_core_percent: int = 100
    min_bonus_count: int = 0

    def to_dict(self) -> dict:
        result = asdict(self)
        if not self.boss_page_filters:
            result.pop("boss_page_filters")
        return result
