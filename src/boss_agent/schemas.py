from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class CandidateProfile:
    name: str
    city: str = ""
    years_of_experience: str = ""
    education: str = ""
    current_company: str = ""
    current_title: str = ""
    highlights: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class JobProfile:
    title: str
    city: str = ""
    salary_range: str = ""
    must_haves: list[str] = field(default_factory=list)
    nice_to_haves: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ScoreResult:
    match_score: int
    reply_priority: str
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

