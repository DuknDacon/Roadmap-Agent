from __future__ import annotations

from uuid import uuid4

from app.main import roadmap
from app.runtime import Runtime
from app.schemas import RoadmapCreateRequest
import app.service as service

from roadmap_agent.ports import PolicyBenefit


class EmptyPolicies:
    def find_candidates(self, request):
        return []


class FinancialIncomeTaxedGapPolicies:
    """financial_income_taxed가 없으면 이 필드가 걸리는 정책 후보 1건을 반환한다
    (실제 청년미래적금처럼 우대형+중위소득 조건이 있는 상품을 흉내)."""

    def find_candidates(self, request):
        missing = (
            ("financial_income_taxed",)
            if request.financial_income_taxed is None
            else ()
        )
        return [
            PolicyBenefit(
                policy_id="P1",
                name="청년미래적금",
                eligible=True,
                monthly_limit=500_000,
                estimated_support=1_000_000,
                maturity_months=24,
                source_url="",
                effective_date="2026-08-31",
                reason="테스트용",
                missing_qualification_fields=missing,
            )
        ]


class EmptySavings:
    def find_candidates(self, request):
        return []


class EmptyRetriever:
    def search(self, query, limit=3):
        return []


PAYLOAD = {
    "birthDate": "1998-01-01",
    "previousAnnualIncome": 40_000_000,
    "currentAnnualIncome": 42_000_000,
    "region": "서울특별시 · 마포구",
    "regionProvinceCode": "11",
    "regionDistrictCode": "11440",
    "householdSize": 1,
    "maritalStatus": "single",
    "employed": True,
    "employmentType": "employee",
    "isSmeEmployee": False,
    "monthlyTakeHome": 3_000_000,
    "monthlyBudget": 800_000,
    "targetDate": "2029-08-01",
    "targetAmount": 30_000_000,
    "hasEmergencyFund": True,
    "riskLevel": "balanced",
    "investmentCap": 30,
}


TEST_RUNTIME = Runtime(
    policy_repository=EmptyPolicies(),
    savings_repository=EmptySavings(),
    retriever=EmptyRetriever(),
)


def setup_module():
    service.get_runtime = lambda: TEST_RUNTIME


def test_risk_level_and_investment_cap_are_optional():
    payload = {**PAYLOAD, "riskLevel": None, "investmentCap": None}
    response = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    assert response.recommended is not None


def test_recommendation_reason_question_uses_agentic_reply():
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD, question="왜 이 상품을 추천했어?", threadId=uuid4()
    ))
    assert response.chat_reply.startswith("현재 최우선안은")


def test_nextjs_relative_risk_suggestion_recalculates():
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD, question="위험을 더 줄여줘", threadId=uuid4()
    ))
    assert response.chat_reply == "투자비중 상한 20% 조건을 반영해 전체 로드맵을 다시 계산했습니다."
    assert response.conversation_intent == "condition_change"
    assert response.request_patch.investment_cap == 20


def test_unclear_question_returns_one_clarification():
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD, question="더 좋은 걸로 해줘", threadId=uuid4()
    ))
    assert "조건을 변경하려는 것인지" in response.chat_reply
    assert response.conversation_status == "needs_input"


def test_thread_keeps_previous_product_for_short_followup():
    thread_id = uuid4()
    first = roadmap(RoadmapCreateRequest(
        **PAYLOAD, question="대안 상품은 납입 한도가 없어?", threadId=thread_id
    ))
    second = roadmap(RoadmapCreateRequest(**PAYLOAD, question="왜?", threadId=thread_id))

    assert first.alternative.title in second.chat_reply


def test_other_product_question_uses_candidate_search_intent():
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD,
        question="미래적금 말고 다른 정책 상품 있어?",
        threadId=uuid4(),
    ))

    assert response.conversation_intent == "product_alternatives"
    assert "가구 전체의 월소득" not in response.chat_reply


def test_product_ranking_followup_does_not_fall_through_to_rag():
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD,
        question="조건은 그대로인데, 이 외의 적금에 대한 순위를 알고싶어",
        threadId=uuid4(),
    ))

    assert response.conversation_intent == "product_ranking"
    assert "공식 근거 문서" not in response.chat_reply
    assert "정부기여금" not in response.chat_reply


def test_first_call_asks_before_roadmap_when_db_candidate_needs_financial_income_taxed():
    """DB 매칭 후보가 financial_income_taxed를 필요로 하면, 로드맵 없이 그
    질문부터 먼저 반환해야 한다(사용자가 먼저 물어볼 필요 없이 능동적으로)."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.alternative is None
    assert response.conversation_status == "needs_input"
    assert response.missing_fields == ["financial_income_taxed"]
    assert response.chat_reply == "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"


def test_second_call_with_financial_income_taxed_answered_builds_real_roadmap():
    """답변(financialIncomeTaxed)이 채워지면 사전 체크를 건너뛰고 실제 로드맵을
    만들어야 한다 — 같은 스레드에서 무한히 되묻지 않는지 확인."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, financialIncomeTaxed=False, threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.conversation_status != "needs_input"
    assert response.recommended is not None


def test_pre_check_does_not_trigger_when_no_candidate_needs_the_field():
    """정책 후보가 아예 없거나 이 필드가 필요 없으면(기존 테스트 전부 이 경우)
    사전 체크는 걸리지 않고 기존처럼 즉시 로드맵이 나온다 — 회귀 확인."""
    response = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
    assert response.recommended is not None
    assert response.conversation_status is None
