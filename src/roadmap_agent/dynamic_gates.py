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

# RAG 금융 코퍼스도, 게이트 힌트 폴백도 답을 못 주는 일반 친족·법률·행정
# 용어. 실제로 "직계존비속이 뭐야?"를 물으면 RAG는 관련 없는 금융상품
# 안내문만 찾아오고, 그 뒤 웹검색도 인용 허용 도메인(law.go.kr 등 8개 —
# gemini.py 참고)에 안 걸려 "확정할 수 없습니다"로 떨어지는 문제가
# 있었다(실사용자 피드백). 이 용어들은 매번 검색을 거치지 않고 여기서
# 바로 확정된 문장으로 답한다 — SeedUp 프론트의 용어사전(glossary.ts)과
# 같은 내용을 유지한다.
GENERAL_TERM_DEFINITIONS: dict[str, str] = {
    "직계존비속": "본인을 기준으로 부모·조부모(직계존속)와 자녀·손자녀(직계비속)를 함께 이르는 말입니다.",
    "형제자매": "같은 부모에게서 태어난 형제와 자매를 함께 이르는 말입니다.",
    "사업주": "회사나 사업체를 실제로 운영하며 대표하는 사람입니다. 법인 등기상 대표이사, 개인사업자 명의자 등이 해당됩니다.",
    "세대분리": "부모와 다른 세대로 주민등록을 분리해 등록하는 것을 말합니다. 청년 대상 정책은 부모와 세대가 분리돼 있어야 지원 대상이 되는 경우가 많습니다.",
    "전대차": "임차인이 자신이 빌린 집이나 방을 다시 다른 사람에게 빌려주는 계약입니다.",
    "공공임대주택": "정부·지자체·LH 등 공공기관이 짓거나 매입해 시세보다 저렴하게 빌려주는 주택입니다.",
    "예술활동증명": "문화체육관광부가 예술 활동 이력을 심사해 발급하는 예술인 자격 증명입니다.",
    "구직등록": "워크넷 등 고용센터에 구직 의사를 등록하는 절차입니다.",
    "고용보험": "근로자가 실직했을 때 급여 일부를 지원받거나 직업훈련을 받을 수 있게 해주는 사회보험입니다.",
    "중도이탈": "정책 사업이나 프로그램 참여를 끝까지 마치지 않고 중간에 그만두는 것을 말합니다.",
}
# "~이 뭐야?"/"~란?" 류 정의를 묻는 신호 문구. _TERM_STOPWORDS 앞부분과
# 겹치지만(둘 다 "질문 방식"을 나타내는 단어라 자연스러운 중복), 여긴
# "정의를 확정해 답해도 되는 turn인지" 판단이 목적이라 별도로 둔다.
_DEFINITION_CUE_WORDS = ("뭐야", "무엇", "의미", "정의", "설명", "궁금", "이란", "라는", "인가요")


def _eun_neun(word: str) -> str:
    """word 마지막 글자에 받침이 있으면 "은", 없으면 "는"을 돌려준다."""
    if not word:
        return "은"
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        return "은" if (code - 0xAC00) % 28 != 0 else "는"
    return "은"


def general_term_definition(message: str) -> str | None:
    """GENERAL_TERM_DEFINITIONS에 있는 용어의 정의를 묻는 질문이면 바로
    답한다. RAG/웹검색을 아예 타지 않아 이런 질문에 관련 없는 근거를
    가져오거나 웹검색 인용 도메인 필터에 걸려 "확정할 수 없다"고 답하는
    것을 막는다."""
    if not any(cue in message for cue in _DEFINITION_CUE_WORDS):
        return None
    for term, definition in GENERAL_TERM_DEFINITIONS.items():
        if term in message:
            return f"{term}{_eun_neun(term)} {definition}"
    return None


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
