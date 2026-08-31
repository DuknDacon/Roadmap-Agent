from __future__ import annotations

import functools
import os
import re
import sqlite3
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .domain import RoadmapRequest


# `median_income` 테이블(SHARED_DB_PATH) 조회가 안 될 때만 쓰는 최후 폴백값.
# 매년 갱신은 여기가 아니라 DB 행 추가로 한다 — 이 dict는 DB가 없는 로컬
# 개발/테스트 환경에서도 함수가 동작하게 하려는 용도일 뿐이다.
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


@functools.lru_cache(maxsize=1)
def _median_income_table_from_db() -> dict[int, tuple[int, ...]]:
    """`median_income` 테이블에서 연도별 1~7인 가구 기준 중위소득을 읽는다.

    SHARED_DB_PATH가 없거나 테이블이 비어 있으면(로컬 테스트 등) 빈 dict를
    반환해 호출부가 `MEDIAN_INCOME_BY_YEAR` 폴백으로 넘어가게 한다.
    """
    path = os.getenv("SHARED_DB_PATH")
    if not path:
        return {}
    try:
        connection = sqlite3.connect(path, timeout=5)
        try:
            rows = connection.execute(
                "SELECT effective_year, household_size, median_monthly_income "
                "FROM median_income ORDER BY effective_year, household_size"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    table: dict[int, list[int]] = {}
    for year, _household_size, amount in rows:
        table.setdefault(year, []).append(amount)
    return {year: tuple(values) for year, values in table.items()}


def median_income_monthly(year: int, household_size: int) -> int | None:
    values = _median_income_table_from_db().get(year) or MEDIAN_INCOME_BY_YEAR.get(year)
    if values is None or household_size < 1:
        return None
    if household_size <= len(values):
        return values[household_size - 1]
    increment = values[-1] - values[-2]
    return values[-1] + increment * (household_size - len(values))


def median_income_limit(text: str, year: int, household_size: int) -> tuple[float, int] | None:
    matches = MEDIAN_INCOME_PERCENT_RE.findall(text)
    base = median_income_monthly(year, household_size)
    if not matches or base is None:
        return None
    distinct_ratios = {float(value) for value in matches}
    if len(distinct_ratios) > 1:
        # 가구유형(독립가구/원가구)이나 신청유형(Ⅰ/Ⅱ유형)별로 기준 비율이 갈리는
        # 정책은 자유 서술 텍스트만으로 어느 비율이 적용되는지 판별할 수 없다.
        # 임의로 하나를 골라 확정 판정을 내리는 대신 확인 필요로 남긴다.
        return None
    ratio = distinct_ratios.pop() / 100
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
