from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

from .domain import RoadmapRequest


MONEY_RE = r"([\d,.]+)\s*(억|천만|만)?\s*원"


def _won(match: re.Match[str]) -> int:
    value = float(match.group(1).replace(",", ""))
    multiplier = {None: 1, "만": 10_000, "천만": 10_000_000, "억": 100_000_000}[
        match.group(2)
    ]
    return round(value * multiplier)


def apply_conversation_change(
    request: RoadmapRequest, message: str, *, as_of: date | None = None
) -> tuple[RoadmapRequest, list[str]]:
    """초안 UI의 제한된 대화 명령을 구조화된 요청 변경으로 변환한다."""
    text = " ".join(message.strip().split())
    changes: dict[str, object] = {}
    descriptions: list[str] = []

    target = re.search(rf"(?:목표금액|목표 금액|목표액)\D{{0,12}}{MONEY_RE}", text)
    if target:
        value = _won(target)
        changes["target_amount"] = value
        descriptions.append(f"목표금액 {value:,}원")

    monthly = re.search(
        rf"(?:월\s*(?:저축액|투입액|예산)|매달|월)\D{{0,12}}{MONEY_RE}", text
    )
    if monthly:
        value = _won(monthly)
        changes["monthly_budget"] = value
        descriptions.append(f"월 투입액 {value:,}원")

    ratio = re.search(r"(?:투자\s*비중|투자비율|투자\s*상한)\D{0,12}([\d.]+)\s*%", text)
    if ratio:
        value = float(ratio.group(1)) / 100
        changes["max_investment_ratio"] = value
        descriptions.append(f"투자비중 상한 {value:.0%}")
    elif re.search(r"(?:위험|투자\s*비중).{0,10}(?:더\s*)?(?:줄여|낮춰)", text):
        current = request.max_investment_ratio
        if current is None:
            current = {
                "conservative": 0.2,
                "balanced": 0.4,
                "aggressive": 0.7,
            }[request.risk_profile.value]
        value = max(round(current - 0.1, 2), 0)
        changes["max_investment_ratio"] = value
        descriptions.append(f"투자비중 상한 {value:.0%}")
    elif re.search(r"(?:위험|투자\s*비중).{0,10}(?:더\s*)?(?:늘려|높여)", text):
        current = request.max_investment_ratio or 0
        value = min(round(current + 0.1, 2), 1)
        changes["max_investment_ratio"] = value
        descriptions.append(f"투자비중 상한 {value:.0%}")

    target_date = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", text)
    if target_date:
        year, month = map(int, target_date.groups())
        if not 1 <= month <= 12:
            raise ValueError("목표 월은 1~12 사이여야 합니다.")
        today = as_of or date.today()
        horizon = (year - today.year) * 12 + month - today.month
        changes["horizon_months"] = horizon
        descriptions.append(f"목표시점 {year}년 {month}월")

    if re.search(r"비상(?:금|자금).{0,8}(?:없|미보유)", text):
        changes["has_emergency_fund"] = False
        descriptions.append("비상자금 미보유")
    elif re.search(r"비상(?:금|자금).{0,8}(?:있|보유)", text):
        changes["has_emergency_fund"] = True
        descriptions.append("비상자금 보유")

    if not changes:
        raise ValueError(
            "변경할 조건을 찾지 못했습니다. 월 투입액, 목표금액, 목표시점, "
            "투자비중 또는 비상자금 여부를 포함해 주세요."
        )
    updated = replace(request, **changes)
    updated.validate()
    return updated, descriptions
