from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

import roadmap_agent
from roadmap_agent.config import load_env_file
from roadmap_agent.conversation_store import DEFAULT_TTL_SECONDS, SqliteConversationStore
from roadmap_agent.gemini import (
    GeminiConversationPlanner,
    GeminiEmbeddingClient,
    GeminiRoadmapExplainer,
)
from roadmap_agent.ports import (
    PolicyRepository,
    RagRetriever,
    RoadmapExplainer,
    SavingsProductRepository,
)
from roadmap_agent.repositories import (
    SqlitePolicyRepository,
    SqliteSavingsProductRepository,
    sqlite_connection_factory_from_env,
)
from roadmap_agent.retrieval import FallbackRagRetriever, HybridRagRetriever, LocalRagRetriever


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_server_environment() -> Path:
    agent_root = Path(roadmap_agent.__file__).resolve().parents[2]
    load_env_file(agent_root / ".env")
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    return agent_root


@dataclass(frozen=True)
class Runtime:
    policy_repository: PolicyRepository | None = None
    savings_repository: SavingsProductRepository | None = None
    retriever: RagRetriever | None = None
    explainer: RoadmapExplainer | None = None
    planner: object | None = None
    conversation_store: SqliteConversationStore | None = None


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    agent_root = _load_server_environment()
    policies = None
    savings = None
    retriever = None
    explainer = None
    planner = None

    shared_db_path = os.getenv("SHARED_DB_PATH")
    if shared_db_path:
        factory = sqlite_connection_factory_from_env()
        policies = SqlitePolicyRepository(factory)
        savings = SqliteSavingsProductRepository(factory)

    if _enabled("ENABLE_RAG") or _enabled("ENABLE_VECTOR_RAG"):
        rag_root = agent_root / "data" / "rag"
        local_retriever = LocalRagRetriever(rag_root)
        index_dir = Path(os.getenv("RAG_INDEX_DIR", agent_root / "data" / "rag_index"))
        try:
            retriever = FallbackRagRetriever(
                HybridRagRetriever(index_dir, GeminiEmbeddingClient()),
                local_retriever,
            )
        except (FileNotFoundError, RuntimeError):
            retriever = local_retriever

    if _enabled("ENABLE_GEMINI"):
        explainer = GeminiRoadmapExplainer(
            web_search_enabled=os.getenv(
                "ENABLE_GEMINI_WEB_SEARCH", "true"
            ).strip().lower() in {"1", "true", "yes", "on"},
            web_search_monthly_limit=int(
                os.getenv("GEMINI_WEB_SEARCH_MONTHLY_LIMIT", "1000")
            ),
        )

    # 대화 원문은 별도 동의 플래그가 있을 때만 외부 계획기로 전달한다.
    if _enabled("ENABLE_GEMINI_PLANNER"):
        planner = GeminiConversationPlanner()

    default_store_path = shared_db_path or (
        Path(__file__).resolve().parent / ".data" / "conversations.sqlite"
    )
    store_path = os.getenv("CONVERSATION_STORE_PATH", str(default_store_path))
    ttl_seconds = int(os.getenv("CONVERSATION_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    conversation_store = SqliteConversationStore(store_path, ttl_seconds=ttl_seconds)

    return Runtime(
        policy_repository=policies,
        savings_repository=savings,
        retriever=retriever,
        explainer=explainer,
        planner=planner,
        conversation_store=conversation_store,
    )
