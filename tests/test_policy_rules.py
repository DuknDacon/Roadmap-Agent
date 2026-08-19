import json

import pytest

from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.policy_rules import (
    PolicyRule,
    PolicyRuleCatalog,
    approve_policy_rule,
    evaluate_policy_rule,
)
from roadmap_agent.ports import PolicyBenefit


def rule_mapping(status="extracted", approved_by=None):
    return {
        "policy_id": "policy-1",
        "policy_name": "예시 청년 적금",
        "effective_year": 2026,
        "income_reference_year": 2025,
        "status": status,
        "approved_by": approved_by,
        "eligibility": [
            {"field": "age", "operator": "gte", "value": 19, "reason": "19세 이상"},
            {"field": "age", "operator": "lte", "value": 34, "reason": "34세 이하"},
            {
                "field": "previous_annual_income", "operator": "lte",
                "value": 60_000_000, "reason": "직전연도 총급여 6천만원 이하",
            },
            {
                "field": "household_median_income_ratio", "operator": "lte",
                "value": 2.0, "reason": "가구 중위소득 200% 이하",
            },
            {
                "field": "financial_income_taxed", "operator": "eq",
                "value": False, "reason": "금융소득종합과세 대상 아님",
            },
        ],
        "tiers": [
            {
                "name": "preferential", "support_rate": 0.12,
                "conditions": [
                    {
                        "field": "previous_annual_income", "operator": "lte",
                        "value": 36_000_000, "reason": "우대형 소득기준",
                    },
                    {
                        "field": "household_median_income_ratio", "operator": "lte",
                        "value": 1.5, "reason": "우대형 가구소득기준",
                    },
                    {
                        "field": "is_sme_employee", "operator": "eq",
                        "value": True, "reason": "중소기업 재직",
                    },
                ],
            },
            {"name": "standard", "support_rate": 0.06, "conditions": []},
        ],
        "sources": [{
            "title": "공식 상품 안내",
            "url": "https://example.go.kr/policy",
            "section": "가입 및 지원 조건",
            "excerpt": "일반형 6%, 우대형 12%를 지원한다.",
        }],
    }


def request(**changes):
    values = {
        "monthly_budget": 500_000,
        "horizon_months": 36,
        "target_amount": None,
        "risk_profile": RiskProfile.BALANCED,
        "age": 28,
        "previous_annual_income": 35_000_000,
        "household_size": 1,
        "household_monthly_income": 3_000_000,
        "financial_income_taxed": False,
        "is_sme_employee": True,
    }
    values.update(changes)
    return RoadmapRequest(**values)


def test_extracted_llm_rule_cannot_calculate_until_approved():
    candidate = PolicyRule.from_mapping(rule_mapping())
    with pytest.raises(ValueError, match="검증되지 않은"):
        evaluate_policy_rule(candidate, request())


def test_verified_rule_selects_preferential_tier_deterministically():
    rule = approve_policy_rule(PolicyRule.from_mapping(rule_mapping()), "reviewer")
    decision = evaluate_policy_rule(rule, request())
    assert decision.eligible is True
    assert decision.qualification_status == "confirmed"
    assert decision.benefit_tier == "preferential"
    assert decision.support_rate == 0.12


def test_verified_rule_falls_back_to_standard_tier():
    rule = approve_policy_rule(PolicyRule.from_mapping(rule_mapping()), "reviewer")
    decision = evaluate_policy_rule(
        rule,
        request(previous_annual_income=50_000_000, is_sme_employee=False),
    )
    assert decision.benefit_tier == "standard"
    assert decision.support_rate == 0.06


def test_missing_user_value_is_returned_instead_of_guessed():
    rule = approve_policy_rule(PolicyRule.from_mapping(rule_mapping()), "reviewer")
    # household_size=2로 둬서 1인가구 자동 계산 폴백이 적용되지 않게 한다.
    decision = evaluate_policy_rule(
        rule, request(household_monthly_income=None, household_size=2)
    )
    assert decision.qualification_status == "needs_input"
    assert decision.missing_fields == ("household_median_income_ratio",)


def test_single_person_household_uses_own_income_without_asking():
    rule = approve_policy_rule(PolicyRule.from_mapping(rule_mapping()), "reviewer")
    decision = evaluate_policy_rule(
        rule,
        request(
            household_monthly_income=None,
            household_size=1,
            previous_annual_income=35_000_000,
        ),
    )
    assert decision.qualification_status != "needs_input"


def test_catalog_applies_only_verified_rules(tmp_path):
    verified = rule_mapping("verified", "reviewer")
    (tmp_path / "verified.json").write_text(json.dumps(verified), encoding="utf-8")
    catalog = PolicyRuleCatalog.from_directory(tmp_path)
    policy = PolicyBenefit(
        "policy-1", "예시 청년 적금", True, 500_000, 0, 36,
        "https://example.go.kr/policy", "2026", "원본 조건",
    )
    applied = catalog.apply(policy, request())
    assert applied.benefit_tier == "preferential"
    assert applied.support_rate == 0.12
    assert applied.estimated_support == 2_160_000


def test_invalid_llm_field_is_rejected():
    value = rule_mapping()
    value["eligibility"][0]["field"] = "invented_credit_score"
    with pytest.raises(ValueError, match="허용되지 않은"):
        PolicyRule.from_mapping(value)
