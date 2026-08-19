from __future__ import annotations

from uuid import uuid4

from app.main import roadmap
from app.runtime import Runtime
from app.schemas import RoadmapCreateRequest
import app.service as service


class EmptyPolicies:
    def find_candidates(self, request):
        return []


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
