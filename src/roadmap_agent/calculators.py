from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .domain import RiskProfile


@dataclass(frozen=True)
class Projection:
    contributed: int
    minimum: int
    base: int
    maximum: int


def future_value_of_monthly_payments(monthly: int, months: int, annual_rate: float) -> int:
    """월말 납입을 가정한 세전 예상액. 실제 상품 계산은 상품별 규칙으로 교체한다."""
    if monthly < 0 or months < 0 or annual_rate <= -1:
        raise ValueError("계산 입력값이 유효하지 않습니다.")
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return monthly * months
    value = monthly * (((1 + monthly_rate) ** months - 1) / monthly_rate)
    return round(value)


def savings_projection(monthly: int, months: int, annual_rate: float = 0.03) -> Projection:
    contributed = monthly * months
    base = future_value_of_monthly_payments(monthly, months, annual_rate)
    return Projection(contributed, contributed, base, base)


def savings_product_projection(
    monthly: int,
    months: int,
    annual_base_rate: float,
    annual_preferential_rate: float,
    tax_rate: float = 0.154,
) -> Projection:
    """월말 적립식 단리와 이자소득세를 적용한 상품 만기 예상액."""
    if monthly < 0 or months < 1:
        raise ValueError("적금 상품 계산 입력값이 유효하지 않습니다.")
    if min(annual_base_rate, annual_preferential_rate, tax_rate) < 0 or tax_rate >= 1:
        raise ValueError("금리 또는 세율이 유효하지 않습니다.")
    contributed = monthly * months
    month_weights = months * (months + 1) / 2

    def amount(rate: float) -> int:
        gross_interest = monthly * rate / 12 * month_weights
        return round(contributed + gross_interest * (1 - tax_rate))

    base = amount(annual_base_rate)
    maximum = amount(max(annual_base_rate, annual_preferential_rate))
    return Projection(contributed, contributed, base, maximum)


def required_monthly_for_savings_product(
    target: int | None,
    months: int,
    annual_rate: float,
    tax_rate: float = 0.154,
) -> int | None:
    """상품과 동일한 적립식 단리·세율 기준으로 목표 월납입액을 계산한다."""
    if target is None:
        return None
    month_weights = months * (months + 1) / 2
    amount_per_won = months + annual_rate / 12 * month_weights * (1 - tax_rate)
    return ceil(target / amount_per_won)


RETURN_RANGES: dict[RiskProfile, tuple[float, float, float]] = {
    RiskProfile.CONSERVATIVE: (-0.02, 0.025, 0.06),
    RiskProfile.BALANCED: (-0.12, 0.045, 0.09),
    RiskProfile.AGGRESSIVE: (-0.25, 0.065, 0.13),
}


def investment_projection(monthly: int, months: int, risk: RiskProfile) -> Projection:
    """보장 수익률이 아닌 시나리오 범위. 특정 상품의 예측값으로 사용하지 않는다."""
    low, base, high = RETURN_RANGES[risk]
    contributed = monthly * months
    return Projection(
        contributed=contributed,
        minimum=future_value_of_monthly_payments(monthly, months, low),
        base=future_value_of_monthly_payments(monthly, months, base),
        maximum=future_value_of_monthly_payments(monthly, months, high),
    )


def allocation_for(risk: RiskProfile, has_emergency_fund: bool) -> tuple[int, int]:
    if not has_emergency_fund:
        return 85, 15
    return {
        RiskProfile.CONSERVATIVE: (80, 20),
        RiskProfile.BALANCED: (60, 40),
        RiskProfile.AGGRESSIVE: (30, 70),
    }[risk]


def achievement_rate(amount: int, target: int | None) -> float | None:
    return None if target is None else round(amount / target * 100, 1)


def investment_cap(risk: RiskProfile, horizon_months: int, user_cap: float | None) -> float:
    cap = {
        RiskProfile.CONSERVATIVE: 0.20,
        RiskProfile.BALANCED: 0.40,
        RiskProfile.AGGRESSIVE: 0.70,
    }[risk]
    if horizon_months < 12:
        cap = 0.0
    elif horizon_months <= 36:
        cap = min(cap, 0.30)
    if user_cap is not None:
        cap = min(cap, user_cap)
    return int(cap * 20) / 20  # 5% 단위 내림


def target_gap(amount: int, target: int | None) -> int | None:
    return None if target is None else max(target - amount, 0)


def required_monthly_for_target(target: int | None, months: int, annual_rate: float) -> int | None:
    if target is None:
        return None
    monthly_rate = annual_rate / 12
    factor = months if monthly_rate == 0 else ((1 + monthly_rate) ** months - 1) / monthly_rate
    return round(target / factor)


def additional_months_for_target(
    monthly: int, current_months: int, target: int | None, annual_rate: float, max_months: int = 120
) -> int | None:
    if target is None:
        return None
    for months in range(current_months, max_months + 1):
        if future_value_of_monthly_payments(monthly, months, annual_rate) >= target:
            return months - current_months
    return None
