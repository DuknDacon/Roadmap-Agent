from __future__ import annotations

import threading
from operator import add
from typing import Annotated, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .conversation import ConversationResponse, execute_conversation
from .conversation_store import SqliteConversationStore
from .domain import RoadmapRequest, RoadmapResult
from .orchestrator import run_roadmap
from .ports import PolicyRepository, RagRetriever, RoadmapExplainer, SavingsProductRepository


class ConversationGraphState(TypedDict, total=False):
    request: RoadmapRequest
    result: RoadmapResult
    message: str
    messages: Annotated[list[str], add]
    response: ConversationResponse


class RoadmapConversationGraph:
    """Thread-scoped conversation graph around the deterministic roadmap tools."""

    def __init__(
        self,
        *,
        policy_repository: PolicyRepository,
        savings_repository: SavingsProductRepository,
        retriever: RagRetriever,
        explainer: RoadmapExplainer | None = None,
        planner=None,
        gate_registry=None,
        checkpointer: BaseCheckpointSaver | None = None,
        session_store: SqliteConversationStore | None = None,
    ) -> None:
        self.policy_repository = policy_repository
        self.savings_repository = savings_repository
        self.retriever = retriever
        self.explainer = explainer
        self.planner = planner
        self.gate_registry = gate_registry
        self.session_store = session_store
        # SQLite 체크포인터는 스레드 동시 접근에 안전하지 않으므로 대화 실행을 직렬화한다.
        self._invoke_lock = threading.Lock()

        graph = StateGraph(ConversationGraphState)
        graph.add_node("agent", self._agent_node)
        graph.add_edge(START, "agent")
        graph.add_edge("agent", END)
        self.app = graph.compile(checkpointer=checkpointer or MemorySaver())

    def _agent_node(self, state: ConversationGraphState) -> dict:
        history = state.get("messages", [])
        message = state["message"]
        context_messages = history[:-1] if history and history[-1] == message else history
        context = " ".join(context_messages[-10:])
        response = execute_conversation(
            state["request"],
            state["result"],
            message,
            run_roadmap_fn=run_roadmap,
            policy_repository=self.policy_repository,
            savings_repository=self.savings_repository,
            retriever=self.retriever,
            explainer=self.explainer,
            planner=self.planner,
            gate_registry=self.gate_registry,
            context=context,
        )
        return {
            "request": response.request,
            "result": response.result,
            "response": response,
            "messages": [response.reply],
        }

    def invoke(
        self,
        thread_id: str,
        request: RoadmapRequest,
        result: RoadmapResult,
        message: str,
        *,
        now: float | None = None,
    ) -> ConversationResponse:
        with self._invoke_lock:
            state = self.app.invoke(
                {
                    "request": request,
                    "result": result,
                    "message": message,
                    "messages": [message],
                },
                config={"configurable": {"thread_id": thread_id}},
            )
            if self.session_store is not None:
                self.session_store.touch(thread_id, now=now)
        return state["response"]

