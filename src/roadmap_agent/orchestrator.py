from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any, TypedDict

from .agents import (
    balanced_agent,
    investment_agent,
    policy_agent,
    policy_candidate_scenarios,
    savings_agent,
    savings_candidate_scenarios,
)
from .calculators import investment_cap
from .domain import RoadmapRequest, RoadmapResult, Scenario
from .ports import (
    EmptyPolicyRepository,
    EmptySavingsProductRepository,
    PolicyRepository,
    RagRetriever,
    RoadmapExplainer,
    SavingsProductRepository,
)
from .retrieval import LocalRagRetriever


class RoadmapState(TypedDict, total=False):
    request: RoadmapRequest
    savings: Scenario
    investment: Scenario
    policy: Scenario | None
    balanced: Scenario
    result: RoadmapResult


def _default_rag_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "rag"


WEIGHTS = {
    "conservative": {"goal": 0.30, "stability": 0.35, "liquidity": 0.20, "return": 0.10, "policy": 0.05},
    "balanced": {"goal": 0.35, "stability": 0.25, "liquidity": 0.15, "return": 0.20, "policy": 0.05},
    "aggressive": {"goal": 0.35, "stability": 0.10, "liquidity": 0.10, "return": 0.35, "policy": 0.10},
}


def _safe_external_error(exc: Exception) -> str:
    status = str(getattr(exc, "status", "") or "").strip()
    message = str(getattr(exc, "message", "") or "").strip()
    message = re.sub(r"(?i)(api[_ -]?key|key)=?[^\s,]+", r"\1=[숨김]", message)
    message = " ".join(message.split())[:180]
    details = ":".join(value for value in (type(exc).__name__, status) if value)
    return f"fallback:{details}" + (f" · {message}" if message else "")


def score_scenario(scenario: Scenario, request: RoadmapRequest) -> float:
    weights = WEIGHTS[request.risk_profile.value]
    comparison_amount = (
        scenario.expected_max
        if scenario.kind in {"savings", "policy"}
        else scenario.expected_base
    )
    goal_rate = (
        comparison_amount / request.target_amount * 100
        if request.target_amount
        else 100
    )
    goal = min(goal_rate, 100)
    stability = {"savings": 95, "policy": 90, "balanced": 70, "investment": 35}[scenario.kind]
    # 정책상품의 가입 상태는 별도 경고로 다룬다. 상품성 점수에서는
    # 같은 목표기간의 적금보다 유동성이 과도하게 낮게 평가되지 않도록 한다.
    liquidity = {"investment": 80, "savings": 70, "balanced": 60, "policy": 45}[scenario.kind]
    gain_ratio = max(comparison_amount - scenario.principal, 0) / max(scenario.principal, 1)
    expected_return = min(gain_ratio * 500, 100)
    policy = 100 if scenario.kind == "policy" else 0
    if not request.has_emergency_fund and scenario.kind == "investment":
        stability = 0
    return round(
        goal * weights["goal"]
        + stability * weights["stability"]
        + liquidity * weights["liquidity"]
        + expected_return * weights["return"]
        + policy * weights["policy"],
        2,
    )


def finalize(request: RoadmapRequest, scenarios: list[Scenario]) -> RoadmapResult:
    if (
        investment_cap(
            request.risk_profile,
            request.horizon_months,
            request.max_investment_ratio,
        )
        == 0
    ):
        scenarios = [
            scenario
            for scenario in scenarios
            if scenario.kind not in {"investment", "balanced"}
        ]
    scored = [replace(scenario, score=score_scenario(scenario, request)) for scenario in scenarios]
    if request.target_amount:
        achieved = [
            item
            for item in scored
            if (
                item.expected_max
                if item.kind in {"savings", "policy"}
                else item.expected_base
            )
            >= request.target_amount
        ]
        candidates = achieved or scored
    else:
        candidates = scored
    ordered = sorted(candidates, key=lambda scenario: (-(scenario.score or 0), scenario.kind))
    remaining = [item for item in sorted(scored, key=lambda x: -(x.score or 0)) if item != ordered[0]]
    structured = any(item.data_status.startswith("structured_") for item in scenarios)
    return RoadmapResult(
        recommended=ordered[0],
        alternatives=remaining[:1],
        assumptions={
            "savings_calculation": (
                "structured_product_rate_and_tax"
                if structured
                else "fallback_annual_rate_3_percent"
            ),
            "savings_payment_timing": "month_end",
            "investment_values_are_scenarios_not_forecasts": True,
            "conditional_product_benefits_compared_at_maximum": True,
            "structured_product_data_connected": structured,
        },
        disclaimer=(
            "본 결과는 자산 형성 교육 및 비교를 위한 참고자료이며 수익을 보장하거나 "
            "특정 금융상품의 가입·매수를 권유하지 않습니다. 실제 가입 전 최신 약관과 공식 출처를 확인하세요."
        ),
    )


def run_roadmap(
    request: RoadmapRequest,
    rag_root: Path | None = None,
    policy_repository: PolicyRepository | None = None,
    savings_repository: SavingsProductRepository | None = None,
    retriever: RagRetriever | None = None,
    explainer: RoadmapExplainer | None = None,
) -> RoadmapResult:
    request.validate()
    active_retriever = retriever or LocalRagRetriever(rag_root or _default_rag_root())
    policies = policy_repository or EmptyPolicyRepository()
    savings_products = savings_repository or EmptySavingsProductRepository()
    scenarios = savings_candidate_scenarios(
        request, active_retriever, savings_products, limit=3
    )
    scenarios.extend(
        policy_candidate_scenarios(
            request, active_retriever, policies, limit=3
        )
    )
    effective_investment_ratio = investment_cap(
        request.risk_profile,
        request.horizon_months,
        request.max_investment_ratio,
    )
    if effective_investment_ratio > 0:
        scenarios.extend(
            [
                investment_agent(request, active_retriever),
                balanced_agent(request, active_retriever, savings_products),
            ]
        )
    result = finalize(request, scenarios)
    if explainer is not None:
        try:
            explanation = explainer.explain(request, result)
            result = replace(
                result,
                recommended_reason=explanation.recommended_reason,
                alternative_reason=explanation.alternative_reason,
                chat_reply=explanation.chat_reply,
            )
        except Exception as exc:
            result = replace(
                result,
                assumptions={
                    **result.assumptions,
                    "llm_status": _safe_external_error(exc),
                },
            )
    return result


def run_conversation(
    request: RoadmapRequest,
    result: RoadmapResult,
    message: str,
    *,
    rag_root: Path | None = None,
    policy_repository: PolicyRepository | None = None,
    savings_repository: SavingsProductRepository | None = None,
    retriever: RagRetriever | None = None,
    explainer: RoadmapExplainer | None = None,
    planner=None,
    context: str = "",
):
    """기존 결과를 기준으로 요청에 필요한 도구만 실행하는 대화 진입점."""
    from .conversation import execute_conversation

    active_retriever = retriever or LocalRagRetriever(rag_root or _default_rag_root())
    policies = policy_repository or EmptyPolicyRepository()
    savings = savings_repository or EmptySavingsProductRepository()
    return execute_conversation(
        request,
        result,
        message,
        run_roadmap_fn=run_roadmap,
        policy_repository=policies,
        savings_repository=savings,
        retriever=active_retriever,
        explainer=explainer,
        planner=planner,
        context=context,
    )


def build_conversation_graph(
    rag_root: Path | None = None,
    policy_repository: PolicyRepository | None = None,
    savings_repository: SavingsProductRepository | None = None,
    retriever: RagRetriever | None = None,
    explainer: RoadmapExplainer | None = None,
    planner=None,
    checkpointer=None,
    session_store=None,
):
    """Thread 체크포인터를 사용하는 기능 2 대화 그래프를 생성한다.

    `checkpointer`/`session_store`를 지정하지 않으면 프로세스 메모리에만 남는
    `MemorySaver`를 사용한다. 서버 재시작에도 대화 상태를 유지하고 일정 시간 뒤
    자동으로 정리하려면 `SqliteConversationStore`를 만들어 `checkpointer`와
    `session_store`에 각각 전달한다.
    """
    from .conversation_graph import RoadmapConversationGraph

    return RoadmapConversationGraph(
        policy_repository=policy_repository or EmptyPolicyRepository(),
        savings_repository=savings_repository or EmptySavingsProductRepository(),
        retriever=retriever or LocalRagRetriever(rag_root or _default_rag_root()),
        explainer=explainer,
        planner=planner,
        checkpointer=checkpointer,
        session_store=session_store,
    )


def build_langgraph(
    rag_root: Path | None = None,
    policy_repository: PolicyRepository | None = None,
    savings_repository: SavingsProductRepository | None = None,
) -> Any:
    """LangGraph 설치 환경에서 사용하는 병렬화 전 초안 그래프."""
    from langgraph.graph import END, START, StateGraph

    retriever = LocalRagRetriever(rag_root or _default_rag_root())
    policies = policy_repository or EmptyPolicyRepository()
    savings_products = savings_repository or EmptySavingsProductRepository()
    graph = StateGraph(RoadmapState)
    graph.add_node(
        "savings",
        lambda s: {"savings": savings_agent(s["request"], retriever, savings_products)},
    )
    graph.add_node("investment", lambda s: {"investment": investment_agent(s["request"], retriever)})
    graph.add_node("policy", lambda s: {"policy": policy_agent(s["request"], retriever, policies)})
    graph.add_node(
        "balanced",
        lambda s: {"balanced": balanced_agent(s["request"], retriever, savings_products)},
    )

    # 초안은 순차 실행한다. 노드 입출력이 안정되면 fan-out/fan-in으로 병렬화한다.
    graph.add_edge(START, "savings")
    graph.add_edge("savings", "investment")
    graph.add_edge("investment", "policy")
    graph.add_edge("policy", "balanced")

    def decide(state: RoadmapState) -> RoadmapState:
        scenarios = [state[name] for name in ("savings", "investment", "balanced")]
        if (
            investment_cap(
                state["request"].risk_profile,
                state["request"].horizon_months,
                state["request"].max_investment_ratio,
            )
            == 0
        ):
            scenarios = [state["savings"]]
        if state.get("policy") is not None:
            scenarios.append(state["policy"])
        return {"result": finalize(state["request"], scenarios)}

    graph.add_node("decide", decide)
    graph.add_edge("balanced", "decide")
    graph.add_edge("decide", END)
    return graph.compile()
