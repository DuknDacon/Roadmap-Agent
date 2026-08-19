from __future__ import annotations

from dataclasses import replace
import calendar
from datetime import date, datetime, timezone
from hashlib import sha1

from roadmap_agent.domain import RiskProfile, RoadmapRequest, Scenario
from roadmap_agent.orchestrator import build_conversation_graph, run_roadmap

from .schemas import (
    AllocationItem,
    EvidenceItem,
    RoadmapCreateRequest,
    RoadmapResponse,
    RoadmapRequestPatch,
    ScenarioResponse,
)
from .runtime import get_runtime


_CONVERSATION_GRAPHS = {}


def _conversation_graph(runtime):
    key = tuple(
        id(value)
        for value in (
            runtime.policy_repository,
            runtime.savings_repository,
            runtime.retriever,
            runtime.explainer,
            runtime.planner,
            runtime.conversation_store,
        )
    )
    if key not in _CONVERSATION_GRAPHS:
        store = runtime.conversation_store
        _CONVERSATION_GRAPHS[key] = build_conversation_graph(
            policy_repository=runtime.policy_repository,
            savings_repository=runtime.savings_repository,
            retriever=runtime.retriever,
            explainer=runtime.explainer,
            planner=runtime.planner,
            checkpointer=store.checkpointer if store else None,
            session_store=store,
        )
    return _CONVERSATION_GRAPHS[key]


RISK_PROFILE = {
    "stable": RiskProfile.CONSERVATIVE,
    "balanced": RiskProfile.BALANCED,
    "growth": RiskProfile.AGGRESSIVE,
}
ALLOCATION = {
    "savings": ("예·적금", "#2775d7"),
    "cash_equivalent": ("현금성 자산", "#82b3ef"),
    "diversified_investment": ("분산투자", "#20a464"),
    "unallocated_cash": ("미배분 금액", "#f3b73f"),
}
PRODUCT_TYPE = {
    "policy": "정부기여금 활용형",
    "savings": "원금 안정형",
    "investment": "분산투자형",
    "balanced": "균형 배분형",
}


def _age(birth_date: date, as_of: date) -> int:
    return as_of.year - birth_date.year - (
        (as_of.month, as_of.day) < (birth_date.month, birth_date.day)
    )


def _months(target: date, as_of: date) -> int:
    return (target.year - as_of.year) * 12 + target.month - as_of.month


def _target_date(horizon_months: int, as_of: date) -> date:
    absolute_month = as_of.year * 12 + as_of.month - 1 + horizon_months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _scenario(value: Scenario, badge: str) -> ScenarioResponse:
    expected = value.expected_max if value.kind in {"policy", "savings"} else value.expected_base
    allocations = []
    for key, amount in value.monthly_allocation.items():
        if amount <= 0:
            continue
        label, color = ALLOCATION.get(key, (key, "#738078"))
        allocations.append(AllocationItem(label=label, amount=amount, color=color))
    evidence = [
        EvidenceItem(
            title=item.title,
            organization="공식 제공기관",
            url=item.source_url,
        )
        for item in value.evidence
        if item.source_url
    ]
    if not evidence:
        evidence = [EvidenceItem(title="연결된 공식 근거 없음", organization="SeedUp", url="")]
    stable_id = sha1(f"{value.kind}:{value.title}".encode()).hexdigest()[:12]
    return ScenarioResponse(
        id=stable_id,
        badge=badge,
        title=value.title,
        productType=PRODUCT_TYPE.get(value.kind, "맞춤 시나리오"),
        monthlyAmount=sum(value.monthly_allocation.values()),
        expectedAmount=expected,
        principal=value.principal,
        goalRate=value.goal_achievement_rate,
        shortfall=value.shortfall,
        allocations=allocations,
        highlights=value.rationale,
        warnings=value.warnings,
        evidence=evidence,
        monthlyLimit=value.monthly_limit,
    )


def create_roadmap(payload: RoadmapCreateRequest) -> RoadmapResponse:
    today = date.today()
    request = RoadmapRequest(
        monthly_budget=payload.monthly_budget,
        horizon_months=_months(payload.target_date, today),
        target_amount=payload.target_amount,
        risk_profile=RISK_PROFILE[payload.risk_level or "balanced"],
        age=_age(payload.birth_date, today),
        annual_income=payload.previous_annual_income,
        previous_annual_income=payload.previous_annual_income,
        current_annual_income=payload.current_annual_income,
        monthly_take_home=payload.monthly_take_home,
        has_emergency_fund=payload.has_emergency_fund,
        max_investment_ratio=(
            payload.investment_cap / 100 if payload.investment_cap is not None else None
        ),
        region_code=f"{payload.region_province_code}:{payload.region_district_code}",
        is_employed=payload.employed,
        employment_type=payload.employment_type,
        is_sme_employee=payload.is_sme_employee,
        household_size=payload.household_size,
        is_married=payload.marital_status == "married",
        question=payload.question,
    )
    runtime = get_runtime()
    result = run_roadmap(
        request,
        policy_repository=runtime.policy_repository,
        savings_repository=runtime.savings_repository,
        retriever=runtime.retriever,
        explainer=None if payload.question.strip() else runtime.explainer,
    )
    conversation_status = None
    conversation_intent = None
    request_patch = None
    if payload.question.strip():
        if payload.thread_id is None:
            raise ValueError("대화 요청에는 threadId가 필요합니다.")
        conversation = _conversation_graph(runtime).invoke(
            str(payload.thread_id), request, result, payload.question
        )
        request = conversation.request
        result = conversation.result
        result = replace(result, chat_reply=conversation.reply)
        conversation_status = conversation.status.value
        conversation_intent = conversation.intent.value
        request_patch = RoadmapRequestPatch(
            monthlyBudget=request.monthly_budget,
            targetDate=_target_date(request.horizon_months, today),
            targetAmount=request.target_amount,
            hasEmergencyFund=request.has_emergency_fund,
            investmentCap=(
                round(request.max_investment_ratio * 100)
                if request.max_investment_ratio is not None
                else None
            ),
        )
    income_change = abs(payload.current_annual_income - payload.previous_annual_income)
    income_change_rate = income_change / max(payload.previous_annual_income, 1)
    income_note = (
        "직전년도와 현재 예상 연소득 차이가 커서 상품별 기준연도 확인이 필요합니다."
        if income_change_rate >= 0.2
        else "직전년도 과세소득과 현재 예상소득을 각각 자격과 납입여력에 반영했습니다."
    )
    alternative = result.alternatives[0] if result.alternatives else result.recommended
    return RoadmapResponse(
        recommended=_scenario(result.recommended, "최우선 추천"),
        alternative=_scenario(alternative, "대안"),
        summary=result.chat_reply or result.recommended_reason or income_note,
        explanation=result.chat_reply,
        recommendedReason=result.recommended_reason,
        alternativeReason=result.alternative_reason,
        chatReply=result.chat_reply,
        notice=income_note,
        generatedAt=datetime.now(timezone.utc),
        conversationStatus=conversation_status,
        conversationIntent=conversation_intent,
        requestPatch=request_patch,
    )
