from roadmap_agent.conversation_store import SqliteConversationStore


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def test_touch_keeps_thread_active_within_ttl(tmp_path):
    store = SqliteConversationStore(tmp_path / "conversations.sqlite", ttl_seconds=1800)
    try:
        store.touch("thread-a", now=1_000.0)
        store.touch("thread-a", now=1_500.0)
        assert store.active_thread_ids() == ["thread-a"]
    finally:
        store.close()


def test_expired_thread_is_pruned_from_sessions_and_checkpoints(tmp_path):
    store = SqliteConversationStore(tmp_path / "conversations.sqlite", ttl_seconds=1800)
    try:
        checkpoint = {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        store.checkpointer.put(_config("old-thread"), checkpoint, {}, {})
        store.touch("old-thread", now=1_000.0)
        assert store.checkpointer.get_tuple(_config("old-thread")) is not None

        store.touch("new-thread", now=1_000.0 + store.ttl_seconds + 1)

        assert store.active_thread_ids() == ["new-thread"]
        assert store.checkpointer.get_tuple(_config("old-thread")) is None
    finally:
        store.close()


def test_active_thread_touched_again_survives_prune_sweep(tmp_path):
    store = SqliteConversationStore(tmp_path / "conversations.sqlite", ttl_seconds=100)
    try:
        store.touch("thread-a", now=1_000.0)
        store.touch("thread-a", now=1_050.0)
        store.touch("thread-b", now=1_060.0)

        assert set(store.active_thread_ids()) == {"thread-a", "thread-b"}
    finally:
        store.close()
