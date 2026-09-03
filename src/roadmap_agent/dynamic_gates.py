from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_TERM_STOPWORDS = {
    "뭐야", "무엇", "의미", "정의", "설명", "궁금", "알려줘", "이란", "라는", "인가요",
    # 아래는 "참여기업"처럼 이 정책에서만 쓰는 고유 용어가 아니라, 여러 정책의
    # 배제조건 문구에 흔히 같이 등장하는 일반 친족·법률 용어다(민법상 정의가
    # 이미 잘 알려져 있음). 이 단어들까지 게이트 문구와 대조하면 "직계존비속이
    # 뭐야?" 같은 일반 상식 질문에도 이 정책의 배제조건 문구를 답으로 잘못
    # 보여주게 된다(실사용자 피드백으로 발견) — 참여기업처럼 정책 고유
    # 조어에만 이 폴백이 걸리도록 제외한다.
    "배우자", "직계존비속", "직계존속", "직계비속", "형제자매", "사업주", "대표자",
}
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

    question은 이제 이중부정 없는 긍정형 직접 질문("~인가요?")으로 쓴다
    (예전엔 "아니오"가 항상 탈락을 뜻하도록 배제조건을 부정 의문문으로
    뒤집어 만들게 했는데, "~아니신가요?"에 "아니요"로 답하는 식의 이중부정이
    실사용자에게 헷갈린다는 피드백으로 폐기). 그래서 어느 답이 탈락인지를
    문장 방향만으로 추론할 수 없어 disqualify_on_yes로 명시한다.

    yes_label/no_label은 프론트가 예/아니오 선택지에 "예/아니요"만 보여주는
    대신 그 질문의 주어까지 포함한 완전한 문장으로 보여주기 위한 것 —
    예: question="배우자가 사업주인가요?"의 yes_label은
    "예, 배우자가 사업주입니다." 같은 형태. 문장 변형은 조사 처리가
    까다로워 자동 생성하지 않고 추출 시점에 LLM이 같이 만든다(검수 대상).
    """

    policy_id: str
    gate_id: str
    question: str
    hint: str
    policy_name: str = ""
    # True면 "예" 답변이 탈락 사유, False(기본값)면 "아니요" 답변이 탈락
    # 사유다. 기본값 False는 이 필드가 없던 예전 검수 데이터(모두 부정
    # 의문문 방식)와 호환된다.
    disqualify_on_yes: bool = False
    yes_label: str = ""
    no_label: str = ""


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
                    disqualify_on_yes=bool(item.get("disqualify_on_yes", False)),
                    yes_label=str(item.get("yes_label") or ""),
                    no_label=str(item.get("no_label") or ""),
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
