from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_TERM_STOPWORDS = {"뭐야", "무엇", "의미", "정의", "설명", "궁금", "알려줘", "이란", "라는", "인가요"}
_TRAILING_PARTICLE = re.compile(r"(이|가|은|는|을|를|도|만|의|와|과)$")


def _candidate_terms(message: str) -> list[str]:
    """메시지에서 게이트 문구와 대조해볼 만한 명사 후보를 뽑는다.

    형태소 분석 없이 조사 하나만 뗀 단순 규칙이라 정확하지 않을 수 있지만,
    이미 RAG 검색이 실패한 뒤의 보조 후보 탐색이라 다소 느슨해도 된다.
    """
    tokens = re.findall(r"[가-힣]{2,}", message)
    terms = set()
    for token in tokens:
        if token in _TERM_STOPWORDS:
            continue
        stripped = _TRAILING_PARTICLE.sub("", token)
        if len(stripped) >= 2 and stripped not in _TERM_STOPWORDS:
            terms.add(stripped)
    return sorted(terms)


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
    policy_name: str = ""


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
            policy_name = str(payload.get("policy_name") or "")
            gates = [
                DynamicGate(
                    policy_id=policy_id,
                    gate_id=str(item["gate_id"]),
                    question=str(item["question"]),
                    hint=str(item.get("hint") or ""),
                    policy_name=policy_name,
                )
                for item in payload.get("gates", [])
            ]
            if gates:
                gates_by_policy[policy_id] = gates
        return cls(gates_by_policy)

    def gates_for(self, policy_id: str) -> list[DynamicGate]:
        return self._gates_by_policy.get(policy_id, [])

    def find_gates_mentioning(self, message: str) -> list[DynamicGate]:
        """일반 금융 RAG 코퍼스에 없는 개별 정책 고유 용어(예: "참여기업")를

        게이트 질문/힌트 문구에서 찾아본다. 법령상의 공식 정의가 아니라 해당
        정책의 자격조건 문구일 뿐이므로 RAG 검색이 실패했을 때의 참고용
        보조 수단으로만 쓴다.
        """
        terms = _candidate_terms(message)
        if not terms:
            return []
        matches: list[DynamicGate] = []
        for gates in self._gates_by_policy.values():
            for gate in gates:
                haystack = gate.question + " " + gate.hint
                if any(term in haystack for term in terms):
                    matches.append(gate)
        return matches

    @staticmethod
    def composite_id(policy_id: str, gate_id: str) -> str:
        return f"{policy_id}:{gate_id}"
