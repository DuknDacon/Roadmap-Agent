from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DynamicGate:
    """LLM이 상품 원문에서 발견한, 4개 하드코딩 필드를 넘어서는 예/아니오 자격조건.

    LLM은 이 질문(question)만 만든다 — eligible 판정은 항상
    repositories.py의 결정론적 코드가 request.dynamic_gate_answers 를
    보고 계산한다.
    """

    policy_id: str
    gate_id: str
    question: str
    hint: str


class DynamicGateRegistry:
    """data/policy_gates/*.json 에서 사람이 검수 완료(status=="verified")한
    게이트만 로드한다.

    검수 안 된("extracted") 게이트는 로드 시점에 걸러진다 — 실행 시점
    가드(policy_rules.py의 evaluate_policy_rule 같은)보다 안전하다.
    """

    def __init__(self, gates_by_policy: dict[str, list[DynamicGate]]):
        self._gates_by_policy = gates_by_policy

    @classmethod
    def from_directory(cls, directory: Path) -> "DynamicGateRegistry":
        if not directory.exists():
            return cls({})
        gates_by_policy: dict[str, list[DynamicGate]] = {}
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "verified":
                continue
            policy_id = str(payload["policy_id"])
            gates = [
                DynamicGate(
                    policy_id=policy_id,
                    gate_id=str(item["gate_id"]),
                    question=str(item["question"]),
                    hint=str(item.get("hint") or ""),
                )
                for item in payload.get("gates", [])
            ]
            if gates:
                gates_by_policy[policy_id] = gates
        return cls(gates_by_policy)

    def gates_for(self, policy_id: str) -> list[DynamicGate]:
        return self._gates_by_policy.get(policy_id, [])

    @staticmethod
    def composite_id(policy_id: str, gate_id: str) -> str:
        return f"{policy_id}:{gate_id}"
