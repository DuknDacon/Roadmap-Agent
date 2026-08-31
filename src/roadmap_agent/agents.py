from __future__ import annotations

from .calculators import (
    additional_months_for_target,
    achievement_rate,
    investment_projection,
    investment_cap,
    required_monthly_for_target,
    required_monthly_for_savings_product,
    savings_projection,
    savings_product_projection,
    target_gap,
)
from .domain import Evidence, RoadmapRequest, Scenario
from .ports import EmptySavingsProductRepository, PolicyRepository, RagRetriever, SavingsProductRepository


def _ranked_savings_products(request: RoadmapRequest, repository: SavingsProductRepository):
    candidates = repository.find_candidates(request)
    ranked = []
    for product in candidates:
        if product.term_months > request.horizon_months:
            continue
        monthly = min(request.monthly_budget, product.maximum_monthly_payment or request.monthly_budget)
        projection = savings_product_projection(
            monthly,
            product.term_months,
            product.annual_base_rate,
            product.annual_preferential_rate,
            product.tax_rate,
        )
        unallocated = (request.monthly_budget - monthly) * product.term_months
        ranked.append(
            (
                projection.maximum + unallocated,
                projection.base + unallocated,
                product,
                monthly,
                projection,
            )
        )
    ranked = sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True)
    unique_products = []
    seen: set[str] = set()
    for item in ranked:
        product = item[2]
        key_parts = product.product_id.split(":")
        logical_product_id = (
            ":".join(key_parts[:3])
            if len(key_parts) >= 3
            else f"{product.company_name}:{product.product_name}"
        )
        if logical_product_id in seen:
            continue
        seen.add(logical_product_id)
        unique_products.append(item)
    return unique_products


def _best_savings_product(request: RoadmapRequest, repository: SavingsProductRepository):
    ranked = _ranked_savings_products(request, repository)
    return ranked[0] if ranked else None


def _fallback_savings_scenario(request: RoadmapRequest, retriever: RagRetriever) -> Scenario:
    projection = savings_projection(request.monthly_budget, request.horizon_months)
    rate = 0.03
    return Scenario(
        kind="savings",
        title="[적금 시나리오] 예·적금 중심",
        monthly_allocation={"savings": request.monthly_budget},
        expected_min=projection.minimum,
        expected_base=projection.base,
        expected_max=projection.maximum,
        goal_achievement_rate=achievement_rate(projection.base, request.target_amount),
        principal=projection.contributed,
        rationale=["월 투입 가능액 전부를 규칙적으로 적립하는 기준 시나리오입니다."],
        warnings=["연 3% 세전 가정이며 실제 금리·세금·우대조건은 상품 데이터로 재계산해야 합니다."],
        shortfall=target_gap(projection.base, request.target_amount),
        required_monthly_budget=required_monthly_for_target(
            request.target_amount, request.horizon_months, rate
        ),
        additional_months=additional_months_for_target(
            request.monthly_budget, request.horizon_months, request.target_amount, rate
        ),
        evidence=retriever.search("적금 우대금리 중도해지 저축", 2),
        data_status="mock_rate_until_finlife_connected",
    )


def _savings_product_scenario(
    request: RoadmapRequest,
    ranked_product,
    common_evidence: list[Evidence],
) -> Scenario:
    _, _, product, monthly, projection = ranked_product
    unallocated_monthly = request.monthly_budget - monthly
    unallocated_total = unallocated_monthly * product.term_months
    allocation = {product.product_name: monthly, "unallocated_cash": unallocated_monthly}
    option_parts = [
        value
        for value in (
            product.savings_type_name,
            product.interest_type_name,
            f"{product.term_months}개월",
        )
        if value
    ]
    option_description = " · ".join(option_parts)
    return Scenario(
        kind="savings",
        title=f"[적금 시나리오] {product.company_name} {product.product_name}",
        monthly_allocation=allocation,
        expected_min=projection.minimum + unallocated_total,
        expected_base=projection.base + unallocated_total,
        expected_max=projection.maximum + unallocated_total,
        goal_achievement_rate=achievement_rate(
            projection.maximum + unallocated_total, request.target_amount
        ),
        principal=request.monthly_budget * product.term_months,
        rationale=[
            "목표기간 안에 만기되는 실제 적금상품 후보입니다.",
            f"동일 상품의 옵션 중 {option_description} 조건을 계산에 사용했습니다.",
            f"적용 금리는 기본 {product.annual_base_rate:.2%}, 최고 {product.annual_preferential_rate:.2%}입니다.",
            "추천 비교에는 우대조건 충족 시 최고금리 예상액을 사용했습니다.",
            *(
                [f"상품 한도를 초과한 월 {unallocated_monthly:,}원은 수익률 0%의 현금으로 합산했습니다."]
                if unallocated_monthly
                else []
            ),
        ],
        warnings=["우대금리는 조건 충족 시에만 적용되므로 기본 예상액과 최대 예상액을 구분합니다."],
        unallocated_cash=unallocated_total,
        shortfall=target_gap(projection.maximum + unallocated_total, request.target_amount),
        required_monthly_budget=required_monthly_for_savings_product(
            request.target_amount,
            product.term_months,
            product.annual_base_rate,
            product.tax_rate,
        ),
        additional_months=None,
        evidence=[
            Evidence(
                title=f"{product.company_name} {product.product_name} 공시정보",
                source_url=product.source_url,
                path=f"finlife:{product.product_id}",
                score=100,
            ),
            *common_evidence,
        ],
        data_status="structured_finlife_candidate",
        monthly_limit=product.maximum_monthly_payment,
    )


def savings_candidate_scenarios(
    request: RoadmapRequest,
    retriever: RagRetriever,
    repository: SavingsProductRepository | None = None,
    *,
    limit: int = 3,
) -> list[Scenario]:
    ranked = _ranked_savings_products(
        request, repository or EmptySavingsProductRepository()
    )
    if not ranked:
        return [_fallback_savings_scenario(request, retriever)]
    common_evidence = retriever.search("적금 우대금리 중도해지 저축", 1)
    return [
        _savings_product_scenario(request, item, common_evidence)
        for item in ranked[:limit]
    ]


def savings_agent(
    request: RoadmapRequest,
    retriever: RagRetriever,
    repository: SavingsProductRepository | None = None,
) -> Scenario:
    return savings_candidate_scenarios(request, retriever, repository, limit=1)[0]


def investment_agent(request: RoadmapRequest, retriever: RagRetriever) -> Scenario:
    ratio = investment_cap(
        request.risk_profile, request.horizon_months, request.max_investment_ratio
    )
    investing_monthly = round(request.monthly_budget * ratio)
    cash_monthly = request.monthly_budget - investing_monthly
    projection = investment_projection(
        investing_monthly, request.horizon_months, request.risk_profile
    )
    cash = savings_projection(cash_monthly, request.horizon_months, annual_rate=0)
    minimum = projection.minimum + cash.minimum
    base = projection.base + cash.base
    maximum = projection.maximum + cash.maximum
    return Scenario(
        kind="investment",
        title="[투자 시나리오] 분산투자 중심",
        monthly_allocation={"cash_equivalent": cash_monthly, "diversified_investment": investing_monthly},
        expected_min=minimum,
        expected_base=base,
        expected_max=maximum,
        goal_achievement_rate=achievement_rate(base, request.target_amount),
        principal=request.monthly_budget * request.horizon_months,
        rationale=[
            f"위험성향·기간·사용자 상한을 적용해 투자 비중을 {ratio:.0%}로 제한했습니다.",
            "투자 부분은 현금성·채권형·주식형 등 자산군 수준으로만 설명합니다.",
        ],
        warnings=[
            "예상 범위는 보장 수익이 아니며 원금손실이 가능합니다.",
            "특정 종목·펀드의 매수를 추천하지 않습니다.",
        ],
        shortfall=target_gap(base, request.target_amount),
        evidence=retriever.search("ISA 투자 위험 분산투자 원금손실", 3),
        data_status="education_range_not_forecast",
    )


def policy_candidate_scenarios(
    request: RoadmapRequest,
    retriever: RagRetriever,
    repository: PolicyRepository,
    *,
    limit: int = 3,
) -> list[Scenario]:
    candidates = [
        item
        for item in repository.find_candidates(request)
        if item.eligible and item.maturity_months <= request.horizon_months
    ]
    if not candidates:
        return []
    common_evidence = retriever.search("청년 정책 정부기여금 자산형성", 2)
    scenarios = []
    for policy in sorted(
        candidates, key=lambda item: item.estimated_support, reverse=True
    )[:limit]:
        monthly = min(request.monthly_budget, policy.monthly_limit)
        unallocated = request.monthly_budget - monthly
        principal = request.monthly_budget * request.horizon_months
        minimum_support = (
            round(monthly * policy.maturity_months * policy.support_rate)
            if policy.support_rate is not None
            else policy.estimated_support
        )
        best_rate = policy.support_rate
        if (
            policy.preferential_support_rate is not None
            and policy.benefit_tier == "preferential"
        ):
            best_rate = policy.preferential_support_rate
        best_support = (
            round(monthly * policy.maturity_months * best_rate)
            if best_rate is not None
            else policy.estimated_support
        )
        minimum = principal + minimum_support
        base = principal + best_support
        additional_checks = [
            item.strip()
            for item in policy.reason.split(";")
            if "필요" in item or "가능성" in item
        ]
        confirmed_checks = [
            item.strip()
            for item in policy.reason.split(";")
            if "필요" not in item and "가능성" not in item
        ]
        availability_warning = (
            "상품 혜택 비교에는 포함했지만 현재 확인된 신청기간은 종료되었습니다. "
            "다음 모집 일정과 실제 가입 가능 여부를 공식 운영기관에서 확인하세요."
            if policy.application_open is False
            else "실제 가입 가능 여부와 수령액은 운영기관 확인 결과에 따라 달라질 수 있습니다."
        )
        scenarios.append(
            Scenario(
                kind="policy",
                title=f"[정책상품 시나리오] {policy.name}",
                monthly_allocation={policy.name: monthly, "unallocated_cash": unallocated},
                expected_min=minimum,
                expected_base=base,
                expected_max=base,
                goal_achievement_rate=achievement_rate(base, request.target_amount),
                principal=principal,
                unallocated_cash=unallocated * request.horizon_months,
                shortfall=target_gap(base, request.target_amount),
                rationale=[
                    *(
                        ["확인된 조건: " + "; ".join(confirmed_checks)]
                        if confirmed_checks
                        else []
                    ),
                    (
                        f"입력한 월 납입액 {monthly:,}원에 우대형 지원율 "
                        f"{best_rate:.0%}를 적용해 정부기여금 {best_support:,}원을 계산했습니다."
                        if best_rate is not None and best_support != minimum_support
                        else f"입력한 월 납입액 {monthly:,}원 기준 정부기여금 {best_support:,}원을 계산했습니다."
                    ),
                    *(
                        ["추가 정보 필요: " + "; ".join(additional_checks)]
                        if additional_checks
                        else []
                    ),
                ],
                warnings=[
                    *(
                        [
                            f"일반형 지원율 {policy.support_rate:.0%} 적용 시 정부기여금은 "
                            f"{minimum_support:,}원입니다. 추천 비교에는 우대형 자격 충족 시 "
                            f"정부기여금 {best_support:,}원을 사용했습니다."
                        ]
                        if best_support != minimum_support
                        else []
                    ),
                    availability_warning,
                ],
                evidence=[
                    Evidence(
                        title=f"{policy.name} 공식정보",
                        source_url=policy.source_url,
                        path=f"policy:{policy.policy_id}",
                        score=100,
                    ),
                    *common_evidence,
                ],
                data_status="structured_policy_candidate",
                monthly_limit=policy.monthly_limit,
            )
        )
    return scenarios


def policy_agent(
    request: RoadmapRequest, retriever: RagRetriever, repository: PolicyRepository
) -> Scenario | None:
    scenarios = policy_candidate_scenarios(request, retriever, repository, limit=1)
    return scenarios[0] if scenarios else None


def balanced_agent(
    request: RoadmapRequest,
    retriever: RagRetriever,
    savings_repository: SavingsProductRepository | None = None,
) -> Scenario:
    investing_ratio = investment_cap(
        request.risk_profile, request.horizon_months, request.max_investment_ratio
    )
    if not request.has_emergency_fund:
        investing_ratio = min(investing_ratio, 0.15)
    investing_pct = round(investing_ratio * 100)
    saving_pct = 100 - investing_pct
    saving_monthly = request.monthly_budget * saving_pct // 100
    investing_monthly = request.monthly_budget - saving_monthly
    unallocated_monthly = 0
    best = _best_savings_product(
        request,
        savings_repository or EmptySavingsProductRepository(),
    )
    if best is None:
        saving = savings_projection(saving_monthly, request.horizon_months)
        savings_label = "savings"
        data_status = "mock_savings_rate_and_education_investment_range"
        savings_warning = "적금 부분의 금리는 임시 가정입니다."
    else:
        _, _, product, _, _ = best
        product_monthly = min(saving_monthly, product.maximum_monthly_payment or saving_monthly)
        saving = savings_product_projection(
            product_monthly,
            product.term_months,
            product.annual_base_rate,
            product.annual_preferential_rate,
            product.tax_rate,
        )
        savings_label = product.product_name
        saving_monthly = product_monthly
        unallocated_monthly = request.monthly_budget - saving_monthly - investing_monthly
        data_status = "structured_finlife_and_education_investment_range"
        savings_warning = "적금 우대금리는 조건 충족 여부에 따라 달라집니다."
    investing = investment_projection(
        investing_monthly, request.horizon_months, request.risk_profile
    )
    unallocated = savings_projection(
        unallocated_monthly, request.horizon_months, annual_rate=0
    )
    base = saving.base + investing.base + unallocated.base
    if investing_monthly == 0:
        title = "[적금 시나리오] 안정형 저축"
        rationale = [
            "요청한 투자 비중 0%를 반영해 투자상품을 배분에서 제외했습니다.",
            "투자상품을 제외하고 실제 적금상품 조건으로 다시 계산했습니다.",
        ]
        if unallocated_monthly:
            rationale.append(
                f"적금상품의 월 납입한도를 초과한 {unallocated_monthly:,}원은 미배분 금액으로 표시했습니다."
            )
        warnings = [savings_warning]
        if best is not None:
            data_status = "structured_finlife_candidate"
    else:
        title = "[균형 시나리오] 적금·분산투자"
        rationale = [
            f"월 예산의 {saving_pct}%는 적립, {investing_pct}%는 분산투자 범위로 나눴습니다.",
            "비상자금 보유 여부와 위험성향을 배분에 반영했습니다.",
        ]
        warnings = [f"투자 부분은 원금손실 가능성이 있습니다. {savings_warning}"]
    return Scenario(
        kind="balanced",
        title=title,
        monthly_allocation={
            savings_label: saving_monthly,
            "diversified_investment": investing_monthly,
            "unallocated_cash": unallocated_monthly,
        },
        expected_min=saving.minimum + investing.minimum + unallocated.minimum,
        expected_base=base,
        expected_max=saving.maximum + investing.maximum + unallocated.maximum,
        goal_achievement_rate=achievement_rate(base, request.target_amount),
        principal=request.monthly_budget * request.horizon_months,
        rationale=rationale,
        warnings=warnings,
        unallocated_cash=unallocated_monthly * request.horizon_months,
        shortfall=target_gap(base, request.target_amount),
        evidence=retriever.search("저축 ISA 분산투자 원금손실 위험", 3),
        data_status=data_status,
    )
