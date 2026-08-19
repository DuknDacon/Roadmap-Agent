from dataclasses import replace

from roadmap_agent.conversation_graph import RoadmapConversationGraph
from roadmap_agent.conversation_store import SqliteConversationStore
from roadmap_agent.domain import RiskProfile, RoadmapRequest, RoadmapResult, Scenario


class EmptyPolicies:
    def find_candidates(self, request):
        return []


class EmptySavings:
    def find_candidates(self, request):
        return []


class EmptyRetriever:
    def search(self, query, limit=3):
        return []


def _request():
    return RoadmapRequest(800_000, 36, None, RiskProfile.CONSERVATIVE)


def _result():
    recommended = Scenario(
        "policy", "청년미래적금", {"policy": 500_000, "cash": 300_000},
        0, 0, 0, 100, 28_800_000, ["정부 기여금을 반영했습니다."], [],
        monthly_limit=500_000,
    )
    alternative = Scenario(
        "savings", "[적금] 한국스탠다드차타드은행 퍼스트가계적금",
        {"savings": 800_000}, 0, 0, 0, 100, 28_800_000,
        ["공시 금리를 반영했습니다."], [], monthly_limit=None,
    )
    return RoadmapResult(recommended, [alternative], {}, "참고용")


def _graph():
    return RoadmapConversationGraph(
        policy_repository=EmptyPolicies(),
        savings_repository=EmptySavings(),
        retriever=EmptyRetriever(),
    )


def test_thread_keeps_product_and_topic_for_short_followup():
    graph = _graph()
    first = graph.invoke(
        "thread-a", _request(), _result(), "퍼스트가계적금은 납입 한도 없어?"
    )
    second = graph.invoke("thread-a", first.request, first.result, "왜?")

    assert "퍼스트가계적금" in first.reply
    assert "퍼스트가계적금" in second.reply
    assert "최대 월 납입액이 제공되지 않았기 때문" in second.reply
    assert "청년미래적금" not in second.reply


def test_threads_do_not_share_conversation_context():
    graph = _graph()
    graph.invoke("thread-a", _request(), _result(), "퍼스트가계적금은 납입 한도 없어?")
    response = graph.invoke("thread-b", _request(), _result(), "왜?")

    assert "청년미래적금" in response.reply


def test_thread_keeps_recalculated_request():
    graph = _graph()
    first = graph.invoke("thread-a", _request(), _result(), "위험을 더 줄여줘")
    second_request = replace(first.request, question="")
    second = graph.invoke("thread-a", second_request, first.result, "현재 투자비중은?")

    assert first.request.max_investment_ratio == 0.1
    assert second.request.max_investment_ratio == 0.1


def test_sqlite_backed_thread_survives_a_fresh_graph_instance(tmp_path):
    store = SqliteConversationStore(tmp_path / "conversations.sqlite", ttl_seconds=1800)
    try:
        graph = RoadmapConversationGraph(
            policy_repository=EmptyPolicies(),
            savings_repository=EmptySavings(),
            retriever=EmptyRetriever(),
            checkpointer=store.checkpointer,
            session_store=store,
        )
        graph.invoke(
            "thread-a", _request(), _result(), "퍼스트가계적금은 납입 한도 없어?", now=1_000.0
        )

        # 서버 재시작을 흉내 내어 완전히 새로운 그래프 인스턴스를 같은 파일로 연다.
        restarted = RoadmapConversationGraph(
            policy_repository=EmptyPolicies(),
            savings_repository=EmptySavings(),
            retriever=EmptyRetriever(),
            checkpointer=store.checkpointer,
            session_store=store,
        )
        second = restarted.invoke(
            "thread-a", _request(), _result(), "왜?", now=1_010.0
        )
        assert "퍼스트가계적금" in second.reply
    finally:
        store.close()


def test_idle_thread_beyond_ttl_loses_context_after_touch(tmp_path):
    store = SqliteConversationStore(tmp_path / "conversations.sqlite", ttl_seconds=60)
    try:
        graph = RoadmapConversationGraph(
            policy_repository=EmptyPolicies(),
            savings_repository=EmptySavings(),
            retriever=EmptyRetriever(),
            checkpointer=store.checkpointer,
            session_store=store,
        )
        graph.invoke(
            "thread-a", _request(), _result(), "퍼스트가계적금은 납입 한도 없어?", now=1_000.0
        )
        # 다른 사용자의 활동이 스윕을 유발해도, 아직 살아있는 스레드는 영향받지 않는다.
        graph.invoke("thread-b", _request(), _result(), "왜?", now=1_030.0)
        assert set(store.active_thread_ids()) == {"thread-a", "thread-b"}

        # thread-a가 TTL을 넘겨 방치된 뒤에는 다른 요청의 정리 스윕에서 삭제된다.
        graph.invoke("thread-c", _request(), _result(), "왜?", now=1_200.0)
        assert "thread-a" not in store.active_thread_ids()

        after_expiry = graph.invoke(
            "thread-a", _request(), _result(), "왜?", now=1_200.0
        )
        assert "청년미래적금" in after_expiry.reply
    finally:
        store.close()
