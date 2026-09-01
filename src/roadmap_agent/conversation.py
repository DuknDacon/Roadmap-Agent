from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
import time
from typing import Protocol

from .domain import Evidence, RoadmapRequest, RoadmapResult
from .policy_qualification import effective_household_monthly_income
from .ports import (
    PolicyRepository,
    RagRetriever,
    RoadmapExplainer,
    SavingsProductRepository,
)
from .ui_state import apply_conversation_change


class ConversationIntent(StrEnum):
    CONDITION_CHANGE = "condition_change"
    RESULT_EXPLANATION = "result_explanation"
    PRODUCT_ALTERNATIVES = "product_alternatives"
    PRODUCT_RANKING = "product_ranking"
    POLICY_ELIGIBILITY = "policy_eligibility"
    FINANCIAL_QA = "financial_qa"
    INPUT_COMPLETION = "input_completion"
    UNCLEAR = "unclear"


class ConversationStatus(StrEnum):
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ConversationPlan:
    intent: ConversationIntent
    tools: tuple[str, ...]
    clarification_question: str | None = None
    structured_changes: dict[str, object] | None = None
    planned_by: str = "rules"


@dataclass(frozen=True)
class ConversationResponse:
    status: ConversationStatus
    intent: ConversationIntent
    request: RoadmapRequest
    result: RoadmapResult
    reply: str
    executed_tools: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    state_history: tuple[str, ...] = ("draft", "processing", "completed")


_CHANGE_TERMS = re.compile(
    r"(바꿔|변경|다시\s*계산|늘려|줄여|낮춰|높여|월\s*(?:저축액|투입액|예산)|"
    r"목표\s*(?:금액|액|시점)|비상\s*(?:금|자금)|매달)"
)
_INVESTMENT_CHANGE = re.compile(
    r"투자\s*(?:비중|비율|상한).{0,12}(?:[\d.]+\s*%|바꿔|변경|늘려|줄여|낮춰|높여)"
)
_RESULT_TERMS = re.compile(r"(왜|추천\s*이유|대안|결과|근거|부족액|달성률)")
_POLICY_TERMS = re.compile(r"(정책|지원금|정부\s*기여금|우대형|일반형|자격|모집)")
_FINANCE_TERMS = re.compile(r"(ISA|연금|세액공제|비과세|적금|투자|채권|주식|금리|세금)", re.I)
_MISSING_TERMS = re.compile(r"(무엇을|뭘|어떤\s*정보|입력.*(?:필요|부족)|누락)")
_CURRENT_RESULT_TERMS = re.compile(
    r"(현재|최우선|추천|대안|시나리오|이\s*상품|그\s*상품|나온\s*상품|카드)"
)
_LIMIT_QUESTION = re.compile(
    r"(최대|한도).{0,10}(?:납입|금액)|(?:납입|금액).{0,10}(?:최대|한도)"
)
_ALTERNATIVE_SEARCH = re.compile(
    r"(?:말고|다른|그\s*외|또\s*다른).{0,18}(?:정책|금융|적금|예금|상품)|"
    r"(?:정책|금융|적금|예금|상품).{0,18}(?:말고|다른|더\s*있|또\s*있)"
)
_PRODUCT_RANKING = re.compile(
    r"(?:그중|이\s*외|다른|후보|상품|적금|정책).{0,35}(?:순위|순서|정렬|랭킹|제일\s*맞)|"
    r"(?:순위|순서|정렬|랭킹).{0,20}(?:상품|적금|정책|조건)"
)
_DONT_KNOW = re.compile(r"몰라|모름|모르겠|모르는데|알\s*수\s*없")
# 정책 자격 예/아니오 질문에 대한 짧은 구어체 단답(예: "응 있어", "아니요") 인식용.
_YES_ANSWER = re.compile(r"^(네|응|예|어|맞아|맞습니다|맞아요|그래|그렇습니다)\b")
_NO_ANSWER = re.compile(r"^(아니요|아니오|아뇨|아니야|아닙니다|아니)\b")

INTENT_TOOLS = {
    ConversationIntent.CONDITION_CHANGE: ("condition_parser", "roadmap_calculators", "ranking"),
    ConversationIntent.RESULT_EXPLANATION: ("result_explainer",),
    ConversationIntent.PRODUCT_ALTERNATIVES: (
        "policy_repository", "savings_repository", "candidate_filter"
    ),
    ConversationIntent.PRODUCT_RANKING: (
        "policy_repository", "savings_repository", "candidate_scoring", "ranking"
    ),
    ConversationIntent.POLICY_ELIGIBILITY: ("policy_repository", "policy_qualification"),
    ConversationIntent.FINANCIAL_QA: ("rag_search",),
    ConversationIntent.INPUT_COMPLETION: ("input_validator",),
    ConversationIntent.UNCLEAR: (),
}
ALLOWED_CHANGE_FIELDS = {
    "monthly_budget",
    "horizon_months",
    "target_amount",
    "max_investment_ratio",
    "has_emergency_fund",
    "max_investment_ratio_delta",
}


class ConversationPlanner(Protocol):
    def plan(self, request: RoadmapRequest, message: str) -> ConversationPlan: ...


def _scenario_reference_tokens(result: RoadmapResult) -> list[tuple[object, tuple[str, ...]]]:
    references = []
    for scenario in (result.recommended, *result.alternatives):
        title = re.sub(r"\[[^]]+\]", "", scenario.title).strip()
        tokens = tuple(
            token
            for token in (title, *title.split())
            if len(token) >= 2 and token not in {"적금", "예금", "정책", "투자"}
        )
        references.append((scenario, tokens))
    return references


def _matches_current_scenario(result: RoadmapResult | None, message: str) -> bool:
    if result is None:
        return False
    return any(
        token in message
        for _, tokens in _scenario_reference_tokens(result)
        for token in tokens
    )


# 로드맵 생성 전 사전 체크(backend/app/service.py)에서도 그대로 재사용하는
# 문구 — 여러 곳에 복붙하지 않도록 여기 하나만 둔다.
FINANCIAL_INCOME_TAXED_QUESTION = "최근 3년 안에 금융소득종합과세 대상이 된 적이 있나요?"
IS_SME_EMPLOYEE_QUESTION = "현재 중소기업에 재직 중인가요?"
HOUSEHOLD_MONTHLY_INCOME_QUESTION = "가구 전체의 월소득은 얼마인가요?"
PREVIOUS_ANNUAL_INCOME_QUESTION = "직전년도(전년도) 실제 연 소득은 세전 기준으로 얼마였나요?"

_PENDING_POLICY_QUESTIONS = (
    HOUSEHOLD_MONTHLY_INCOME_QUESTION,
    FINANCIAL_INCOME_TAXED_QUESTION,
    IS_SME_EMPLOYEE_QUESTION,
    PREVIOUS_ANNUAL_INCOME_QUESTION,
)


def _awaiting_policy_answer(context: str) -> bool:
    return any(question in context for question in _PENDING_POLICY_QUESTIONS)


def classify_intent(
    message: str, result: RoadmapResult | None = None, context: str = ""
) -> ConversationIntent:
    text = " ".join(message.strip().split())
    if not text:
        return ConversationIntent.UNCLEAR
    if _CHANGE_TERMS.search(text) or _INVESTMENT_CHANGE.search(text):
        return ConversationIntent.CONDITION_CHANGE
    if _MISSING_TERMS.search(text):
        return ConversationIntent.INPUT_COMPLETION
    if _PRODUCT_RANKING.search(text):
        return ConversationIntent.PRODUCT_RANKING
    if _ALTERNATIVE_SEARCH.search(text):
        return ConversationIntent.PRODUCT_ALTERNATIVES
    if _matches_current_scenario(result, text):
        return ConversationIntent.RESULT_EXPLANATION
    if _POLICY_TERMS.search(text):
        return ConversationIntent.POLICY_ELIGIBILITY
    if _CURRENT_RESULT_TERMS.search(text):
        return ConversationIntent.RESULT_EXPLANATION
    if _FINANCE_TERMS.search(text) and not re.search(r"(이|그|현재|추천한)\s*(?:상품|적금|결과)", text):
        return ConversationIntent.FINANCIAL_QA
    if _RESULT_TERMS.search(text):
        return ConversationIntent.RESULT_EXPLANATION
    if _FINANCE_TERMS.search(text):
        return ConversationIntent.FINANCIAL_QA
    if _awaiting_policy_answer(context):
        return ConversationIntent.POLICY_ELIGIBILITY
    return ConversationIntent.UNCLEAR


def _validated_llm_plan(plan: ConversationPlan) -> ConversationPlan:
    expected_tools = INTENT_TOOLS[plan.intent]
    if any(tool not in expected_tools for tool in plan.tools):
        raise ValueError("허용되지 않은 도구가 포함된 실행 계획입니다.")
    changes = plan.structured_changes or {}
    if any(field not in ALLOWED_CHANGE_FIELDS for field in changes):
        raise ValueError("허용되지 않은 조건 변경이 포함된 실행 계획입니다.")
    if plan.intent == ConversationIntent.CONDITION_CHANGE and not changes:
        raise ValueError("조건 변경값이 없는 실행 계획입니다.")
    return replace(plan, tools=expected_tools, planned_by="llm")


def plan_conversation(
    request: RoadmapRequest,
    message: str,
    planner: ConversationPlanner | None = None,
    result: RoadmapResult | None = None,
    context: str = "",
) -> ConversationPlan:
    intent = classify_intent(message, result, context)
    if intent == ConversationIntent.CONDITION_CHANGE:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.RESULT_EXPLANATION:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.PRODUCT_ALTERNATIVES:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.PRODUCT_RANKING:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.FINANCIAL_QA:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.INPUT_COMPLETION:
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if intent == ConversationIntent.POLICY_ELIGIBILITY:
        missing = policy_qualification_gaps(request, message, context)
        if missing:
            if _awaiting_policy_answer(context) and _DONT_KNOW.search(message):
                return ConversationPlan(
                    intent,
                    (),
                    f"{missing[0]} 확인되지 않으면 이 조건은 정확히 판정할 수 없어 "
                    "참고용으로만 안내됩니다. 알게 되면 다시 알려주세요.",
                )
            return ConversationPlan(intent, (), missing[0])
        return ConversationPlan(intent, INTENT_TOOLS[intent])
    if planner is not None:
        try:
            return _validated_llm_plan(planner.plan(request, message))
        except Exception:
            pass
    return ConversationPlan(
        intent,
        (),
        "조건을 변경하려는 것인지, 추천 이유나 금융 제도를 묻는 것인지 알려주세요.",
    )


def policy_qualification_gaps(
    request: RoadmapRequest, message: str, context: str = ""
) -> list[str]:
    awaiting = _awaiting_policy_answer(context)
    gaps: list[str] = []
    if effective_household_monthly_income(request) is None:
        gaps.append(HOUSEHOLD_MONTHLY_INCOME_QUESTION)
    if request.financial_income_taxed is None and (
        awaiting or re.search(r"(우대형|금융소득|자격)", message)
    ):
        gaps.append(FINANCIAL_INCOME_TAXED_QUESTION)
    if request.is_sme_employee is None and (
        awaiting or re.search(r"(우대형|중소기업)", message)
    ):
        gaps.append(IS_SME_EMPLOYEE_QUESTION)
    if request.previous_annual_income is None and (
        awaiting or re.search(r"(직전년도|전년도|작년)\s*소득", message)
    ):
        gaps.append(PREVIOUS_ANNUAL_INCOME_QUESTION)
    return gaps


def apply_policy_answers(
    request: RoadmapRequest, message: str, context: str = ""
) -> tuple[RoadmapRequest, list[str]]:
    changes: dict[str, object] = {}
    descriptions: list[str] = []
    income = re.search(
        r"가구(?:\s*전체)?\s*(?:월)?소득\D{0,12}([\d,.]+)\s*(천만|만)?\s*원",
        message,
    )
    if income:
        value = float(income.group(1).replace(",", ""))
        multiplier = {None: 1, "만": 10_000, "천만": 10_000_000}[income.group(2)]
        amount = round(value * multiplier)
        changes["household_monthly_income"] = amount
        descriptions.append(f"가구 월소득 {amount:,}원")
    elif (
        request.household_monthly_income is None
        and HOUSEHOLD_MONTHLY_INCOME_QUESTION in context
        and PREVIOUS_ANNUAL_INCOME_QUESTION not in context
    ):
        # 직전에 가구 월소득을 물었다면, 단위 없이 숫자만 온 답도 만원 단위로 받는다.
        # 직전년도 소득도 같은 방식(숫자만 답)으로 되묻으므로, 두 질문 문구가
        # context 에 동시에 있으면(최근 10개 메시지 중에 둘 다 물어본 적이 있으면)
        # 어느 쪽 답인지 알 수 없다 — 이럴 땐 추측해서 잘못 채우지 말고 무시한다.
        bare_number = re.fullmatch(r"[\d,]+(?:\.\d+)?", message.strip())
        if bare_number:
            amount = round(float(bare_number.group().replace(",", "")) * 10_000)
            changes["household_monthly_income"] = amount
            descriptions.append(f"가구 월소득 {amount:,}원")
    if re.search(
        r"금융소득종합과세.{0,12}(?:없|아니|해당되지|안\s*(?:됐|되|냈|낸|함|해))", message
    ):
        changes["financial_income_taxed"] = False
        descriptions.append("금융소득종합과세 이력 없음")
    elif re.search(r"금융소득종합과세.{0,12}(?:있|맞|해당)", message):
        changes["financial_income_taxed"] = True
        descriptions.append("금융소득종합과세 이력 있음")
    elif (
        request.financial_income_taxed is None
        and "금융소득종합과세" in context
        and "중소기업" not in context
    ):
        # 직전에 이 질문을 물었다면 "응 있어"/"아니요" 같은 짧은 구어체 단답도 인식한다.
        # 중소기업 재직 여부도 같은 방식(예/아니오)으로 되묻으므로, 두 질문이 모두
        # context 에 있으면 어느 쪽 답인지 알 수 없어 추측하지 않는다.
        stripped = message.strip()
        if _NO_ANSWER.search(stripped):
            changes["financial_income_taxed"] = False
            descriptions.append("금융소득종합과세 이력 없음")
        elif _YES_ANSWER.search(stripped):
            changes["financial_income_taxed"] = True
            descriptions.append("금융소득종합과세 이력 있음")
    if re.search(
        r"중소기업.{0,10}(?:재직|다니).{0,10}(?:아니|않|안\s*(?:함|해|다님|다녀))"
        r"|중소기업.{0,15}안\s*(?:재직|다니|다녀)",
        message,
    ):
        changes["is_sme_employee"] = False
        descriptions.append("중소기업 재직 아님")
    elif re.search(r"중소기업.{0,10}(?:재직|다니)", message):
        changes["is_sme_employee"] = True
        descriptions.append("중소기업 재직")
    elif (
        request.is_sme_employee is None
        and "중소기업" in context
        and "금융소득종합과세" not in context
    ):
        # 같은 이유로 두 질문이 동시에 context 에 있으면 무시한다.
        stripped = message.strip()
        if _NO_ANSWER.search(stripped):
            changes["is_sme_employee"] = False
            descriptions.append("중소기업 재직 아님")
        elif _YES_ANSWER.search(stripped):
            changes["is_sme_employee"] = True
            descriptions.append("중소기업 재직")
    prev_income = re.search(
        r"(?:직전년도|전년도|작년)\s*(?:연)?\s*소득\D{0,12}([\d,.]+)\s*(천만|만)?\s*원",
        message,
    )
    if prev_income:
        value = float(prev_income.group(1).replace(",", ""))
        multiplier = {None: 1, "만": 10_000, "천만": 10_000_000}[prev_income.group(2)]
        amount = round(value * multiplier)
        changes["previous_annual_income"] = amount
        descriptions.append(f"직전년도 연 소득 {amount:,}원")
    elif (
        request.previous_annual_income is None
        and PREVIOUS_ANNUAL_INCOME_QUESTION in context
        and HOUSEHOLD_MONTHLY_INCOME_QUESTION not in context
    ):
        # 가구 월소득과 같은 이유로 두 질문이 동시에 context 에 있으면 무시한다.
        bare_number = re.fullmatch(r"[\d,]+(?:\.\d+)?", message.strip())
        if bare_number:
            amount = round(float(bare_number.group().replace(",", "")) * 10_000)
            changes["previous_annual_income"] = amount
            descriptions.append(f"직전년도 연 소득 {amount:,}원")
    if not changes:
        return request, []
    updated = replace(request, **changes)
    updated.validate()
    return updated, descriptions


def apply_structured_changes(
    request: RoadmapRequest, changes: dict[str, object]
) -> tuple[RoadmapRequest, list[str]]:
    if any(field not in ALLOWED_CHANGE_FIELDS for field in changes):
        raise ValueError("변경할 수 없는 조건이 포함되어 있습니다.")
    normalized: dict[str, object] = {}
    descriptions: list[str] = []
    for field, raw_value in changes.items():
        if field in {"monthly_budget", "horizon_months", "target_amount"}:
            value: object = int(raw_value)
        elif field == "max_investment_ratio":
            value = float(raw_value)
        elif field == "max_investment_ratio_delta":
            current = request.max_investment_ratio or 0
            value = round(min(max(current + float(raw_value), 0), 1), 2)
            field = "max_investment_ratio"
        elif field == "has_emergency_fund":
            if not isinstance(raw_value, bool):
                raise ValueError("비상자금 여부는 참 또는 거짓이어야 합니다.")
            value = raw_value
        else:
            continue
        normalized[field] = value
        if field == "monthly_budget":
            descriptions.append(f"월 투입액 {value:,}원")
        elif field == "horizon_months":
            descriptions.append(f"목표기간 {value}개월")
        elif field == "target_amount":
            descriptions.append(f"목표금액 {value:,}원")
        elif field == "max_investment_ratio":
            descriptions.append(f"투자비중 상한 {value:.0%}")
        else:
            descriptions.append("비상자금 보유" if value else "비상자금 미보유")
    updated = replace(request, **normalized)
    updated.validate()
    return updated, descriptions


def input_gap_reply(request: RoadmapRequest) -> str:
    gaps: list[str] = []
    if request.household_monthly_income is None:
        gaps.append("가구 전체 월소득")
    if request.financial_income_taxed is None:
        gaps.append("금융소득종합과세 이력")
    if request.is_sme_employee is None:
        gaps.append("중소기업 재직 여부")
    if request.previous_annual_income is None:
        gaps.append("직전년도 연 소득")
    if not gaps:
        return "현재 기본 계산에 필요한 입력은 모두 갖춰져 있습니다."
    return "정책 자격을 더 정확히 확인하려면 " + ", ".join(gaps) + "가 필요합니다."


def alternative_products_reply(
    request: RoadmapRequest,
    result: RoadmapResult,
    message: str,
    policy_repository: PolicyRepository,
    savings_repository: SavingsProductRepository,
) -> str:
    current_titles = {
        re.sub(r"\[[^]]+\]", "", scenario.title).replace(" ", "")
        for scenario in (result.recommended, *result.alternatives)
    }

    def is_current(name: str) -> bool:
        normalized = name.replace(" ", "")
        return any(normalized in title or title in normalized for title in current_titles)

    policy_only = "정책" in message
    savings_only = bool(re.search(r"적금|예금", message)) and not policy_only
    items: list[str] = []
    if not savings_only:
        try:
            policies = policy_repository.find_candidates(replace(request, question=message))
        except Exception:
            policies = []
        for policy in policies:
            if is_current(policy.name):
                continue
            status = "자격 가능" if policy.eligible else "추가 자격 확인"
            items.append(
                f"{policy.name}({status}, 월 한도 {policy.monthly_limit:,}원)"
            )
            if len(items) >= 3:
                break
    if not policy_only and len(items) < 3:
        try:
            savings = savings_repository.find_candidates(request)
        except Exception:
            savings = []
        for product in savings:
            name = f"{product.company_name} {product.product_name}".strip()
            if is_current(name):
                continue
            limit = (
                f"월 한도 {product.maximum_monthly_payment:,}원"
                if product.maximum_monthly_payment is not None
                else "월 한도 확인 필요"
            )
            items.append(f"{name}(기본금리 {product.annual_base_rate:.2%}, {limit})")
            if len(items) >= 3:
                break
    if not items:
        category = "정책상품" if policy_only else "금융상품"
        return (
            f"현재 연결된 DB에서 지금 추천 결과 외에 비교 가능한 {category}을 찾지 못했습니다. "
            "검색 범위나 자격조건을 바꾸어 다시 확인할 수 있습니다."
        )
    category = "정책상품" if policy_only else "금융상품"
    return f"현재 DB에서 추가로 비교 가능한 {category}은 " + ", ".join(items) + "입니다."


def ranked_products_reply(
    request: RoadmapRequest,
    result: RoadmapResult,
    message: str,
    context: str,
    policy_repository: PolicyRepository,
    savings_repository: SavingsProductRepository,
) -> str:
    current_titles = " ".join(
        re.sub(r"\[[^]]+\]", "", scenario.title)
        for scenario in (result.recommended, *result.alternatives)
    ).replace(" ", "")

    def is_current(name: str) -> bool:
        return name.replace(" ", "") in current_titles

    reference = f"{context} {message}"
    policy_only = "정책" in message or ("그중" in message and "정책" in context)
    savings_only = bool(re.search(r"적금|예금", message)) and not policy_only
    ranked: list[tuple[float, str, str]] = []

    if not savings_only:
        try:
            policies = policy_repository.find_candidates(replace(request, question=reference))
        except Exception:
            policies = []
        for policy in policies:
            if is_current(policy.name):
                continue
            support_ratio = policy.estimated_support / max(
                request.monthly_budget * request.horizon_months, 1
            )
            score = (
                (100 if policy.eligible else 0)
                + min(support_ratio * 100, 30)
                + (10 if policy.maturity_months <= request.horizon_months else 0)
                + (5 if policy.application_open is True else 0)
                + min(policy.monthly_limit / request.monthly_budget, 1) * 10
            )
            qualification = "자격 가능" if policy.eligible else "추가 자격 확인"
            reason = (
                f"{qualification}, 예상 지원 {policy.estimated_support:,}원, "
                f"월 한도 {policy.monthly_limit:,}원"
            )
            ranked.append((score, policy.name, reason))

    if not policy_only:
        try:
            products = savings_repository.find_candidates(request)
        except Exception:
            products = []
        for product in products:
            name = f"{product.company_name} {product.product_name}".strip()
            if is_current(name):
                continue
            limit_ratio = (
                min(product.maximum_monthly_payment / request.monthly_budget, 1)
                if product.maximum_monthly_payment is not None
                else 0.5
            )
            score = (
                product.annual_preferential_rate * 100
                + (10 if product.term_months <= request.horizon_months else 0)
                + limit_ratio * 10
            )
            limit = (
                f"월 한도 {product.maximum_monthly_payment:,}원"
                if product.maximum_monthly_payment is not None
                else "월 한도 확인 필요"
            )
            reason = (
                f"최고금리 {product.annual_preferential_rate:.2%}, "
                f"{product.term_months}개월, {limit}"
            )
            ranked.append((score, name, reason))

    if not ranked:
        return "현재 DB에서 기존 추천 외에 순위를 계산할 후보를 찾지 못했습니다."
    ordered = sorted(ranked, key=lambda item: (-item[0], item[1]))[:5]
    entries = [f"{index}위 {name}({reason})" for index, (_, name, reason) in enumerate(ordered, 1)]
    provisional = any("추가 자격 확인" in reason for _, _, reason in ordered)
    prefix = "현재 입력 기준 잠정 순위는 " if provisional else "현재 입력 기준 순위는 "
    suffix = (
        " 자격 확인이 필요한 후보는 누락 정보를 확인하면 순위가 바뀔 수 있습니다."
        if provisional else ""
    )
    return prefix + ", ".join(entries) + "입니다." + suffix


def evidence_reply(
    question: str,
    evidence: list[Evidence],
    answerer: object | None = None,
) -> str:
    if not evidence:
        return "공식 근거 문서에서 답을 확인하지 못했습니다. 질문의 제도명이나 연도를 구체적으로 알려주세요."
    if answerer is not None and hasattr(answerer, "answer_financial_question"):
        try:
            return answerer.answer_financial_question(question, evidence)
        except Exception:
            pass
    relevant = [item for item in evidence if item.content]
    if not relevant:
        return "검색 결과에 질문을 판단할 본문이 없어 답을 확인할 수 없습니다. 연결된 공식 약관에서 해당 조건을 확인해 주세요."
    return (
        "검색된 공식 문서 본문만으로는 질문의 조건을 확정할 수 없습니다. "
        "상품의 최신 공식 약관이나 운영기관 안내에서 가입 후 자격 변경 조항을 확인해 주세요."
    )


def _referenced_scenario(result: RoadmapResult, message: str):
    scenarios = [result.recommended, *result.alternatives]
    named_matches = [
        scenario
        for scenario, tokens in _scenario_reference_tokens(result)
        if any(token in message for token in tokens)
    ]
    if len(named_matches) == 1:
        return named_matches[0]
    if re.search(r"최우선|추천\s*상품|추천안", message):
        return result.recommended
    if "대안" in message and result.alternatives:
        return result.alternatives[0]
    kind_terms = {
        "savings": ("적금", "예금"),
        "policy": ("정책", "지원금", "정부기여금"),
        "investment": ("투자", "주식", "채권"),
        "balanced": ("혼합", "균형"),
    }
    matching_kinds = {
        kind
        for kind, terms in kind_terms.items()
        if any(term in message for term in terms)
    }
    kind_matches = [scenario for scenario in scenarios if scenario.kind in matching_kinds]
    if len(kind_matches) == 1:
        return kind_matches[0]
    return result.recommended


def result_reply(result: RoadmapResult, message: str = "", context: str = "") -> str:
    reference_text = f"{context} {message}".strip()
    scenario = _referenced_scenario(result, reference_text)
    if re.search(r"왜|이유", message) and _LIMIT_QUESTION.search(context):
        if scenario.monthly_limit is not None:
            return (
                f"{scenario.title}의 공식 상품 데이터에 월 최대 납입액이 "
                f"{scenario.monthly_limit:,}원으로 공시되어 있기 때문입니다. "
                "추천 계산에서도 이 한도를 넘지 않도록 반영했습니다."
            )
        return (
            f"{scenario.title}의 공식 수집 데이터에 최대 월 납입액이 제공되지 않았기 "
            "때문입니다. 한도가 없다는 뜻은 아니며, 현재 데이터만으로는 한도 유무와 "
            "금액을 확정할 수 없습니다."
        )
    if _LIMIT_QUESTION.search(message):
        if scenario.monthly_limit is not None:
            return f"{scenario.title}의 월 납입한도는 최대 {scenario.monthly_limit:,}원입니다."
        return f"{scenario.title}은 현재 데이터에서 월 납입한도를 확인할 수 없습니다."
    reasons = " ".join(scenario.rationale[:2])
    label = "현재 최우선안" if scenario == result.recommended else "현재 대안"
    return f"{label}은 {scenario.title}입니다. {reasons}".strip()


def explain_existing_result(
    request: RoadmapRequest,
    result: RoadmapResult,
    message: str,
    explainer: RoadmapExplainer | None,
    context: str = "",
) -> tuple[RoadmapResult, str]:
    contextual_limit_followup = bool(
        re.search(r"왜|이유", message) and _LIMIT_QUESTION.search(context)
    )
    if explainer is None or _LIMIT_QUESTION.search(message) or contextual_limit_followup:
        reply = result_reply(result, message, context)
        return replace(result, chat_reply=reply), reply
    explanation = explainer.explain(replace(request, question=message), result)
    reply = explanation.chat_reply or explanation.recommended_reason
    return replace(
        result,
        recommended_reason=explanation.recommended_reason,
        alternative_reason=explanation.alternative_reason,
        chat_reply=reply,
    ), reply


def execute_conversation(
    request: RoadmapRequest,
    result: RoadmapResult,
    message: str,
    *,
    run_roadmap_fn,
    policy_repository: PolicyRepository,
    savings_repository: SavingsProductRepository,
    retriever: RagRetriever,
    explainer: RoadmapExplainer | None = None,
    planner: ConversationPlanner | None = None,
    context: str = "",
) -> ConversationResponse:
    t0 = time.monotonic()
    print(f"[CV-01] execute_conversation 진입 | message={message[:80]!r}")
    updated_policy_request, policy_changes = apply_policy_answers(request, message, context)
    plan = plan_conversation(updated_policy_request, message, planner, result, context)
    print(
        f"[CV-02] intent={plan.intent.value} planned_by={plan.planned_by} "
        f"tools={list(plan.tools)}"
        + (
            f" clarification={plan.clarification_question!r}"
            if plan.clarification_question
            else ""
        )
    )

    def _finish(response: ConversationResponse) -> ConversationResponse:
        print(
            f"[CV-09] 응답 완료 | status={response.status.value} "
            f"tools={list(response.executed_tools)} | {time.monotonic()-t0:.2f}s"
        )
        return response

    if plan.clarification_question:
        return _finish(ConversationResponse(
            ConversationStatus.NEEDS_INPUT,
            plan.intent,
            updated_policy_request,
            result,
            plan.clarification_question,
            state_history=("draft", "needs_input"),
        ))
    if plan.intent == ConversationIntent.CONDITION_CHANGE:
        if plan.structured_changes:
            updated, changes = apply_structured_changes(request, plan.structured_changes)
        else:
            try:
                updated, changes = apply_conversation_change(
                    updated_policy_request, message
                )
            except ValueError:
                if not policy_changes:
                    raise
                updated, changes = updated_policy_request, policy_changes
        updated_result = run_roadmap_fn(
            updated,
            policy_repository=policy_repository,
            savings_repository=savings_repository,
            retriever=retriever,
            explainer=explainer,
        )
        reply = " · ".join(changes) + " 조건을 반영해 전체 로드맵을 다시 계산했습니다."
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED,
            plan.intent,
            updated,
            updated_result,
            reply,
            plan.tools,
            tuple(changes),
        ))
    if plan.intent == ConversationIntent.RESULT_EXPLANATION:
        try:
            explained, reply = explain_existing_result(
                request, result, message, explainer, context
            )
        except Exception:
            reply = result_reply(result, message, context)
            explained = replace(result, chat_reply=reply)
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED, plan.intent, request, explained, reply, plan.tools
        ))
    if plan.intent == ConversationIntent.PRODUCT_ALTERNATIVES:
        reply = alternative_products_reply(
            request, result, message, policy_repository, savings_repository
        )
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED,
            plan.intent,
            request,
            replace(result, chat_reply=reply),
            reply,
            plan.tools,
        ))
    if plan.intent == ConversationIntent.PRODUCT_RANKING:
        reply = ranked_products_reply(
            request,
            result,
            message,
            context,
            policy_repository,
            savings_repository,
        )
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED,
            plan.intent,
            request,
            replace(result, chat_reply=reply),
            reply,
            plan.tools,
        ))
    if plan.intent == ConversationIntent.FINANCIAL_QA:
        try:
            evidence = retriever.search(message, limit=3)
        except Exception:
            evidence = []
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED,
            plan.intent,
            request,
            result,
            evidence_reply(message, evidence, explainer),
            plan.tools,
            evidence=tuple(evidence),
        ))
    if plan.intent == ConversationIntent.INPUT_COMPLETION:
        return _finish(ConversationResponse(
            ConversationStatus.COMPLETED,
            plan.intent,
            request,
            result,
            input_gap_reply(request),
            plan.tools,
        ))

    # 이번 답변으로 실제 필드가 바뀌었으면(예: financial_income_taxed 확정) 텍스트
    # 설명만 새로 만들 게 아니라 로드맵 자체를 재계산해야 한다 — 그래야 오른쪽
    # 로드맵 패널(추천 상품·예상액)도 답변을 반영해 바뀐다. 그전까지는 이 분기가
    # `result`를 그대로 통과시켜, 사용자가 답해도 패널이 안 바뀌는 버그가 있었다.
    if policy_changes:
        result = run_roadmap_fn(
            updated_policy_request,
            policy_repository=policy_repository,
            savings_repository=savings_repository,
            retriever=retriever,
            explainer=explainer,
        )
    policies = policy_repository.find_candidates(replace(updated_policy_request, question=message))
    eligible = [item for item in policies if item.eligible]
    if eligible:
        details = []
        for item in eligible[:3]:
            availability = {
                True: "현재 모집 확인",
                False: "현재 모집 종료",
                None: "모집상태 확인 필요",
            }[item.application_open]
            qualification = {
                "confirmed": "자격 확인",
                "needs_input": "추가정보 필요",
                "needs_verification": "운영기관 확인 필요",
                "ineligible": "대상 아님",
            }.get(item.qualification_status, "판정 확인 필요")
            tier = {
                "standard": "일반형",
                "preferential": "우대형",
                "preferential_possible": "우대형 가능성",
            }.get(item.benefit_tier, item.benefit_tier)
            missing = (
                ", 누락: " + ", ".join(item.missing_qualification_fields)
                if item.missing_qualification_fields else ""
            )
            details.append(
                f"{item.name}({qualification}, {tier}, {availability}{missing}: {item.reason})"
            )
        reply = "현재 입력으로 자격 가능성이 확인된 정책상품은 " + ", ".join(details) + "입니다."
    else:
        reply = "현재 입력과 조회 결과로 자격이 확인된 정책상품이 없습니다. 탈락 사유와 모집상태를 공식 공고에서 다시 확인해 주세요."
    return _finish(ConversationResponse(
        ConversationStatus.COMPLETED,
        plan.intent,
        updated_policy_request,
        result,
        reply,
        plan.tools,
        tuple(policy_changes),
    ))
