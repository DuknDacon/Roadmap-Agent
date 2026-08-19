from __future__ import annotations

import re
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .domain import RoadmapRequest


MEDIAN_INCOME_BY_YEAR: dict[int, tuple[int, ...]] = {
    # 보건복지부 연도별 기준 중위소득, 월 기준 1~7인 가구.
    2025: (
        2_392_013,
        3_932_658,
        5_025_353,
        6_097_773,
        7_108_192,
        8_064_805,
        8_988_428,
    ),
    2026: (
        2_564_238,
        4_199_292,
        5_359_036,
        6_494_738,
        7_556_719,
        8_555_952,
        9_515_150,
    ),
}
MEDIAN_INCOME_SOURCE_URL = "https://www.mohw.go.kr/menu.es?mid=a10708010900"
MEDIAN_INCOME_PERCENT_RE = re.compile(r"중위소득\s*([\d.]+)\s*%")


def median_income_monthly(year: int, household_size: int) -> int | None:
    values = MEDIAN_INCOME_BY_YEAR.get(year)
    if values is None or household_size < 1:
        return None
    if household_size <= len(values):
        return values[household_size - 1]
    increment = values[-1] - values[-2]
    return values[-1] + increment * (household_size - len(values))


def median_income_limit(text: str, year: int, household_size: int) -> tuple[float, int] | None:
    match = MEDIAN_INCOME_PERCENT_RE.search(text)
    base = median_income_monthly(year, household_size)
    if match is None or base is None:
        return None
    ratio = float(match.group(1)) / 100
    return ratio, round(base * ratio)


def effective_household_monthly_income(request: "RoadmapRequest") -> int | None:
    """가구 전체 월소득을 명시하지 않았어도, 1인가구라면 본인 소득으로 대신 계산한다.

    가구원 수가 1명이면 가구소득은 본인 소득과 같으므로 별도로 물어볼 필요가
    없다. 2명 이상이면 다른 가구원의 소득을 알 수 없어 계속 사용자에게 확인해야
    한다.
    """
    if request.household_monthly_income is not None:
        return request.household_monthly_income
    if request.household_size == 1:
        annual = request.current_annual_income or request.previous_annual_income
        if annual is not None:
            return annual // 12
    return None
