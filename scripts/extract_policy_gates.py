from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roadmap_agent.config import load_env_file
from roadmap_agent.repositories import sqlite_connection_factory

# 자격조건 관련 필드만 — 신청방법(plcyAplyMthdCn)·제출서류(sbmsnDcmntCn) 같은
# "어떻게 신청하는지" 필드는 뺀다. 포함하면 자격과 무관한 문구 변경에도 불필요한
# 재추출이 발생하고, 가짜 게이트("서류를 준비하셨나요?")가 생길 위험이 있다.
# bizPrdEtcCn(사업기간 기타사항, 실측 샘플 "상시" 등)은 자격조건이 아니라 사업
# 시행기간 부가설명이라 뺐다 — 처음에 넣었더니 실제 추출 대상이 26건이 아니라
# 100건까지 뻥튀기됨을 실제 DB로 확인하고 바로잡음(2026-09-01).
YOUTH_GATE_FIELDS = ("addAplyQlfcCndCn", "ptcpPrpTrgtCn", "earnEtcCn")
WELFARE_GATE_FIELDS = ("slctCritCn", "tgtrDtlCn")


def _gate_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    return "\n".join(str(row.get(name) or "") for name in fields)


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def collect_candidates(connection_factory) -> list[dict[str, Any]]:
    """youth_policy(활성) + welfare_service 전체에서 자격조건 관련 텍스트를 뽑는다."""
    candidates: list[dict[str, Any]] = []
    with connection_factory() as connection:
        for row in connection.execute(
            'SELECT * FROM youth_policy WHERE "plcyAprvSttsCd" = ?', ("0044002",)
        ).fetchall():
            row = dict(row)
            candidates.append(
                {
                    "policy_id": str(row.get("plcyNo") or ""),
                    "policy_name": str(row.get("plcyNm") or ""),
                    "source_table": "youth_policy",
                    "text": _gate_text(row, YOUTH_GATE_FIELDS),
                }
            )
        for row in connection.execute("SELECT * FROM welfare_service").fetchall():
            row = dict(row)
            candidates.append(
                {
                    "policy_id": str(row.get("servId") or ""),
                    "policy_name": str(row.get("servNm") or ""),
                    "source_table": "welfare_service",
                    "text": _gate_text(row, WELFARE_GATE_FIELDS),
                }
            )
    return [c for c in candidates if c["policy_id"]]


def extract_gates(
    candidates: list[dict[str, Any]],
    *,
    gates_dir: Path,
    extractor: Any | None,
    dry_run: bool = False,
) -> dict[str, int]:
    gates_dir.mkdir(parents=True, exist_ok=True)
    existing_ids = {path.stem for path in gates_dir.glob("*.json")}
    current_ids: set[str] = set()
    stats = {
        "skipped_empty": 0,
        "skipped_unchanged": 0,
        "extracted": 0,
        "failed": 0,
        "pruned": 0,
    }

    for candidate in candidates:
        policy_id = candidate["policy_id"]
        text = candidate["text"].strip()
        if not text:
            stats["skipped_empty"] += 1
            continue

        current_ids.add(policy_id)
        content_hash = _content_hash(candidate["text"])
        path = gates_dir / f"{policy_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("content_hash") == content_hash:
                stats["skipped_unchanged"] += 1
                continue

        if dry_run:
            stats["extracted"] += 1
            print(f"[dry-run] 추출 대상: {policy_id} {candidate['policy_name']}")
            continue

        # 상품 하나의 LLM 응답이 깨져도(예: JSON 파싱 실패) 배치 전체가 멈추지
        # 않게 한다 — 실패한 건은 파일을 안 남겨서, 다음 재실행 때 해시가 그대로
        # 안 맞으니 자동으로 다시 시도된다.
        try:
            gates = extractor.extract(
                policy_id=policy_id,
                policy_name=candidate["policy_name"],
                document=candidate["text"],
            )
        except Exception as exc:  # noqa: BLE001 — 배치 진행이 개별 실패보다 우선
            stats["failed"] += 1
            print(f"추출 실패(다음 실행에 재시도됨): {policy_id} {candidate['policy_name']} — {exc}")
            continue

        stats["extracted"] += 1
        if not gates:
            if path.exists():
                path.unlink()
            print(f"게이트 없음: {policy_id} {candidate['policy_name']}")
            continue
        payload = {
            "policy_id": policy_id,
            "policy_name": candidate["policy_name"],
            "source_table": candidate["source_table"],
            "content_hash": content_hash,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_model": extractor.model,
            "status": "extracted",
            "approved_by": None,
            "gates": gates,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"추출됨(검수 필요): {policy_id} {candidate['policy_name']} — 게이트 {len(gates)}건")

    # orphan 정리: DB에 더 이상 없거나(상품 삭제) 자격조건 텍스트가 비어버린
    # policy_id의 캐시 파일을 지운다 — 성필님의 DB 전체 교체 드롭에도 대응한다.
    for stale_id in existing_ids - current_ids:
        stale_path = gates_dir / f"{stale_id}.json"
        if stale_path.exists():
            if not dry_run:
                stale_path.unlink()
            stats["pruned"] += 1
            print(f"orphan 정리: {stale_id}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="정책 상품 원문에서 4개 하드코딩 필드를 넘어서는 예/아니오 자격조건을 추출한다"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--gates-dir", type=Path, default=Path("data/policy_gates"))
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="LLM 호출 없이 스킵/추출 대상만 표시"
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    db_path = args.db_path or os.environ.get("SHARED_DB_PATH")
    if not db_path:
        raise SystemExit("--db-path 또는 SHARED_DB_PATH 환경변수가 필요합니다.")

    connection_factory = sqlite_connection_factory(db_path)
    candidates = collect_candidates(connection_factory)

    extractor = None
    if not args.dry_run:
        from google import genai

        from roadmap_agent.gemini import GeminiPolicyGateExtractor

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("GEMINI_API_KEY 환경변수가 필요합니다.")
        client = genai.Client(api_key=api_key)
        extractor = GeminiPolicyGateExtractor(client=client)

    stats = extract_gates(candidates, gates_dir=args.gates_dir, extractor=extractor, dry_run=args.dry_run)
    print(
        f"대상 {len(candidates)}건 — 빈 텍스트 {stats['skipped_empty']}건, "
        f"변경없음(스킵) {stats['skipped_unchanged']}건, 추출 {stats['extracted']}건, "
        f"실패(재시도 필요) {stats['failed']}건, 정리(orphan) {stats['pruned']}건"
    )
    print('추출된 파일은 status="extracted" 상태입니다 — git diff로 검토 후 verified로 승격하세요.')
    if stats["failed"]:
        print(f"{stats['failed']}건은 실패해서 파일이 안 만들어졌습니다 — 같은 명령을 다시 실행하면 그 건들만 재시도됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
