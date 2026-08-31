from dataclasses import replace

from roadmap_agent.conversation import (
    ConversationIntent,
    ConversationStatus,
    classify_intent,
    apply_policy_answers,
    execute_conversation,
    plan_conversation,
    policy_qualification_gaps,
    ConversationPlan,
)
from roadmap_agent.domain import Evidence, RiskProfile, RoadmapRequest, RoadmapResult, Scenario
from roadmap_agent.ports import PolicyBenefit, SavingsProduct


class EmptySavings:
    def find_candidates(self, request):
        return []


class Policies:
    def find_candidates(self, request):
        return [PolicyBenefit("p1", "청년정책", True, 500_000, 100_000, 36, "url", "2026", "충족")]


class AlternativePolicies:
    def find_candidates(self, request):
        return [
            PolicyBenefit("p1", "청년정책", True, 500_000, 100_000, 36, "url", "2026", "충족"),
            PolicyBenefit("p2", "청년도약계좌", True, 700_000, 200_000, 60, "url2", "2026", "충족"),
        ]


class AlternativeSavings:
    def find_candidates(self, request):
        return [SavingsProduct(
            "s2", "테스트은행", "새희망적금", 0.03, 0.04, 600_000,
            36, 0.154, "url", "2026",
        )]


class Retriever:
    def search(self, query, limit=3):
        return [Evidence("국세청 연금계좌 안내", "https://example.test", "pension.md", 10)]


def base_request(**changes):
    return replace(
        RoadmapRequest(800_000, 36, 30_000_000, RiskProfile.BALANCED),
        **changes,
    )


def base_result():
    recommended = Scenario(
        "policy", "청년정책", {"policy": 500_000, "cash": 300_000},
        28_000_000, 29_000_000, 30_000_000, 100, 28_800_000,
        ["정부 기여금을 받을 수 있습니다."], [], monthly_limit=500_000,
    )
    alternative = Scenario(
        "savings", "[적금] 우리은행 우리SUPER주거래적금", {"savings": 500_000, "cash": 300_000},
        28_000_000, 29_000_000, 30_000_000, 90, 28_800_000,
        ["우대금리 조건을 반영했습니다."], [], monthly_limit=500_000,
    )
    return RoadmapResult(recommended, [alternative], {}, "참고용")


def test_classifies_supported_intents():
    assert classify_intent("월 투입액을 60만 원으로 바꿔줘") == ConversationIntent.CONDITION_CHANGE
    assert classify_intent("왜 이 상품을 추천했어?") == ConversationIntent.RESULT_EXPLANATION
    assert classify_intent("나는 왜 우대형이 아니야?") == ConversationIntent.POLICY_ELIGIBILITY
    assert classify_intent("연금저축 세액공제가 뭐야?") == ConversationIntent.FINANCIAL_QA
    assert classify_intent("ISA는 왜 비과세야?") == ConversationIntent.FINANCIAL_QA
    assert classify_intent("왜 이 적금을 추천했어?") == ConversationIntent.RESULT_EXPLANATION
    assert classify_intent("적금 시나리오에서도 50만원이 최대 납입금액이야?") == ConversationIntent.RESULT_EXPLANATION
    assert classify_intent("미래적금 말고 다른 정책 상품 있어?") == ConversationIntent.PRODUCT_ALTERNATIVES
    assert classify_intent("다른 금융 상품이 있는지 물어보는 거야") == ConversationIntent.PRODUCT_ALTERNATIVES
    assert classify_intent(
        "그중에서 내 조건에 제일 맞는거대로 정렬해줘"
    ) == ConversationIntent.PRODUCT_RANKING
    assert classify_intent(
        "조건은 그대로인데, 이 외의 적금에 대한 순위를 알고싶어"
    ) == ConversationIntent.PRODUCT_RANKING


def test_product_and_bank_names_are_current_result_questions():
    result = base_result()
    assert classify_intent(
        "우리은행 납입한도는 얼마야?", result
    ) == ConversationIntent.RESULT_EXPLANATION
    assert classify_intent(
        "우리SUPER주거래적금 납입한도는 얼마야?", result
    ) == ConversationIntent.RESULT_EXPLANATION


def test_policy_question_requests_one_missing_value_at_a_time():
    plan = plan_conversation(base_request(), "나는 왜 우대형이 아니야?")
    assert plan.clarification_question == "가구 전체의 월소득은 얼마인가요?"


def test_unclear_question_returns_one_clarification_without_tools():
    response = execute_conversation(
        base_request(), base_result(), "더 좋은 걸로 해줘",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    assert response.status == ConversationStatus.NEEDS_INPUT
    assert response.executed_tools == ()
    assert response.state_history == ("draft", "needs_input")


def test_policy_answers_are_structured_without_guessing():
    updated, changes = apply_policy_answers(
        base_request(),
        "가구 월소득은 350만 원이고 금융소득종합과세 이력은 없어",
    )
    assert updated.household_monthly_income == 3_500_000
    assert updated.financial_income_taxed is False
    assert len(changes) == 2


def test_bare_number_answers_pending_household_income_question():
    context = "나는 왜 우대형이 아니야? 가구 전체의 월소득은 얼마인가요?"
    updated, changes = apply_policy_answers(base_request(), "300", context=context)
    assert updated.household_monthly_income == 3_000_000
    assert changes == ["가구 월소득 3,000,000원"]


def test_bare_number_ignored_when_no_pending_household_question():
    updated, changes = apply_policy_answers(base_request(), "300")
    assert updated.household_monthly_income is None
    assert changes == []


def test_bare_yes_answers_pending_financial_income_question():
    context = "나는 왜 우대형이 아니야? 최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
    updated, changes = apply_policy_answers(base_request(), "응 있어", context=context)
    assert updated.financial_income_taxed is True
    assert changes == ["금융소득종합과세 이력 있음"]


def test_bare_no_answers_pending_financial_income_question():
    context = "나는 왜 우대형이 아니야? 최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
    updated, changes = apply_policy_answers(base_request(), "아니요", context=context)
    assert updated.financial_income_taxed is False
    assert changes == ["금융소득종합과세 이력 없음"]


def test_bare_yes_answers_pending_sme_employee_question():
    context = "나는 왜 우대형이 아니야? 현재 중소기업에 재직 중인가요?"
    updated, changes = apply_policy_answers(base_request(), "네", context=context)
    assert updated.is_sme_employee is True
    assert changes == ["중소기업 재직"]


def test_bare_answer_ignored_when_no_pending_boolean_question():
    updated, changes = apply_policy_answers(base_request(), "응 있어")
    assert updated.financial_income_taxed is None
    assert changes == []


def test_policy_followup_answer_routes_back_to_policy_eligibility():
    request = base_request(is_sme_employee=False)
    context = "나는 왜 우대형이 아니야? 가구 전체의 월소득은 얼마인가요?"
    response = execute_conversation(
        request, base_result(), "300",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        context=context,
    )
    assert response.request.household_monthly_income == 3_000_000
    assert response.intent == ConversationIntent.POLICY_ELIGIBILITY


class ConditionalPolicies:
    """financial_income_taxed=True면 청년미래적금이 자격 후보에서 아예 빠진다
    (실제 repositories.py의 financial_income_taxed=True → eligible=False 동작을
    후보 목록 단위로 흉내). estimated_support를 높게 둬 존재할 때는 항상 추천에
    선정되도록 한다."""

    def find_candidates(self, request):
        candidates = [
            PolicyBenefit("p2", "일반정책", True, 500_000, 50_000, 36, "url2", "2026", "항상 가능"),
        ]
        if request.financial_income_taxed is not True:
            candidates.append(
                PolicyBenefit(
                    "p1", "청년미래적금", True, 500_000, 300_000, 36, "url", "2026",
                    "금융소득 조건부",
                )
            )
        return candidates


def test_policy_eligibility_answer_actually_recomputes_roadmap_not_just_text():
    """정책 자격 질문에 답하면 채팅 텍스트만 새로 만드는 게 아니라 실제로
    run_roadmap을 다시 호출해 로드맵(추천 상품)까지 바뀌어야 한다 — 텍스트로만
    설명하고 result를 그대로 통과시키던 기존 버그의 회귀 테스트."""
    from roadmap_agent.orchestrator import run_roadmap

    def titles(result):
        return {result.recommended.title, *(item.title for item in result.alternatives)}

    # 1인가구 + 소득 지정 → 가구소득이 자동 계산돼(household income gap 없음)
    # financial_income_taxed만 유일한 미확정 필드로 남는다.
    request = base_request(
        is_sme_employee=False, household_size=1,
        previous_annual_income=40_000_000, current_annual_income=40_000_000,
    )
    initial_result = run_roadmap(
        request,
        policy_repository=ConditionalPolicies(),
        savings_repository=EmptySavings(),
        retriever=Retriever(),
    )
    assert any("청년미래적금" in title for title in titles(initial_result))

    context = (
        "나는 왜 우대형이 아니야? "
        "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
    )
    response = execute_conversation(
        request, initial_result, "응 있어",
        run_roadmap_fn=run_roadmap,
        policy_repository=ConditionalPolicies(),
        savings_repository=EmptySavings(),
        retriever=Retriever(),
        context=context,
    )

    assert response.request.financial_income_taxed is True
    # 재계산된 결과에서는 청년미래적금이 자격에서 빠져 후보 어디에도 없어야 한다
    # (재계산이 실제로 일어났다는 증거 — 텍스트만 새로 만들었다면 이전 result가
    # 그대로 통과되어 청년미래적금이 여전히 후보에 남아있게 된다).
    assert not any("청년미래적금" in title for title in titles(response.result))


def test_dont_know_reply_explains_impact_instead_of_generic_fallback():
    context = "나는 왜 우대형이 아니야? 가구 전체의 월소득은 얼마인가요?"
    plan = plan_conversation(base_request(), "잘 모르겠어", context=context)
    assert plan.intent == ConversationIntent.POLICY_ELIGIBILITY
    assert "정확히 판정할 수 없" in plan.clarification_question


def test_single_person_household_income_gap_is_not_asked():
    request = base_request(
        household_size=1, previous_annual_income=35_000_000, is_sme_employee=False,
    )
    gaps = policy_qualification_gaps(request, "나는 왜 우대형이 아니야?")
    assert "가구 전체의 월소득은 얼마인가요?" not in gaps


def test_multi_person_household_income_gap_is_still_asked():
    request = base_request(
        household_size=2, previous_annual_income=35_000_000, is_sme_employee=False,
    )
    gaps = policy_qualification_gaps(request, "나는 왜 우대형이 아니야?")
    assert "가구 전체의 월소득은 얼마인가요?" in gaps


def test_policy_field_phrased_as_condition_change_is_not_dropped():
    calls = []

    def runner(request, **kwargs):
        calls.append(request)
        return base_result()

    response = execute_conversation(
        base_request(is_sme_employee=True), base_result(),
        "중소기업 재직 안 함으로 조건 변경해줘",
        run_roadmap_fn=runner,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    assert response.status == ConversationStatus.COMPLETED
    assert response.request.is_sme_employee is False
    assert response.changes == ("중소기업 재직 아님",)
    assert len(calls) == 1


def test_condition_change_recalculates_with_selected_tools_only():
    calls = []

    def runner(request, **kwargs):
        calls.append(request)
        return base_result()

    response = execute_conversation(
        base_request(), base_result(), "월 투입액을 60만 원으로 바꿔줘",
        run_roadmap_fn=runner,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    assert response.status == ConversationStatus.COMPLETED
    assert response.request.monthly_budget == 600_000
    assert response.executed_tools == ("condition_parser", "roadmap_calculators", "ranking")
    assert len(calls) == 1


def test_condition_parser_supports_target_cap_and_emergency_fund():
    response = execute_conversation(
        base_request(),
        base_result(),
        "목표금액은 4천만 원, 투자 비중은 10%로 하고 비상금은 없다고 해줘",
        run_roadmap_fn=lambda request, **kwargs: base_result(),
        policy_repository=Policies(),
        savings_repository=EmptySavings(),
        retriever=Retriever(),
    )
    assert response.request.target_amount == 40_000_000
    assert response.request.max_investment_ratio == 0.1
    assert response.request.has_emergency_fund is False


def test_relative_risk_change_used_by_nextjs_suggestion():
    response = execute_conversation(
        base_request(max_investment_ratio=0.3),
        base_result(),
        "위험을 더 줄여줘",
        run_roadmap_fn=lambda request, **kwargs: base_result(),
        policy_repository=Policies(),
        savings_repository=EmptySavings(),
        retriever=Retriever(),
    )
    assert response.request.max_investment_ratio == 0.2
    assert response.changes == ("투자비중 상한 20%",)


def test_result_explanation_does_not_recalculate():
    def should_not_run(*args, **kwargs):
        raise AssertionError("결과 설명에서 재계산하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "왜 추천했어?", run_roadmap_fn=should_not_run,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    assert response.executed_tools == ("result_explainer",)
    assert "청년정책" in response.reply


def test_savings_scenario_limit_uses_current_alternative_without_rag():
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise AssertionError("현재 추천 결과 질문에서 RAG를 호출하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "적금 시나리오에서도 50만원이 최대 납입금액이야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=BrokenRetriever(),
    )

    assert response.executed_tools == ("result_explainer",)
    assert "우리은행 우리SUPER주거래적금" in response.reply
    assert "500,000원" in response.reply


def test_alternative_product_name_selects_it_without_saying_alternative():
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise AssertionError("상품명 질문에서 RAG를 호출하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "우리SUPER주거래적금은 납입한도가 없어?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=BrokenRetriever(),
    )

    assert response.executed_tools == ("result_explainer",)
    assert "우리은행 우리SUPER주거래적금" in response.reply
    assert "500,000원" in response.reply


def test_recommended_name_and_labels_select_the_correct_scenario():
    by_name = execute_conversation(
        base_request(), base_result(), "청년정책 납입한도는 얼마야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    by_label = execute_conversation(
        base_request(), base_result(), "최우선 상품 납입한도는 얼마야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )

    assert "청년정책" in by_name.reply
    assert "청년정책" in by_label.reply


def test_structured_limit_answer_takes_priority_over_llm_explainer():
    class WrongExplainer:
        def explain(self, request, result):
            raise AssertionError("정형 한도 질문을 LLM에 보내면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "대안으로 나온 우리은행 최대 납입금액은 얼마야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        explainer=WrongExplainer(),
    )

    assert "우리은행 우리SUPER주거래적금" in response.reply
    assert "500,000원" in response.reply


def test_short_why_question_keeps_previous_savings_limit_context():
    response = execute_conversation(
        base_request(), base_result(), "왜?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        context="적금 시나리오에서도 50만원이 최대 납입금액이야?",
    )

    assert response.executed_tools == ("result_explainer",)
    assert "우리은행 우리SUPER주거래적금" in response.reply
    assert "공식 상품 데이터" in response.reply
    assert "500,000원" in response.reply


def test_short_why_explains_missing_alternative_limit_from_previous_turn():
    result = base_result()
    unknown_limit = replace(result.alternatives[0], monthly_limit=None)
    result = replace(result, alternatives=[unknown_limit])

    response = execute_conversation(
        base_request(), result, "왜?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        context="대안으로 나온거는 납입 한도 없어?",
    )

    assert "우리은행 우리SUPER주거래적금" in response.reply
    assert "최대 월 납입액이 제공되지 않았기 때문" in response.reply
    assert "한도가 없다는 뜻은 아니며" in response.reply
    assert "청년정책" not in response.reply


def test_financial_question_runs_rag_only():
    response = execute_conversation(
        base_request(), base_result(), "연금저축 세액공제가 뭐야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
    )
    assert response.executed_tools == ("rag_search",)
    assert response.evidence[0].title == "국세청 연금계좌 안내"


def test_other_policy_product_question_queries_candidates_without_rag_or_income_prompt():
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise AssertionError("대안 상품 탐색에서 RAG를 호출하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "미래적금 말고 다른 정책 상품 있어?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=AlternativePolicies(),
        savings_repository=AlternativeSavings(),
        retriever=BrokenRetriever(),
    )

    assert response.intent == ConversationIntent.PRODUCT_ALTERNATIVES
    assert response.executed_tools == (
        "policy_repository", "savings_repository", "candidate_filter"
    )
    assert "청년도약계좌" in response.reply
    assert "가구 전체의 월소득" not in response.reply


def test_other_financial_product_question_lists_policy_and_savings_candidates():
    response = execute_conversation(
        base_request(), base_result(), "다른 금융 상품이 있는지 물어보는 거야",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=AlternativePolicies(),
        savings_repository=AlternativeSavings(),
        retriever=Retriever(),
    )

    assert "청년도약계좌" in response.reply
    assert "테스트은행 새희망적금" in response.reply
    assert response.evidence == ()


def test_followup_ranks_previous_policy_candidates_without_rag():
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise AssertionError("상품 순위 계산에서 RAG를 호출하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(), "그중에서 내 조건에 제일 맞는거대로 정렬해줘",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=AlternativePolicies(),
        savings_repository=AlternativeSavings(),
        retriever=BrokenRetriever(),
        context="현재 DB에서 추가로 비교 가능한 정책상품은 청년도약계좌입니다.",
    )

    assert response.intent == ConversationIntent.PRODUCT_RANKING
    assert response.executed_tools == (
        "policy_repository", "savings_repository", "candidate_scoring", "ranking"
    )
    assert "1위 청년도약계좌" in response.reply
    assert "테스트은행" not in response.reply
    assert response.evidence == ()


def test_followup_ranks_other_savings_without_rag_or_legal_answer():
    class RankedSavings:
        def find_candidates(self, request):
            return [
                SavingsProduct(
                    "s2", "테스트은행", "새희망적금", 0.03, 0.04, 600_000,
                    36, 0.154, "url", "2026",
                ),
                SavingsProduct(
                    "s3", "예시은행", "단기적금", 0.025, 0.035, 300_000,
                    12, 0.154, "url2", "2026",
                ),
            ]

    class BrokenRetriever:
        def search(self, query, limit=3):
            raise AssertionError("상품 순위 계산에서 RAG를 호출하면 안 됩니다")

    response = execute_conversation(
        base_request(), base_result(),
        "조건은 그대로인데, 이 외의 적금에 대한 순위를 알고싶어",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=AlternativePolicies(), savings_repository=RankedSavings(),
        retriever=BrokenRetriever(),
    )

    assert response.intent == ConversationIntent.PRODUCT_RANKING
    assert "1위 테스트은행 새희망적금" in response.reply
    assert "2위 예시은행 단기적금" in response.reply
    assert "청년도약계좌" not in response.reply
    assert "법령" not in response.reply
    assert "정부기여금" not in response.reply


def test_financial_question_answers_from_retrieved_content():
    class Answerer:
        def answer_financial_question(self, question, evidence):
            assert "대기업으로 이직" in question
            assert evidence[0].title == "국세청 연금계좌 안내"
            return "가입 후 이직에 따른 유지 여부는 해당 상품의 최신 약관 확인이 필요합니다."

    response = execute_conversation(
        base_request(), base_result(), "중소기업 다니다가 미래적금 만료 전에 대기업으로 이직하면 어떻게 돼?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        explainer=Answerer(),
    )

    assert "최신 약관 확인이 필요" in response.reply
    assert "문서를 확인했습니다" not in response.reply


def test_external_tool_failures_have_safe_local_fallbacks():
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise RuntimeError("secret upstream error")

    response = execute_conversation(
        base_request(), base_result(), "연금저축 세액공제가 뭐야?",
        run_roadmap_fn=lambda *args, **kwargs: None,
        policy_repository=Policies(),
        savings_repository=EmptySavings(),
        retriever=BrokenRetriever(),
    )
    assert response.status == ConversationStatus.COMPLETED
    assert "공식 근거 문서에서 답을 확인하지 못했습니다" in response.reply
    assert "secret" not in response.reply


def test_llm_planner_handles_ambiguous_condition_change_with_whitelisted_tools():
    class Planner:
        def plan(self, request, message):
            return ConversationPlan(
                ConversationIntent.CONDITION_CHANGE,
                ("condition_parser", "roadmap_calculators", "ranking"),
                structured_changes={"max_investment_ratio": 0.1},
            )

    response = execute_conversation(
        base_request(max_investment_ratio=0.3), base_result(), "당분간 더 안전하게 가고 싶어",
        run_roadmap_fn=lambda request, **kwargs: base_result(),
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        planner=Planner(),
    )
    assert response.intent == ConversationIntent.CONDITION_CHANGE
    assert response.request.max_investment_ratio == 0.1
    assert response.executed_tools == ("condition_parser", "roadmap_calculators", "ranking")


def test_llm_planner_cannot_select_unregistered_tool():
    class Planner:
        def plan(self, request, message):
            return ConversationPlan(
                ConversationIntent.FINANCIAL_QA,
                ("shell",),
            )

    plan = plan_conversation(base_request(), "내 상황 좀 알아서 분석해줘", Planner())
    assert plan.intent == ConversationIntent.UNCLEAR
    assert plan.tools == ()
    assert plan.clarification_question is not None


def test_llm_relative_change_is_applied_to_current_verified_value():
    class Planner:
        def plan(self, request, message):
            return ConversationPlan(
                ConversationIntent.CONDITION_CHANGE,
                ("condition_parser", "roadmap_calculators", "ranking"),
                structured_changes={"max_investment_ratio_delta": -0.1},
            )

    response = execute_conversation(
        base_request(max_investment_ratio=0.4), base_result(), "당분간 안전하게 가고 싶어",
        run_roadmap_fn=lambda request, **kwargs: base_result(),
        policy_repository=Policies(), savings_repository=EmptySavings(), retriever=Retriever(),
        planner=Planner(),
    )
    assert response.request.max_investment_ratio == 0.3
