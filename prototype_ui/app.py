from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
RESULT_LOGIC_VERSION = 6
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadmap_agent.calculators import investment_cap as effective_investment_cap
from roadmap_agent.config import load_env_file
from roadmap_agent.domain import RiskProfile, RoadmapRequest, Scenario
from roadmap_agent.gemini import GeminiEmbeddingClient, GeminiRoadmapExplainer
from roadmap_agent.intake import IntakeRequest, RiskAnswers
from roadmap_agent.orchestrator import run_roadmap
from roadmap_agent.repositories import (
    PostgresPolicyRepository,
    PostgresSavingsProductRepository,
    postgres_connection_factory_from_env,
)
from roadmap_agent.region_codes import load_region_codes
from roadmap_agent.retrieval import (
    FallbackRagRetriever,
    LocalRagRetriever,
    PostgresVectorRagRetriever,
)
from roadmap_agent.ui_state import apply_conversation_change


st.set_page_config(page_title="SeedUp Roadmap Agent", page_icon="🌱", layout="wide")
st.markdown(
    """
    <style>
    .block-container {max-width: 1480px; padding-top: 2rem;}
    [data-testid="stMetric"] {background:#f7faf8; border:1px solid #dfe9e2;
      border-radius:16px; padding:14px;}
    .scenario-card {border:1px solid #dfe9e2; border-radius:18px; padding:20px;
      background:white; margin:10px 0 18px 0;}
    .eyebrow {color:#18864b; font-weight:700; font-size:.85rem; letter-spacing:.04em;}
    .muted {color:#68736c; font-size:.92rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def won(value: int | None) -> str:
    return "-" if value is None else f"{value:,}원"


DATA_STATUS_LABELS = {
    "mock_rate_until_finlife_connected": "실제 상품 데이터 없이 가정 금리를 사용했습니다",
    "structured_finlife_candidate": "금융감독원 실제 적금상품 정보를 사용했습니다",
    "education_range_not_forecast": "교육용 투자 시나리오이며 수익 예측값이 아닙니다",
    "structured_policy_candidate": "구조화된 정책상품 정보를 사용했습니다",
    "mock_savings_rate_and_education_investment_range": (
        "가정 적금금리와 교육용 투자 시나리오를 사용했습니다"
    ),
    "structured_finlife_and_education_investment_range": (
        "실제 적금상품 정보와 교육용 투자 시나리오를 사용했습니다"
    ),
}

ALLOCATION_LABELS = {
    "savings": "예·적금",
    "cash_equivalent": "현금성 자산",
    "diversified_investment": "분산투자",
    "unallocated_cash": "미배분 금액",
}


def user_data_status(value: str) -> str:
    return DATA_STATUS_LABELS.get(value, "확인된 데이터와 계산 기준을 사용했습니다")


def user_allocation_label(value: str) -> str:
    return ALLOCATION_LABELS.get(value, value)


def scenario_view(
    scenario: Scenario,
    *,
    primary: bool,
    compact: bool = False,
) -> None:
    comparison_amount = (
        scenario.expected_max
        if scenario.kind in {"savings", "policy"}
        else scenario.expected_base
    )
    label = "최우선 추천" if primary else "대안 시나리오"
    st.markdown(
        f'<div class="scenario-card"><div class="eyebrow">{label}</div>'
        f'<h3>{scenario.title}</h3><div class="muted">{user_data_status(scenario.data_status)}</div></div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2 if compact else 4)
    cols[0].metric("월 배분", won(sum(scenario.monthly_allocation.values())))
    cols[1].metric("추천 비교 예상액", won(comparison_amount))
    cols[0 if compact else 2].metric(
        "목표 달성률",
        "-" if scenario.goal_achievement_rate is None else f"{scenario.goal_achievement_rate:.1f}%",
    )
    cols[1 if compact else 3].metric("부족액", won(scenario.shortfall))

    allocation = {
        user_allocation_label(name): value
        for name, value in scenario.monthly_allocation.items()
        if value > 0
    }
    if allocation:
        st.caption("월 배분 구성")
        allocation_rows = [
            {"구성": name, "금액": value} for name, value in allocation.items()
        ]
        st.vega_lite_chart(
            {
                "data": {"values": allocation_rows},
                "mark": {"type": "arc", "innerRadius": 55},
                "encoding": {
                    "theta": {"field": "금액", "type": "quantitative", "stack": True},
                    "color": {
                        "field": "구성",
                        "type": "nominal",
                        "legend": {"title": None, "orient": "bottom"},
                    },
                    "tooltip": [
                        {"field": "구성", "type": "nominal", "title": "배분"},
                        {"field": "금액", "type": "quantitative", "title": "월 금액", "format": ","},
                    ],
                },
                "view": {"stroke": None},
                "height": 280,
            },
            use_container_width=True,
        )
        total_allocation = sum(allocation.values())
        st.caption(
            " · ".join(
                f"{name} {value:,}원 ({value / total_allocation:.0%})"
                for name, value in allocation.items()
            )
        )
    with st.expander("계산 범위와 추천 이유", expanded=primary):
        if scenario.kind == "policy":
            calculation_range = {
                "원금": won(scenario.principal),
                "일반형 예상액": won(scenario.expected_min),
                "우대형 예상액": won(scenario.expected_max),
                "추천 비교액": won(comparison_amount),
                "필요 월납입액": won(scenario.required_monthly_budget),
            }
        elif scenario.kind == "savings":
            calculation_range = {
                "원금": won(scenario.principal),
                "기본금리 예상액": won(scenario.expected_base),
                "최고금리 예상액": won(scenario.expected_max),
                "추천 비교액": won(comparison_amount),
                "필요 월납입액": won(scenario.required_monthly_budget),
            }
        else:
            calculation_range = {
                "원금": won(scenario.principal),
                "하락 시나리오": won(scenario.expected_min),
                "기준 시나리오": won(scenario.expected_base),
                "상승 시나리오": won(scenario.expected_max),
                "필요 월납입액": won(scenario.required_monthly_budget),
            }
        st.write(calculation_range)
        for item in scenario.rationale:
            st.markdown(f"- {item}")
        for warning in scenario.warnings:
            st.warning(warning)
    with st.expander("공식 근거"):
        if not scenario.evidence:
            st.caption("연결된 근거가 없습니다.")
        for evidence in scenario.evidence:
            st.markdown(f"**{evidence.title}** · 관련도 {evidence.score}")
            if evidence.source_url:
                st.link_button("공식 출처 열기", evidence.source_url)


def run_agent(request: RoadmapRequest, use_vector: bool, use_gemini: bool):
    load_env_file(ROOT / ".env")
    warnings: list[str] = []
    ai_status: list[str] = []
    policy_repository = None
    savings_repository = None
    retriever = None
    explainer = None
    factory = None
    try:
        factory = postgres_connection_factory_from_env()
        policy_repository = PostgresPolicyRepository(factory)
        savings_repository = PostgresSavingsProductRepository(factory)
    except Exception as exc:
        warnings.append(f"PostgreSQL 연결 없이 폴백 계산을 사용합니다: {type(exc).__name__}")

    if use_vector and factory is not None:
        try:
            local = LocalRagRetriever(ROOT / "data" / "rag")
            retriever = FallbackRagRetriever(
                PostgresVectorRagRetriever(factory, GeminiEmbeddingClient()), local
            )
        except Exception as exc:
            warnings.append(f"벡터 검색 준비 실패로 로컬 검색을 사용합니다: {type(exc).__name__}")
    elif use_vector:
        warnings.append("AI 의미 검색을 요청했지만 PostgreSQL 연결이 없어 로컬 검색을 사용합니다.")
    if use_gemini:
        try:
            explainer = GeminiRoadmapExplainer()
        except Exception as exc:
            warnings.append(f"Gemini 설명 없이 계산 결과만 표시합니다: {type(exc).__name__}")

    result = run_roadmap(
        request,
        policy_repository=policy_repository,
        savings_repository=savings_repository,
        retriever=retriever,
        explainer=explainer,
    )
    if use_vector:
        if isinstance(retriever, FallbackRagRetriever) and retriever.primary_used:
            ai_status.append("AI 의미 기반 문서 검색 적용")
        else:
            reason = (
                retriever.fallback_reason
                if isinstance(retriever, FallbackRagRetriever)
                else "벡터 검색 준비 실패"
            )
            ai_status.append(f"AI 의미 검색 미적용({reason}) · 로컬 키워드 검색 사용")
    else:
        ai_status.append("로컬 키워드 문서 검색")
    if use_gemini and result.explanation:
        ai_status.append("AI 맞춤 결과 설명 생성 완료")
    elif use_gemini:
        llm_status = str(result.assumptions.get("llm_status", "호출 준비 실패"))
        ai_status.append(f"AI 맞춤 설명 미적용({llm_status}) · 기본 설명 사용")
    else:
        ai_status.append("기본 계산 설명")
    return result, warnings, ai_status


st.title("🌱 SeedUp Roadmap Agent")
st.caption("기능 2 흐름 검증용 1차 UI · 현재는 로컬 샘플 데이터가 포함될 수 있습니다.")

with st.sidebar:
    st.header("분석 설정")
    use_vector = st.toggle("AI 의미 기반 문서 검색", value=False)
    use_gemini = st.toggle("AI 맞춤 결과 설명", value=False)
    st.caption("켜면 Gemini API를 사용합니다. 기본 기능 확인 중에는 꺼두면 비용이 들지 않습니다.")

    st.divider()
    st.subheader("기본 필수")
    birth_date = st.date_input("생년월일", value=date(1998, 1, 1), max_value=date.today())
    annual_income_manwon = st.number_input(
        "연소득(만원)", min_value=0, value=4_000, step=100,
        help="예: 연소득 4,000만 원이면 4000을 입력하세요.",
    )
    household_raw = st.number_input(
        "가구원 수",
        min_value=1,
        value=1,
        step=1,
        help="본인을 포함해 생계나 주거를 함께하는 가구원 수를 입력하세요.",
    )
    married_raw = st.radio(
        "혼인 여부",
        [False, True],
        format_func=lambda value: "기혼" if value else "미혼",
        horizontal=True,
    )
    region_file = ROOT / "data" / "reference" / "region_codes.json"
    regions = load_region_codes(region_file)
    province_option = st.selectbox(
        "시·도",
        regions,
        index=None,
        placeholder="시·도 이름을 입력해 검색하세요",
        format_func=lambda item: item.name,
    )
    district_option = None
    if province_option is not None:
        district_option = st.selectbox(
            "시·군·구",
            province_option.districts,
            index=None,
            placeholder="시·군·구 이름을 입력해 검색하세요",
            format_func=lambda item: item.name,
        )
    else:
        st.selectbox(
            "시·군·구",
            [],
            index=None,
            placeholder="시·도를 먼저 선택하세요",
            disabled=True,
        )
    is_employed = st.radio("현재 재직 중인가요?", [True, False], format_func=lambda x: "예" if x else "아니오", horizontal=True)

    employment_type = None
    is_sme = None
    if is_employed:
        employment_type = st.selectbox("고용형태", ["employee", "contract", "freelancer", "self_employed"])
        is_sme = st.radio("중소기업 재직인가요?", [True, False], format_func=lambda x: "예" if x else "아니오", horizontal=True)

    monthly_budget = st.number_input("필수지출 제외 월 투입액(원)", min_value=10_000, value=800_000, step=10_000)
    budget_confirmed = st.checkbox("필수지출을 제외하고 지속 가능한 금액입니다.")
    target_date = st.date_input("목표 시점", value=date(2029, 8, 1), min_value=date.today())
    emergency = st.radio("비상자금을 보유하고 있나요?", [True, False], format_func=lambda x: "예" if x else "아니오", horizontal=True)

    st.subheader("투자 성향")
    st.caption("투자금에 손실이 생겼을 때 감당할 수 있는 범위를 알려주세요.")

    loss_tolerance_labels = {
        1: "1 · 손실이 싫어요",
        2: "2 · 보통이에요",
        3: "3 · 손실을 감내할 수 있어요",
    }
    loss_tolerance = st.select_slider(
        "손실 감내 수준",
        options=[1, 2, 3],
        value=2,
        format_func=lambda value: loss_tolerance_labels[value],
        help="기대수익을 위해 투자금의 일시적인 손실을 어느 정도 감당할 수 있는지 선택하세요.",
    )

    loss_response_labels = {
        1: "1 · 바로 회수할래요",
        2: "2 · 상황을 지켜볼래요",
        3: "3 · 장기적으로 유지할래요",
    }
    loss_response = st.select_slider(
        "손실 발생 시 대응",
        options=[1, 2, 3],
        value=2,
        format_func=lambda value: loss_response_labels[value],
        help="실제로 투자금에 손실이 발생했을 때 가장 가까운 행동을 선택하세요.",
    )

    investment_cap_percent = st.slider(
        "매달 모으는 돈 중 투자상품에 넣어도 되는 최대 비율",
        0,
        100,
        30,
        5,
        format="%d%%",
        help="나머지 금액은 예·적금 등 원금 변동이 적은 방법에 배분합니다.",
    )
    investment_cap = investment_cap_percent / 100

    with st.expander("선택·정밀 매칭 입력"):
        target_amount_raw = st.number_input(
            "목표금액(선택)",
            min_value=0,
            value=0,
            step=1_000_000,
            help="아직 목표금액을 정하지 않았다면 0원으로 두세요.",
        )
        monthly_take_home_manwon = st.number_input(
            "월 실수령액(만원, 선택)",
            min_value=0,
            value=0,
            step=10,
            help="대략적인 금액만 입력해도 됩니다. 입력하지 않으려면 0으로 두세요.",
        )
        dependents_raw = st.number_input("부양가족 수(선택)", min_value=0, value=0)

    analyze = st.button("로드맵 만들기", type="primary", use_container_width=True)

if analyze:
    if province_option is None or district_option is None:
        st.error("시·도와 시·군·구를 모두 선택해 주세요.")
        st.stop()
    intake = IntakeRequest(
        birth_date=birth_date,
        annual_income=annual_income_manwon * 10_000,
        monthly_take_home=(monthly_take_home_manwon * 10_000) or None,
        region_province_code=province_option.code,
        region_district_code=district_option.code,
        employment_type=employment_type,
        is_employed=is_employed,
        is_sme_employee=is_sme,
        household_size=household_raw,
        dependents=dependents_raw,
        is_married=married_raw,
        monthly_budget=monthly_budget,
        target_year=target_date.year,
        target_month=target_date.month,
        target_amount=target_amount_raw or None,
        risk_answers=RiskAnswers(loss_tolerance, loss_response, investment_cap),
        budget_after_essentials_confirmed=budget_confirmed,
        has_emergency_fund=emergency,
    )
    try:
        request = intake.normalize(date.today())
        result, runtime_warnings, ai_status = run_agent(request, use_vector, use_gemini)
        st.session_state.request = request
        st.session_state.result = result
        st.session_state.runtime_warnings = runtime_warnings
        st.session_state.ai_status = ai_status
        st.session_state.messages = []
        st.session_state.last_changes = []
        st.session_state.result_logic_version = RESULT_LOGIC_VERSION
    except Exception as exc:
        st.error(str(exc))

if "result" not in st.session_state:
    st.info("왼쪽에서 필수 조건을 입력하고 ‘로드맵 만들기’를 눌러 주세요.")
    st.stop()

current_request = st.session_state.request
current_scenarios = [
    st.session_state.result.recommended,
    *st.session_state.result.alternatives,
]
stale_logic = (
    st.session_state.get("result_logic_version") != RESULT_LOGIC_VERSION
)
invalid_zero_investment_result = (
    effective_investment_cap(
        current_request.risk_profile,
        current_request.horizon_months,
        current_request.max_investment_ratio,
    )
    == 0
    and any(item.kind in {"investment", "balanced"} for item in current_scenarios)
)
if stale_logic or invalid_zero_investment_result:
    repaired_result, runtime_warnings, ai_status = run_agent(
        current_request, use_vector, use_gemini
    )
    st.session_state.result = repaired_result
    st.session_state.runtime_warnings = runtime_warnings
    st.session_state.ai_status = ai_status
    st.session_state.result_logic_version = RESULT_LOGIC_VERSION
    st.rerun()

st.subheader("현재 추천 로드맵")
if st.session_state.get("ai_status"):
    st.info("실행 모드: " + " · ".join(st.session_state.ai_status))
for warning in st.session_state.get("runtime_warnings", []):
    st.warning(warning)
if st.session_state.get("last_changes"):
    st.success("최신 반영 조건: " + " · ".join(st.session_state.last_changes))

result = st.session_state.result
if result.alternatives:
    recommended_column, alternative_column = st.columns(2, gap="large")
    with recommended_column:
        scenario_view(
            result.recommended,
            primary=True,
            compact=True,
        )
    with alternative_column:
        scenario_view(result.alternatives[0], primary=False, compact=True)
else:
    scenario_view(
        result.recommended,
        primary=True,
    )
st.caption(result.disclaimer)

st.divider()
st.subheader("Roadmap Agent와 대화하기")
st.caption(
    "현재는 월 투입액·투자 비중·목표금액·목표시점·비상자금 조건 변경을 지원합니다."
)
with st.container(border=True):
    with st.container(height=300, border=False):
        messages = st.session_state.get("messages", [])
        if not messages:
            with st.chat_message("assistant"):
                if result.explanation:
                    st.markdown("**AI 맞춤 설명**")
                    st.markdown(result.explanation)
                st.write(
                    "위의 첫 로드맵을 만들었어요. 조건을 바꾸고 싶다면 "
                    "‘투자 비중을 0%로 해줘’처럼 말씀해 주세요."
                )
        for message in messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

    prompt = st.chat_input("로드맵에서 변경할 조건을 입력하세요")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        previous_title = st.session_state.result.recommended.title
        updated, descriptions = apply_conversation_change(st.session_state.request, prompt)
        updated_result, runtime_warnings, ai_status = run_agent(
            updated, use_vector, use_gemini
        )
        st.session_state.request = updated
        st.session_state.result = updated_result
        st.session_state.runtime_warnings = runtime_warnings
        st.session_state.ai_status = ai_status
        st.session_state.last_changes = descriptions
        st.session_state.result_logic_version = RESULT_LOGIC_VERSION
        response = " · ".join(descriptions) + "을 반영해 전체 로드맵을 다시 계산했습니다."
        if previous_title != updated_result.recommended.title:
            response += (
                f" 최우선 추천이 ‘{previous_title}’에서 "
                f"‘{updated_result.recommended.title}’으로 변경됐습니다."
            )
        if updated_result.explanation:
            response += "\n\n**AI 맞춤 설명**\n\n" + updated_result.explanation
        st.session_state.messages.append({"role": "assistant", "content": response})
        st.rerun()
    except Exception as exc:
        st.session_state.messages.append({"role": "assistant", "content": str(exc)})
        st.rerun()
