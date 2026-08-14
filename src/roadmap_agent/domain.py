from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RiskProfile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class RoadmapRequest:
    monthly_budget: int
    horizon_months: int
    target_amount: int | None
    risk_profile: RiskProfile
    age: int | None = None
    annual_income: int | None = None
    monthly_take_home: int | None = None
    has_emergency_fund: bool = False
    max_investment_ratio: float | None = None
    region_code: str | None = None
    employment_type: str | None = None
    is_employed: bool | None = None
    is_sme_employee: bool | None = None
    household_size: int | None = None
    dependents: int | None = None
    is_married: bool | None = None
    question: str = ""

    def validate(self) -> None:
        if self.monthly_budget <= 0:
            raise ValueError("monthly_budget은 0보다 커야 합니다.")
        if not 6 <= self.horizon_months <= 120:
            raise ValueError("horizon_months는 6~120개월이어야 합니다.")
        if self.target_amount is not None and self.target_amount <= 0:
            raise ValueError("target_amount는 0보다 커야 합니다.")
        if self.age is not None and not 14 <= self.age <= 100:
            raise ValueError("age는 14~100이어야 합니다.")
        if self.annual_income is not None and self.annual_income < 0:
            raise ValueError("annual_income은 음수일 수 없습니다.")
        if self.monthly_take_home is not None and self.monthly_take_home <= 0:
            raise ValueError("monthly_take_home은 0보다 커야 합니다.")
        if self.monthly_take_home is not None and self.monthly_budget > self.monthly_take_home:
            raise ValueError("월 투입액은 월 실수령액보다 클 수 없습니다.")
        if self.max_investment_ratio is not None and not 0 <= self.max_investment_ratio <= 1:
            raise ValueError("max_investment_ratio는 0~1이어야 합니다.")
        if self.household_size is not None and self.household_size < 1:
            raise ValueError("household_size는 1 이상이어야 합니다.")
        if self.dependents is not None and self.dependents < 0:
            raise ValueError("dependents는 음수일 수 없습니다.")


@dataclass(frozen=True)
class Evidence:
    title: str
    source_url: str
    path: str
    score: int


@dataclass(frozen=True)
class Scenario:
    kind: str
    title: str
    monthly_allocation: dict[str, int]
    expected_min: int
    expected_base: int
    expected_max: int
    goal_achievement_rate: float | None
    principal: int
    rationale: list[str]
    warnings: list[str]
    unallocated_cash: int = 0
    shortfall: int | None = None
    required_monthly_budget: int | None = None
    additional_months: int | None = None
    score: float | None = None
    evidence: list[Evidence] = field(default_factory=list)
    data_status: str = "ready"


@dataclass(frozen=True)
class RoadmapResult:
    recommended: Scenario
    alternatives: list[Scenario]
    assumptions: dict[str, Any]
    disclaimer: str
    explanation: str | None = None
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
