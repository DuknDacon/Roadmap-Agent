from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from .domain import RiskProfile, RoadmapRequest


@dataclass(frozen=True)
class RiskAnswers:
    loss_tolerance: int
    loss_response: int
    maximum_investment_ratio: float

    def profile(self) -> RiskProfile:
        if not 1 <= self.loss_tolerance <= 3 or not 1 <= self.loss_response <= 3:
            raise ValueError("위험성향 응답은 1~3이어야 합니다.")
        if not 0 <= self.maximum_investment_ratio <= 1:
            raise ValueError("투자상품 최대 배분 비율은 0~1이어야 합니다.")
        score = self.loss_tolerance + self.loss_response
        if score <= 2:
            return RiskProfile.CONSERVATIVE
        if score <= 4:
            return RiskProfile.BALANCED
        return RiskProfile.AGGRESSIVE


@dataclass(frozen=True)
class IntakeRequest:
    birth_date: date | None = None
    annual_income: int | None = None
    monthly_take_home: int | None = None
    region_province_code: str | None = None
    region_district_code: str | None = None
    employment_type: str | None = None
    is_employed: bool | None = None
    is_sme_employee: bool | None = None
    household_size: int | None = None
    dependents: int | None = None
    is_married: bool | None = None
    monthly_budget: int | None = None
    target_year: int | None = None
    target_month: int | None = None
    target_amount: int | None = None
    risk_answers: RiskAnswers | None = None
    budget_after_essentials_confirmed: bool = False
    has_emergency_fund: bool | None = None

    def missing_initial_fields(self) -> list[str]:
        required = {
            "birth_date": self.birth_date,
            "annual_income": self.annual_income,
            "region_province_code": self.region_province_code,
            "region_district_code": self.region_district_code,
            "is_employed": self.is_employed,
            "monthly_budget": self.monthly_budget,
            "target_year": self.target_year,
            "target_month": self.target_month,
            "risk_answers": self.risk_answers,
            "has_emergency_fund": self.has_emergency_fund,
            "household_size": self.household_size,
            "is_married": self.is_married,
        }
        missing = [name for name, value in required.items() if value is None]
        if self.is_employed is True:
            if self.employment_type is None:
                missing.append("employment_type")
            if self.is_sme_employee is None:
                missing.append("is_sme_employee")
        if not self.budget_after_essentials_confirmed:
            missing.append("budget_after_essentials_confirmed")
        return missing

    def normalize(self, as_of: date) -> RoadmapRequest:
        missing = self.missing_initial_fields()
        if missing:
            raise ValueError("필수 입력 누락: " + ", ".join(missing))
        assert self.birth_date is not None
        assert self.target_year is not None and self.target_month is not None
        assert self.monthly_budget is not None and self.risk_answers is not None
        assert self.has_emergency_fund is not None
        if not 1 <= self.target_month <= 12:
            raise ValueError("target_month는 1~12여야 합니다.")
        target_day = calendar.monthrange(self.target_year, self.target_month)[1]
        target = date(self.target_year, self.target_month, target_day)
        months = (target.year - as_of.year) * 12 + target.month - as_of.month
        age = as_of.year - self.birth_date.year - (
            (as_of.month, as_of.day) < (self.birth_date.month, self.birth_date.day)
        )
        request = RoadmapRequest(
            monthly_budget=self.monthly_budget,
            horizon_months=months,
            target_amount=self.target_amount,
            risk_profile=self.risk_answers.profile(),
            age=age,
            annual_income=self.annual_income,
            monthly_take_home=self.monthly_take_home,
            has_emergency_fund=self.has_emergency_fund,
            max_investment_ratio=self.risk_answers.maximum_investment_ratio,
            region_code=f"{self.region_province_code}:{self.region_district_code}",
            employment_type=self.employment_type,
            is_employed=self.is_employed,
            is_sme_employee=self.is_sme_employee,
            household_size=self.household_size,
            dependents=self.dependents,
            is_married=self.is_married,
        )
        request.validate()
        return request
