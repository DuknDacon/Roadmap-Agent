from __future__ import annotations

from dataclasses import replace
import calendar
from datetime import date, datetime, timezone
from hashlib import sha1

from roadmap_agent.conversation import (
    FINANCIAL_INCOME_TAXED_QUESTION,
    HOUSEHOLD_MONTHLY_INCOME_QUESTION,
    IS_SME_EMPLOYEE_QUESTION,
    PREVIOUS_ANNUAL_INCOME_QUESTION,
)
from roadmap_agent.domain import RiskProfile, RoadmapRequest, Scenario
from roadmap_agent.orchestrator import build_conversation_graph, run_roadmap

from .schemas import (
    AllocationItem,
    EvidenceItem,
    MissingFieldDetail,
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


# 로드맵 생성 전 사전 체크 대상 필드 → 질문 문구. 상품마다 필요한 자격조건이
# 달라, DB 매칭 후보(PolicyBenefit.missing_qualification_fields)가 실제로
# 요구하는 필드만 여기서 걸러 로드맵 없이 먼저 물어본다. 새 상품이 다른
# 필드를 요구하게 되면 repositories.py 가 missing_qualification_fields 에
# 그 필드명을 채우고, 여기 매핑에 질문 문구 한 줄만 추가하면 된다.
_PRELAUNCH_FIELD_QUESTIONS: dict[str, str] = {
    "financial_income_taxed": FINANCIAL_INCOME_TAXED_QUESTION,
    "is_sme_employee": IS_SME_EMPLOYEE_QUESTION,
    "household_monthly_income": HOUSEHOLD_MONTHLY_INCOME_QUESTION,
    "previous_annual_income": PREVIOUS_ANNUAL_INCOME_QUESTION,
}
_PRELAUNCH_FIELD_INPUT_TYPES: dict[str, str] = {
    "financial_income_taxed": "boolean",
    "is_sme_employee": "boolean",
    "household_monthly_income": "number",
    "previous_annual_income": "number",
}


def _prelaunch_missing_fields(request: RoadmapRequest, runtime) -> list[str]:
    """DB 매칭 후보 전체가 요구하는, 사전 체크 대상 필드의 합집합.

    순수 DB 조회(policy_repository.find_candidates)만 쓰므로 LLM 호출이 없고
    수십 ms 안에 끝난다. 반환된 필드가 다음 호출에서 채워지면 그 필드는 더
    이상 어떤 후보의 missing_qualification_fields 에도 나타나지 않는다.

    레거시 4개 필드명뿐 아니라, DynamicGateRegistry가 채운 합성 키
    ("policy_id:gate_id")도 그대로 통과시킨다 — 두 종류 다
    missing_qualification_fields 안에서는 구분 없는 opaque 문자열이다.
    """
    if runtime.policy_repository is None:
        return []
    candidates = runtime.policy_repository.find_candidates(request)
    present = {
        field for candidate in candidates for field in candidate.missing_qualification_fields
    }
    return [name for name in present if name in _PRELAUNCH_FIELD_QUESTIONS or ":" in name]


def _missing_field_details(missing_fields: list[str], runtime) -> list[MissingFieldDetail]:
    """missing_fields 각 항목을 프론트가 바로 렌더할 수 있는 질문 메타데이터로 바꾼다.

    레거시 4개 필드는 _PRELAUNCH_FIELD_QUESTIONS에서, 동적 게이트(합성 키)는
    gate_registry에서 찾는다 — 동적 게이트는 상품마다 달라 라우터에 미리
    등록해둘 수 없으므로 여기서 직접 구조화된 형태로 실어 보낸다.
    """
    details: list[MissingFieldDetail] = []
    for name in missing_fields:
        if name in _PRELAUNCH_FIELD_QUESTIONS:
            details.append(
                MissingFieldDetail(
                    field=name,
                    question=_PRELAUNCH_FIELD_QUESTIONS[name],
                    inputType=_PRELAUNCH_FIELD_INPUT_TYPES.get(name, "text"),
                )
            )
            continue
        if ":" in name and runtime.gate_registry is not None:
            policy_id, gate_id = name.split(":", 1)
            gate = next(
                (g for g in runtime.gate_registry.gates_for(policy_id) if g.gate_id == gate_id),
                None,
            )
            if gate is not None:
                details.append(
                    MissingFieldDetail(
                        field=name,
                        question=gate.question,
                        hint=gate.hint or None,
                        inputType="boolean",
                    )
                )
    return details


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
        financial_income_taxed=payload.financial_income_taxed,
        household_monthly_income=payload.household_monthly_income,
        household_size=payload.household_size,
        is_married=payload.marital_status == "married",
        question=payload.question,
        dynamic_gate_answers=payload.dynamic_gate_answers,
    )
    runtime = get_runtime()

    # 로드맵을 계산하기 전에, DB 매칭 후보 중 사용자 입력만으로는 판정 못 하는
    # 필드가 걸리는 게 있으면 로드맵 없이 먼저 물어본다(한 번에 여러 개일 수
    # 있음 — ProfileAskForm 은 fields 배열을 그대로 받아 한 카드에 렌더한다).
    missing_fields = _prelaunch_missing_fields(request, runtime)
    if missing_fields:
        details = _missing_field_details(missing_fields, runtime)
        return RoadmapResponse(
            summary="맞춤 로드맵을 만들기 전에 확인이 필요합니다.",
            chatReply=" ".join(detail.question for detail in details),
            notice="추가 정보를 답변하시면 그 즉시 로드맵을 만들어 드립니다.",
            generatedAt=datetime.now(timezone.utc),
            conversationStatus="needs_input",
            missingFields=missing_fields,
            missingFieldDetails=details,
        )

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
    if payload.previous_annual_income is None:
        # 이 사용자에게 매칭된 정책 후보 중 직전년도 소득이 필요한 게 없어 사전
        # 체크(위)를 통과한 경우 — 안내 문구만 그 사실을 반영하고, 어떤 자격
        # 판정에도 현재 소득을 직전년도 소득 대신 쓰지 않는다(request.previous_annual_income
        # 은 None으로 그대로 유지돼 repositories.py가 정확히 "미확인"으로 취급함).
        income_note = "이번 추천에는 직전년도 소득 확인이 필요한 상품이 없어 현재 예상소득만 반영했습니다."
    else:
        income_change = abs(payload.current_annual_income - payload.previous_annual_income)
        income_change_rate = income_change / max(payload.previous_annual_income, 1)
        income_note = (
            "직전년도와 현재 예상 연소득 차이가 커서 상품별 기준연도 확인이 필요합니다."
            if income_change_rate >= 0.2
            else "직전년도 과세소득과 현재 예상소득을 각각 자격과 납입여력에 반영했습니다."
        )
    alternative = result.alternatives[0] if result.alternatives else result.recommended
    alternatives_list = (
        [_scenario(item, "대안") for item in result.alternatives]
        if result.alternatives
        else [_scenario(result.recommended, "대안")]
    )
    return RoadmapResponse(
        recommended=_scenario(result.recommended, "최우선 추천"),
        alternative=_scenario(alternative, "대안"),
        alternatives=alternatives_list,
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
