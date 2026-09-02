from __future__ import annotations

from uuid import uuid4

from app.main import roadmap
from app.runtime import Runtime
from app.schemas import RoadmapCreateRequest
import app.service as service

from roadmap_agent.dynamic_gates import DynamicGate, DynamicGateRegistry
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


class MultiFieldGapPolicies:
    """한 후보가 financial_income_taxed 와 is_sme_employee 를 동시에 요구하는
    경우(우대형+금융소득 조건이 모두 있는 상품)를 흉내 — 사전 체크가 여러
    필드를 한 번에 물어보는지 확인하기 위함."""

    def find_candidates(self, request):
        missing = []
        if request.financial_income_taxed is None:
            missing.append("financial_income_taxed")
        if request.is_sme_employee is None:
            missing.append("is_sme_employee")
        return [
            PolicyBenefit(
                policy_id="P2",
                name="복합조건 우대적금",
                eligible=True,
                monthly_limit=500_000,
                estimated_support=1_000_000,
                maturity_months=24,
                source_url="",
                effective_date="2026-08-31",
                reason="테스트용",
                missing_qualification_fields=tuple(missing),
            )
        ]


class HouseholdIncomeGapPolicies:
    """household_monthly_income이 없으면 이 필드가 걸리는 정책 후보 1건을
    반환한다(2인 이상 가구의 중위소득 조건 상품을 흉내) — 사전 체크가
    financial_income_taxed 전용이 아니라 일반화됐는지 확인하기 위함."""

    def find_candidates(self, request):
        missing = (
            ("household_monthly_income",)
            if request.household_monthly_income is None
            else ()
        )
        return [
            PolicyBenefit(
                policy_id="P3",
                name="희망저축계좌",
                eligible=True,
                monthly_limit=300_000,
                estimated_support=500_000,
                maturity_months=36,
                source_url="",
                effective_date="2026-08-31",
                reason="테스트용",
                missing_qualification_fields=missing,
            )
        ]


class PreviousIncomeGapPolicies:
    """previous_annual_income이 없으면 이 필드가 걸리는 정책 후보 1건을
    반환한다(소득상한이 있는 상품이 온보딩 값이 아니라 직전년도 실제 소득을
    요구하는 경우를 흉내) — 온보딩 폼에서 이 필드를 제거한 뒤에도 사전
    체크가 실제로 물어보는지 확인하기 위함."""

    def find_candidates(self, request):
        missing = (
            ("previous_annual_income",)
            if request.previous_annual_income is None
            else ()
        )
        return [
            PolicyBenefit(
                policy_id="P4",
                name="소득상한 있는 정책상품",
                eligible=True,
                monthly_limit=300_000,
                estimated_support=500_000,
                maturity_months=36,
                source_url="",
                effective_date="2026-08-31",
                reason="테스트용",
                missing_qualification_fields=missing,
            )
        ]


class DynamicGateGapPolicies:
    """DynamicGateRegistry가 발견한, 4개 하드코딩 필드를 넘어서는 예/아니오
    게이트(청년예술인 상품의 예술활동증명 같은)가 걸린 정책 후보 1건을 흉내."""

    def find_candidates(self, request):
        missing = (
            ("P5:artist_certification",)
            if request.dynamic_gate_answers.get("P5:artist_certification") is None
            else ()
        )
        eligible = request.dynamic_gate_answers.get("P5:artist_certification") is not False
        return [
            PolicyBenefit(
                policy_id="P5",
                name="청년예술인 예술활동 적립계좌",
                eligible=eligible,
                monthly_limit=300_000,
                estimated_support=500_000,
                maturity_months=36,
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


def test_government_contribution_legal_basis_question_is_financial_qa_not_policy_eligibility():
    """"정부기여금"은 POLICY_TERMS에 걸리지만, "법적 근거"를 묻는 순수 정보성
    질문까지 policy_eligibility로 오분류하면 안 된다(RAG 테스트 #7 재현) —
    질문과 무관한 자격판정 상품 목록만 돌아오고 실제 질문엔 전혀 답을 안 하던
    버그."""
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD,
        question="청년미래적금 정부기여금은 어떤 법적 근거로 지급돼?",
        threadId=uuid4(),
    ))

    assert response.conversation_intent == "financial_qa"


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


def test_household_monthly_income_pre_check_asks_then_builds_roadmap_once_answered():
    """household_monthly_income 도 같은 사전 체크 메커니즘으로 물어봐야 한다 —
    financial_income_taxed 전용이 아니라 일반화됐는지 확인."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=HouseholdIncomeGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        first = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
        assert first.recommended is None
        assert first.missing_fields == ["household_monthly_income"]
        assert first.chat_reply == "가구 전체의 월소득은 얼마인가요?"

        payload = {**PAYLOAD, "householdMonthlyIncome": 3_500_000}
        second = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert second.conversation_status != "needs_input"
    assert second.recommended is not None


def test_previous_annual_income_pre_check_asks_then_builds_roadmap_once_answered():
    """온보딩 폼에서 직전년도 연 소득 항목을 뺀 뒤에도, 그 값을 실제로 요구하는
    상품이 매칭되면 로드맵 없이 먼저 물어봐야 한다 — 현재 연 소득과 같다고
    조용히 가정해버리면 소득상한 자격 판정이 틀릴 수 있다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=PreviousIncomeGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        payload = {**PAYLOAD, "previousAnnualIncome": None}
        first = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
        assert first.recommended is None
        assert first.missing_fields == ["previous_annual_income"]
        assert "직전년도" in first.chat_reply

        payload = {**PAYLOAD, "previousAnnualIncome": 38_000_000}
        second = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert second.conversation_status != "needs_input"
    assert second.recommended is not None


def test_pre_check_does_not_trigger_when_no_candidate_needs_the_field():
    """정책 후보가 아예 없거나 이 필드가 필요 없으면(기존 테스트 전부 이 경우)
    사전 체크는 걸리지 않고 기존처럼 즉시 로드맵이 나온다 — 회귀 확인."""
    response = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
    assert response.recommended is not None
    assert response.conversation_status is None


def test_first_call_asks_all_missing_fields_at_once_when_a_candidate_needs_both():
    """한 후보가 여러 필드를 동시에 요구하면 한 번에 모두 물어봐야 한다 —
    필드 하나씩 따로 왕복하지 않는지 확인(일반화된 사전 체크의 핵심)."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=MultiFieldGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        payload = {**PAYLOAD, "isSmeEmployee": None}
        response = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert set(response.missing_fields) == {"financial_income_taxed", "is_sme_employee"}
    assert "금융소득종합과세" in response.chat_reply
    assert "중소기업" in response.chat_reply


def test_second_call_answering_only_one_of_two_fields_still_asks_for_the_remaining_one():
    """두 필드 중 하나만 답하면, 그 필드는 더 이상 안 걸리고 나머지 하나만
    걸려야 한다 — 답변이 실제로 반영되는지 확인."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=MultiFieldGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        payload = {**PAYLOAD, "isSmeEmployee": None, "financialIncomeTaxed": False}
        response = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.missing_fields == ["is_sme_employee"]


def test_third_call_answering_both_fields_builds_real_roadmap():
    """두 필드가 모두 채워지면 사전 체크를 완전히 건너뛰고 실제 로드맵이
    나와야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=MultiFieldGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        payload = {**PAYLOAD, "financialIncomeTaxed": False}
        response = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert response.conversation_status != "needs_input"
    assert response.recommended is not None


_GATE_REGISTRY = DynamicGateRegistry(
    {
        "P5": [
            DynamicGate(
                policy_id="P5",
                gate_id="artist_certification",
                question="예술활동증명을 받으셨나요?",
                hint="문체부가 인정하는 예술인 신분 증빙입니다.",
            )
        ]
    }
)


def test_first_call_asks_dynamic_gate_question_before_roadmap():
    """DynamicGateRegistry가 발견한 게이트도 레거시 4개 필드와 똑같이, 로드맵 없이
    먼저 구조화된 질문(missingFieldDetails)으로 되물어야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=DynamicGateGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
        gate_registry=_GATE_REGISTRY,
    )
    try:
        response = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.conversation_status == "needs_input"
    assert response.missing_fields == ["P5:artist_certification"]
    assert len(response.missing_field_details) == 1
    detail = response.missing_field_details[0]
    assert detail.field == "P5:artist_certification"
    assert detail.question == "예술활동증명을 받으셨나요?"
    assert detail.hint == "문체부가 인정하는 예술인 신분 증빙입니다."
    assert detail.input_type == "boolean"
    assert response.chat_reply == "예술활동증명을 받으셨나요?"


def test_second_call_answering_dynamic_gate_false_builds_roadmap_with_ineligible_policy():
    """"아니오" 답변은 프론트 구조화 폼(dynamicGateAnswers)을 통해서만 들어온다 —
    LLM이 아니라 이 값을 보고 eligible이 계산돼야 한다(repositories.py가 담당)."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=DynamicGateGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
        gate_registry=_GATE_REGISTRY,
    )
    try:
        payload = {**PAYLOAD, "dynamicGateAnswers": {"P5:artist_certification": False}}
        response = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
    finally:
        service.get_runtime = original_runtime

    # 게이트가 답변됐으니 사전 체크는 더 이상 걸리지 않고 실제 로드맵이 나와야 한다
    # (이 상품 자체는 ineligible이지만, 그건 로드맵 계산 내부에서 걸러질 문제다).
    assert response.conversation_status != "needs_input"
    assert response.recommended is not None


def test_dynamic_gate_answer_submission_does_not_fall_back_to_generic_clarification():
    """ProfileAskForm의 동적 게이트 답변은 프론트가 고정 문구로 보내고, 그 안에
    담긴 실제 답변 문구(게이트 question/hint 그대로)는 apply_policy_answers의
    정규식(레거시 4개 필드 전용) 어디에도 안 걸린다 — policy_changes가 항상
    빈 채로 UNCLEAR로 떨어진다. answeringMissingFields 신호가 없으면 이미
    반영된 답변이 있어도 일반 안내문("조건을 변경하려는 것인지...")만 나오고
    conversationStatus도 needs_input이 되어, 라우터가 방금 재계산된 로드맵
    카드를 숨겨버리는 회귀가 있었다(실사용자 피드백으로 발견)."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=DynamicGateGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
        gate_registry=_GATE_REGISTRY,
    )
    try:
        payload = {
            **PAYLOAD,
            "dynamicGateAnswers": {"P5:artist_certification": True},
        }
        response = roadmap(RoadmapCreateRequest(
            **payload,
            question="추가 정보를 반영해서 자산관리 로드맵을 다시 만들어줘. 제공된 정보: 예술활동증명을 받으셨나요?=true",
            answeringMissingFields=True,
            threadId=uuid4(),
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.conversation_status == "completed"
    assert response.conversation_intent == "policy_eligibility"
    assert response.recommended is not None
    assert response.chat_reply != "조건을 변경하려는 것인지, 추천 이유나 금융 제도를 묻는 것인지 알려주세요."


# ── 사전 체크 게이트가 turn의 의도와 무관하게 무조건 걸리던 버그의 회귀 테스트.
# 미확인 필드(financial_income_taxed)가 남아있는 상태에서도, 그 필드와 무관한
# 의도(금융 Q&A/추천 이유 설명/불명확 요청)는 정상 응답해야 하고, 반대로 실제
# 정책 자격 질문이나 게이트 답변 제출은 여전히 게이트가 걸려야 한다.
def test_financial_qa_question_answers_even_when_prelaunch_field_missing():
    """로드맵이 미완성(사전 체크 필드 미확인)이어도 순수 금융 지식 질문은
    필드와 무관하므로 정상적으로(RAG 미검색 응답이라도) 답해야지, 미확인
    필드 질문만 앵무새처럼 돌아오면 안 된다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="ISA 세액공제 한도가 얼마야?", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.chat_reply != "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
    assert response.conversation_intent == "financial_qa"


def test_result_explanation_question_answers_even_when_prelaunch_field_missing():
    """"왜 추천?" 같은 결과 설명 요청도 미확인 필드 게이트에 막히지 않고 실제
    설명(잠정 로드맵 기준)으로 답해야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="왜 이 상품을 추천했어?", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.chat_reply != "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
    assert response.chat_reply.startswith("현재 최우선안은")


def test_unclear_question_skips_gate_when_prelaunch_field_missing():
    """사용자가 그냥 애매한 말을 한 turn은 answeringMissingFields 신호가 없으므로
    게이트를 건너뛰고 기존의 일반 UNCLEAR 안내문을 받아야 한다 — 미확인 필드
    질문만 반복되던 버그의 핵심 재현 케이스."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="더 좋은 걸로 해줘", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert "조건을 변경하려는 것인지" in response.chat_reply
    assert response.chat_reply != "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"


def test_policy_eligibility_question_still_gates_when_prelaunch_field_missing():
    """정책 자격을 실제로 묻는 turn은 그 필드가 정말 필요하므로 기존처럼
    게이트가 걸려야 한다 — 화이트리스트가 이 케이스까지 풀어버리면 안 된다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="이 정책 자격이 되는지 알려줘", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.conversation_status == "needs_input"
    assert response.chat_reply == "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"


def test_answering_missing_fields_flag_keeps_gate_even_for_unclear_looking_text():
    """profile_ask 답변 제출은 프론트가 고정 문구("추가 정보를 반영해서...")로
    보내 텍스트만 보면 UNCLEAR와 구분이 안 된다 — answeringMissingFields 신호로
    여전히 게이트가 걸려, 아직 남은 다른 미확인 필드를 건너뛰지 않아야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD,
            question="추가 정보를 반영해서 자산관리 로드맵을 다시 만들어줘. 제공된 정보: 가구원 수=1",
            answeringMissingFields=True,
            threadId=uuid4(),
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.conversation_status == "needs_input"
    assert response.chat_reply == "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"


def test_free_text_field_answer_is_reflected_in_request_patch_and_stops_repeating():
    """자유텍스트로 명확히 답한 자격조건 필드(예: "중소기업 재직 안 해요")는 그
    턴의 의도가 UNCLEAR로 떨어지더라도 반영되어야 하고, 그 반영 결과가
    request_patch로 프론트/라우터에 실려가야 다음 턴에 같은 질문이 반복되지
    않는다. 게이트가 완전히 안 풀린 나머지 필드(financial_income_taxed)는
    여전히 안내돼야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=MultiFieldGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        payload = {**PAYLOAD, "isSmeEmployee": None}
        first = roadmap(RoadmapCreateRequest(**payload, threadId=uuid4()))
        assert set(first.missing_fields) == {"financial_income_taxed", "is_sme_employee"}

        second = roadmap(RoadmapCreateRequest(
            **payload, question="중소기업 재직 안 해요", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert second.conversation_status == "completed"
    assert second.request_patch.is_sme_employee is False
    # 채팅 답변은 짧은 완료 문구만 담고(실사용자 피드백: 조건을 문장에 다
    # 이어붙이면 가독성이 나쁘다), 조건별 세부 내용은 카드로 구조화해서 나간다.
    assert "완료" in second.chat_reply
    assert "financial_income_taxed" not in second.chat_reply
    assert len(second.policy_eligibility_cards) == 1
    card = second.policy_eligibility_cards[0]
    assert any("확인 필요 항목" in c for c in card.conditions)
    assert not any("financial_income_taxed" in c for c in card.conditions)
    # 규칙 분류기는 이 turn을 UNCLEAR로 떨어뜨리지만, 실제로 만든 응답은
    # 정책 자격 요약이다 — conversationIntent를 UNCLEAR 그대로 보고하면
    # 라우터의 "financial_qa/unclear는 로드맵 카드 억제" 로직이 방금 막
    # 반영된 로드맵 카드까지 같이 숨겨버린다(실사용자 피드백으로 발견한 회귀).
    assert second.conversation_intent == "policy_eligibility"


def test_household_income_answered_by_chat_persists_across_separate_http_requests():
    """Agentic AI화 페이지에 남아있던 이슈 재현: 채팅으로 답한 가구소득이
    같은 threadId 안에서만 유지되고 별개 HTTP 요청(클라이언트가 다음 턴에
    request_patch를 그대로 재전송)에서는 사라지던 문제. RoadmapRequestPatch에
    household_monthly_income이 있어야, 이번 턴에 채팅으로 답한 값을 클라이언트가
    프로필에 반영해 다음 요청에 다시 실어보낼 수 있다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=HouseholdIncomeGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        first = roadmap(RoadmapCreateRequest(**PAYLOAD, threadId=uuid4()))
        assert first.missing_fields == ["household_monthly_income"]

        second = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="가구 월소득은 350만원이야", threadId=uuid4()
        ))
    finally:
        service.get_runtime = original_runtime

    assert second.request_patch.household_monthly_income == 3_500_000


def test_router_initial_generation_message_still_gates_on_missing_fields():
    """실서비스 회귀 재현: 프론트/라우터는 최초 생성 요청도 question을 비워
    보내지 않는다 — "입력한 조건으로 자산관리 로드맵을 만들어줘." 같은 고정
    문구를 보내는데, 이 문구엔 정책 키워드가 전혀 없어 classify_intent가
    UNCLEAR로 분류한다. isInitialRoadmapRequest 신호가 없으면 이 turn이
    미확인 자격조건이 남아있는데도 게이트를 건너뛰어버리는 회귀가 있었다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=FinancialIncomeTaxedGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD,
            question="입력한 조건으로 자산관리 로드맵을 만들어줘.",
            isInitialRoadmapRequest=True,
            threadId=uuid4(),
        ))
    finally:
        service.get_runtime = original_runtime

    assert response.recommended is None
    assert response.conversation_status == "needs_input"
    assert response.chat_reply == "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"


def test_router_followup_after_initial_request_is_not_forced_to_gate():
    """isInitialRoadmapRequest는 진짜 최초 생성 turn에만 실려온다 — 이후
    턴(라우터가 is_first_call=False로 넘김)까지 계속 게이트를 강제하면 안
    된다(그러면 사실상 예전의 '매 turn 무조건 게이트' 버그로 되돌아간다)."""
    response = roadmap(RoadmapCreateRequest(
        **PAYLOAD, question="왜 이 상품을 추천했어?", threadId=uuid4(),
    ))
    assert response.chat_reply.startswith("현재 최우선안은")


def test_eligible_policy_reply_never_leaks_raw_dynamic_gate_keys():
    """실서비스 회귀 재현: 자유텍스트 답변이 UNCLEAR로 떨어져 정책 자격 요약
    분기(execute_conversation 맨 아래)에 도달했을 때, missing_qualification_
    fields의 원본 키("P5:artist_certification" 같은 합성 키)가 사용자 응답에
    그대로 노출되던 문제. 개수만 안내해야 한다."""
    original_runtime = service.get_runtime
    service.get_runtime = lambda: Runtime(
        policy_repository=DynamicGateGapPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
        gate_registry=_GATE_REGISTRY,
    )
    try:
        response = roadmap(RoadmapCreateRequest(
            **PAYLOAD, question="중소기업 재직 안 해요", threadId=uuid4(),
        ))
    finally:
        service.get_runtime = original_runtime

    assert "P5:artist_certification" not in response.chat_reply
    assert "20260" not in response.chat_reply  # 정책 ID 접두사(연월일시분초) 미노출
    assert len(response.policy_eligibility_cards) == 1
    card = response.policy_eligibility_cards[0]
    assert "P5:artist_certification" not in " ".join(card.conditions)
    assert "확인 필요 항목 1건" in card.conditions
