from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RoadmapCreateRequest(ApiModel):
    birth_date: date = Field(alias="birthDate")
    previous_annual_income: int | None = Field(
        default=None, alias="previousAnnualIncome", ge=0
    )
    current_annual_income: int = Field(alias="currentAnnualIncome", ge=0)
    region: str = Field(min_length=1)
    region_province_code: str = Field(alias="regionProvinceCode", min_length=2)
    region_district_code: str = Field(alias="regionDistrictCode", min_length=5)
    household_size: int = Field(alias="householdSize", ge=1)
    marital_status: Literal["single", "married"] = Field(alias="maritalStatus")
    employed: bool
    employment_type: str | None = Field(default=None, alias="employmentType")
    is_sme_employee: bool | None = Field(default=None, alias="isSmeEmployee")
    financial_income_taxed: bool | None = Field(default=None, alias="financialIncomeTaxed")
    household_monthly_income: int | None = Field(
        default=None, alias="householdMonthlyIncome", ge=0
    )
    monthly_take_home: int | None = Field(default=None, alias="monthlyTakeHome", gt=0)
    monthly_budget: int = Field(alias="monthlyBudget", gt=0)
    target_date: date = Field(alias="targetDate")
    target_amount: int | None = Field(default=None, alias="targetAmount", gt=0)
    has_emergency_fund: bool = Field(alias="hasEmergencyFund")
    risk_level: Literal["stable", "balanced", "growth"] | None = Field(
        default=None, alias="riskLevel"
    )
    investment_cap: int | None = Field(
        default=None, alias="investmentCap", ge=0, le=100
    )
    question: str = Field(default="", max_length=1000)
    thread_id: UUID | None = Field(default=None, alias="threadId")
    # "policy_id:gate_id" 합성 키 → 예/아니오. DynamicGateRegistry가 발견한,
    # 4개 하드코딩 필드를 넘어서는 자격조건에 대한 사용자 답변.
    dynamic_gate_answers: dict[str, bool] = Field(
        default_factory=dict, alias="dynamicGateAnswers"
    )

    @field_validator("target_date")
    @classmethod
    def target_must_be_future(cls, value: date) -> date:
        if value <= date.today():
            raise ValueError("목표 시점은 오늘 이후여야 합니다.")
        return value


class AllocationItem(ApiModel):
    label: str
    amount: int
    color: str


class EvidenceItem(ApiModel):
    title: str
    organization: str
    url: str


class ScenarioResponse(ApiModel):
    id: str
    badge: str
    title: str
    product_type: str = Field(alias="productType")
    monthly_amount: int = Field(alias="monthlyAmount")
    expected_amount: int = Field(alias="expectedAmount")
    principal: int
    goal_rate: float | None = Field(alias="goalRate")
    shortfall: int | None
    allocations: list[AllocationItem]
    highlights: list[str]
    warnings: list[str]
    evidence: list[EvidenceItem]
    monthly_limit: int | None = Field(default=None, alias="monthlyLimit")


class MissingFieldDetail(ApiModel):
    """profile_ask 로 렌더할 필드 하나의 질문 메타데이터.

    레거시 4개 필드와 동적 게이트(합성 키 "policy_id:gate_id") 모두 이 형태로
    통일해서 내려준다 — 라우터가 필드명마다 로컬 딕셔너리를 미리 등록해둘
    필요 없이 그대로 렌더할 수 있게 하기 위함(동적 게이트는 상품마다 달라
    라우터에 미리 등록해둘 수 없다).
    """

    field: str
    question: str
    hint: str | None = None
    input_type: str = Field(default="boolean", alias="inputType")


class RoadmapResponse(ApiModel):
    recommended: ScenarioResponse | None = None
    alternative: ScenarioResponse | None = None
    alternatives: list[ScenarioResponse] = Field(default_factory=list)
    summary: str
    explanation: str | None = None
    recommended_reason: str | None = Field(default=None, alias="recommendedReason")
    alternative_reason: str | None = Field(default=None, alias="alternativeReason")
    chat_reply: str | None = Field(default=None, alias="chatReply")
    notice: str
    generated_at: datetime = Field(alias="generatedAt")
    conversation_status: str | None = Field(default=None, alias="conversationStatus")
    conversation_intent: str | None = Field(default=None, alias="conversationIntent")
    request_patch: "RoadmapRequestPatch | None" = Field(default=None, alias="requestPatch")
    # 로드맵 생성 전 DB 매칭 후보에 사용자 입력만으로는 판정 못 하는 필드가
    # 있으면(예: financial_income_taxed) 로드맵 없이 이 값만 채워 반환한다.
    missing_fields: list[str] = Field(default_factory=list, alias="missingFields")
    # missing_fields와 같은 필드를 가리키지만 라우터/프론트가 바로 렌더할 수 있는
    # 구조화된 질문 메타데이터(질문 문구·힌트·입력 타입)를 담는다. missing_fields는
    # 하위호환을 위해 그대로 둔다.
    missing_field_details: list[MissingFieldDetail] = Field(
        default_factory=list, alias="missingFieldDetails"
    )


class RoadmapRequestPatch(ApiModel):
    monthly_budget: int = Field(alias="monthlyBudget")
    target_date: date = Field(alias="targetDate")
    target_amount: int | None = Field(alias="targetAmount")
    has_emergency_fund: bool = Field(alias="hasEmergencyFund")
    investment_cap: int | None = Field(alias="investmentCap")


class ApiError(ApiModel):
    code: str
    message: str
    detail: object | None = None
