#!/usr/bin/env python3
"""SQLite에 저장된 대화(threadId) 체크포인트를 시간순으로 복원해서 보여준다.

기능 2 사용자·검증 시나리오 문서의 Given/When/Then과 실제 동작을 대조할 때 쓴다.
LangGraph는 한 턴(invoke 1회)마다 체크포인트를 여러 개(START 상태 1개 + 처리
결과 상태 여러 개) 남기므로, 같은 메시지가 연속으로 이어지는 구간은 마지막
상태(그 턴의 최종 처리 결과)만 남기고 정리해서 보여준다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from roadmap_agent.conversation_store import SqliteConversationStore


DEFAULT_STORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "SeedUp" / "backend" / "app" / ".data" / "conversations.sqlite"
)


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def turn_history(store: SqliteConversationStore, thread_id: str) -> list[dict]:
    """스레드의 체크포인트를 시간순으로 훑어, 메시지별 최종 처리 결과만 남긴다."""
    tuples = list(store.checkpointer.list(_thread_config(thread_id)))
    ordered = list(reversed(tuples))  # list()는 최신순이라 시간순으로 뒤집는다

    turns: list[dict] = []
    last_message = object()  # 첫 항목이 무조건 새 턴으로 시작하도록 하는 sentinel
    for tup in ordered:
        values = tup.checkpoint.get("channel_values", {})
        message = values.get("message")
        if message is None:
            continue
        response = values.get("response")
        turn = {
            "message": message,
            "intent": response.intent.value if response else None,
            "status": response.status.value if response else None,
            "reply": response.reply if response else None,
        }
        if message == last_message and turns:
            turns[-1] = turn  # 같은 메시지의 마지막 체크포인트로 덮어써 최종 결과만 남긴다
        else:
            turns.append(turn)
        last_message = message
    return turns


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite 대화 저장소의 체크포인트를 시간순으로 복원해 보여준다."
    )
    parser.add_argument(
        "--store-path", type=Path, default=DEFAULT_STORE_PATH,
        help="SqliteConversationStore가 쓰는 DB 파일 경로",
    )
    parser.add_argument(
        "--thread-id", help="특정 스레드만 볼 때 지정. 생략하면 활성 스레드 목록만 보여준다"
    )
    parser.add_argument(
        "--max-reply-chars", type=int, default=150, help="답변 미리보기 길이 제한"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.store_path.is_file():
        print(f"파일이 없습니다: {args.store_path}")
        return 1

    store = SqliteConversationStore(args.store_path)
    try:
        if not args.thread_id:
            thread_ids = store.active_thread_ids()
            if not thread_ids:
                print("활성 스레드가 없습니다(TTL 만료로 정리됐을 수 있습니다).")
                return 0
            print(f"활성 스레드 {len(thread_ids)}개:")
            for thread_id in thread_ids:
                turns = turn_history(store, thread_id)
                print(f"  {thread_id}  ({len(turns)}턴)")
            print("\n--thread-id <값> 으로 특정 스레드의 전체 대화를 볼 수 있습니다.")
            return 0

        turns = turn_history(store, args.thread_id)
        if not turns:
            print("해당 스레드에 체크포인트가 없습니다.")
            return 0
        for index, turn in enumerate(turns, 1):
            reply = turn["reply"]
            if reply and len(reply) > args.max_reply_chars:
                reply = reply[: args.max_reply_chars] + "..."
            print(f"{index:2d}. [{turn['intent']}/{turn['status']}] 사용자: {turn['message']!r}")
            print(f"    답변: {reply!r}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
