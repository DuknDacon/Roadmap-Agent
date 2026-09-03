from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from . import conversation as conversation_module
from . import domain as domain_module


DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30일 — 해커톤 제출 기간 동안 실사용 여부 확인용(2026-09-03)

# 대화 그래프 상태에 실리는 우리 쪽 데이터클래스·Enum들. LangGraph의 msgpack
# 직렬화기는 기본적으로 모르는 타입을 관대하게 저장해주지만, 이후 버전에서는
# 명시적으로 허용 목록에 없으면 역직렬화를 막을 예정이라 미리 등록해 둔다.
_ALLOWED_CHECKPOINT_TYPES = (
    domain_module.RiskProfile,
    domain_module.RoadmapRequest,
    domain_module.Evidence,
    domain_module.Scenario,
    domain_module.RoadmapResult,
    conversation_module.ConversationStatus,
    conversation_module.ConversationIntent,
    conversation_module.ConversationResponse,
)


class SqliteConversationStore:
    """LangGraph 대화 체크포인트를 SQLite 파일에 저장하고, 마지막 활동 후 TTL이 지난
    스레드를 정리한다.

    서버 프로세스가 재시작돼도(개발 중 --reload 포함) 대화 상태가 파일에 남아 있고,
    스레드ID(사용자 세션)마다 독립적으로 시각을 추적해 만료되므로 동시 접속자끼리
    서로 영향을 주지 않는다. 사용자 입력을 영구 보관하지 않기 위해 일정 시간
    활동이 없으면 해당 스레드의 대화 상태를 완전히 삭제한다.

    세션 테이블은 체크포인터가 이미 열어 둔 연결(`checkpointer.conn`)과 그 락을
    그대로 재사용한다 — 같은 SQLite 파일에 별도 연결을 또 열면 파일 잠금이
    충돌할 수 있기 때문이다.
    """

    def __init__(self, path: str | Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        # `.with_msgpack_allowlist(...)`는 이미 제한된 허용목록에 항목을 더할 때만
        # 동작하고, 기본값인 permissive(True) 상태에서는 그대로 반환해 버리므로
        # 허용목록을 생성자에서 바로 지정한다.
        serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_CHECKPOINT_TYPES)
        self.checkpointer = SqliteSaver(conn, serde=serde)
        self.checkpointer.setup()
        self._conn = self.checkpointer.conn
        self._lock = self.checkpointer.lock
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS conversation_sessions "
                "(thread_id TEXT PRIMARY KEY, last_active_at REAL NOT NULL)"
            )
            self._conn.commit()

    def touch(self, thread_id: str, *, now: float | None = None) -> None:
        """대화 턴이 끝날 때마다 호출한다. 활동 시각을 갱신하고 만료된 세션을 정리한다."""
        current = time.time() if now is None else now
        cutoff = current - self.ttl_seconds
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_sessions (thread_id, last_active_at) "
                "VALUES (?, ?) ON CONFLICT(thread_id) DO UPDATE SET "
                "last_active_at = excluded.last_active_at",
                (thread_id, current),
            )
            self._conn.commit()
            expired = [
                row[0]
                for row in self._conn.execute(
                    "SELECT thread_id FROM conversation_sessions "
                    "WHERE last_active_at < ?",
                    (cutoff,),
                )
            ]
        # checkpointer.delete_thread()가 같은 락을 다시 잡으므로(재진입 불가) 락 밖에서 호출한다.
        for expired_id in expired:
            self.checkpointer.delete_thread(expired_id)
        if expired:
            with self._lock:
                self._conn.executemany(
                    "DELETE FROM conversation_sessions WHERE thread_id = ?",
                    [(expired_id,) for expired_id in expired],
                )
                self._conn.commit()

    def active_thread_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT thread_id FROM conversation_sessions"
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        self._conn.close()
