from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .domain import Evidence, RoadmapRequest, RoadmapResult


@dataclass(frozen=True)
class RoadmapExplanation:
    recommended_reason: str
    alternative_reason: str
    chat_reply: str | None = None


@dataclass(frozen=True)
class SavingsProduct:
    product_id: str
    company_name: str
    product_name: str
    annual_base_rate: float
    annual_preferential_rate: float
    maximum_monthly_payment: int | None
    term_months: int
    tax_rate: float
    source_url: str
    effective_date: str
    savings_type_name: str = ""
    interest_type_name: str = ""


@dataclass(frozen=True)
class PolicyBenefit:
    policy_id: str
    name: str
    eligible: bool
    monthly_limit: int
    estimated_support: int
    maturity_months: int
    source_url: str
    effective_date: str
    reason: str
    application_open: bool | None = None
    support_rate: float | None = None
    preferential_support_rate: float | None = None
    qualification_status: str = "confirmed"
    benefit_tier: str = "standard"
    missing_qualification_fields: tuple[str, ...] = ()


class SavingsProductRepository(Protocol):
    def find_candidates(self, request: RoadmapRequest) -> list[SavingsProduct]: ...


class PolicyRepository(Protocol):
    def find_candidates(self, request: RoadmapRequest) -> list[PolicyBenefit]: ...


class RagRetriever(Protocol):
    def search(self, query: str, limit: int = 3) -> list[Evidence]: ...


class RoadmapExplainer(Protocol):
    def explain(self, request: RoadmapRequest, result: RoadmapResult) -> RoadmapExplanation: ...


class FinancialQaAnswerer(Protocol):
    def answer_financial_question(self, question: str, evidence: list[Evidence]) -> str: ...


class EmptySavingsProductRepository:
    def find_candidates(self, request: RoadmapRequest) -> list[SavingsProduct]:
        return []


class EmptyPolicyRepository:
    def find_candidates(self, request: RoadmapRequest) -> list[PolicyBenefit]:
        return []
