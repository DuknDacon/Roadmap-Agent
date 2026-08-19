from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .domain import RoadmapRequest
from .policy_qualification import effective_household_monthly_income, median_income_monthly
from .ports import PolicyBenefit


ALLOWED_FIELDS = {
    "age",
    "previous_annual_income",
    "financial_income_taxed",
    "is_sme_employee",
    "household_median_income_ratio",
}
ALLOWED_OPERATORS = {"eq", "lte", "gte"}


@dataclass(frozen=True)
class RuleSource:
    title: str
    url: str
    section: str
    excerpt: str


@dataclass(frozen=True)
class RuleCondition:
    field: str
    operator: str
    value: int | float | bool
    reason: str


@dataclass(frozen=True)
class BenefitTierRule:
    name: str
    support_rate: float
    conditions: tuple[RuleCondition, ...]


@dataclass(frozen=True)
class PolicyRule:
    policy_id: str
    policy_name: str
    effective_year: int
    income_reference_year: int
    status: str
    eligibility: tuple[RuleCondition, ...]
    tiers: tuple[BenefitTierRule, ...]
    sources: tuple[RuleSource, ...]
    approved_by: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "PolicyRule":
        def condition(item: dict[str, Any]) -> RuleCondition:
            return RuleCondition(
                field=str(item["field"]),
                operator=str(item["operator"]),
                value=item["value"],
                reason=str(item["reason"]),
            )

        rule = cls(
            policy_id=str(value["policy_id"]),
            policy_name=str(value["policy_name"]),
            effective_year=int(value["effective_year"]),
            income_reference_year=int(value["income_reference_year"]),
            status=str(value.get("status", "extracted")),
            eligibility=tuple(condition(item) for item in value.get("eligibility", [])),
            tiers=tuple(
                BenefitTierRule(
                    name=str(item["name"]),
                    support_rate=float(item["support_rate"]),
                    conditions=tuple(condition(entry) for entry in item.get("conditions", [])),
                )
                for item in value.get("tiers", [])
            ),
            sources=tuple(
                RuleSource(
                    title=str(item["title"]),
                    url=str(item["url"]),
                    section=str(item["section"]),
                    excerpt=str(item["excerpt"]),
                )
                for item in value.get("sources", [])
            ),
            approved_by=value.get("approved_by"),
        )
        validate_policy_rule(rule)
        return rule


@dataclass(frozen=True)
class PolicyRuleDecision:
    eligible: bool
    qualification_status: str
    benefit_tier: str
    support_rate: float
    missing_fields: tuple[str, ...]
    reasons: tuple[str, ...]


def validate_policy_rule(rule: PolicyRule) -> None:
    if rule.status not in {"extracted", "verified"}:
        raise ValueError("정책 규칙 상태는 extracted 또는 verified여야 합니다.")
    if rule.status == "verified" and not rule.approved_by:
        raise ValueError("검증된 정책 규칙에는 승인자가 필요합니다.")
    if not rule.policy_id or not rule.policy_name or not rule.sources:
        raise ValueError("정책 ID, 이름, 출처는 필수입니다.")
    if not 2020 <= rule.income_reference_year <= rule.effective_year <= 2100:
        raise ValueError("정책 규칙의 기준연도가 올바르지 않습니다.")
    if not rule.tiers:
        raise ValueError("정책 규칙에는 하나 이상의 혜택 유형이 필요합니다.")
    for source in rule.sources:
        if not source.url.startswith("https://") or not source.section or not source.excerpt:
            raise ValueError("공식 HTTPS 출처, 섹션, 근거 문장이 필요합니다.")
    for tier in rule.tiers:
        if not 0 <= tier.support_rate <= 1:
            raise ValueError("지원율은 0~1 범위여야 합니다.")
    for condition in (*rule.eligibility, *(c for tier in rule.tiers for c in tier.conditions)):
        if condition.field not in ALLOWED_FIELDS or condition.operator not in ALLOWED_OPERATORS:
            raise ValueError("허용되지 않은 정책 조건 필드 또는 연산자입니다.")


def approve_policy_rule(rule: PolicyRule, approved_by: str) -> PolicyRule:
    if not approved_by.strip():
        raise ValueError("승인자 이름이 필요합니다.")
    approved = replace(rule, status="verified", approved_by=approved_by.strip())
    validate_policy_rule(approved)
    return approved


def _condition_value(condition: RuleCondition, request: RoadmapRequest, year: int):
    if condition.field == "household_median_income_ratio":
        household_income = effective_household_monthly_income(request)
        if request.household_size is None or household_income is None:
            return None
        median = median_income_monthly(year, request.household_size)
        return None if median is None else household_income / median
    return getattr(request, condition.field)


def _matches(condition: RuleCondition, actual: Any) -> bool:
    if condition.operator == "eq":
        return actual == condition.value
    if condition.operator == "lte":
        return actual <= condition.value
    return actual >= condition.value


def evaluate_policy_rule(rule: PolicyRule, request: RoadmapRequest) -> PolicyRuleDecision:
    if rule.status != "verified":
        raise ValueError("검증되지 않은 LLM 추출 규칙은 계산에 사용할 수 없습니다.")
    missing: list[str] = []
    reasons: list[str] = []
    for condition in rule.eligibility:
        actual = _condition_value(condition, request, rule.income_reference_year)
        if actual is None:
            missing.append(condition.field)
        elif not _matches(condition, actual):
            return PolicyRuleDecision(
                False, "ineligible", "none", 0.0, (), (condition.reason,)
            )
        else:
            reasons.append(condition.reason + " 충족")
    if missing:
        return PolicyRuleDecision(
            True, "needs_input", "unknown", 0.0,
            tuple(dict.fromkeys(missing)), tuple(reasons),
        )

    tier_missing: list[str] = []
    for tier in rule.tiers:
        matches = True
        current_missing: list[str] = []
        for condition in tier.conditions:
            actual = _condition_value(condition, request, rule.income_reference_year)
            if actual is None:
                current_missing.append(condition.field)
                matches = False
            elif not _matches(condition, actual):
                matches = False
        if matches:
            return PolicyRuleDecision(
                True, "confirmed", tier.name, tier.support_rate, (),
                (*reasons, f"{tier.name} 조건 충족"),
            )
        tier_missing.extend(current_missing)
    return PolicyRuleDecision(
        True, "needs_input" if tier_missing else "needs_verification", "unknown", 0.0,
        tuple(dict.fromkeys(tier_missing)), tuple(reasons),
    )


class PolicyRuleCatalog:
    def __init__(self, rules: list[PolicyRule]):
        self.rules = {
            key: rule
            for rule in rules
            if rule.status == "verified"
            for key in (rule.policy_id, rule.policy_name)
        }

    @classmethod
    def from_directory(cls, directory: Path) -> "PolicyRuleCatalog":
        rules = [
            PolicyRule.from_mapping(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*.json"))
        ]
        return cls(rules)

    def apply(self, policy: PolicyBenefit, request: RoadmapRequest) -> PolicyBenefit:
        rule = self.rules.get(policy.policy_id) or self.rules.get(policy.name)
        if rule is None:
            return policy
        decision = evaluate_policy_rule(rule, request)
        reason = "; ".join((*decision.reasons, policy.reason))
        return replace(
            policy,
            eligible=decision.eligible,
            estimated_support=round(
                min(request.monthly_budget, policy.monthly_limit)
                * policy.maturity_months * decision.support_rate
            ),
            reason=reason,
            support_rate=decision.support_rate,
            preferential_support_rate=None,
            qualification_status=decision.qualification_status,
            benefit_tier=decision.benefit_tier,
            missing_qualification_fields=decision.missing_fields,
        )
