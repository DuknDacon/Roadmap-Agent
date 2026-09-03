from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from dataclasses import asdict
from typing import Any

from .domain import Evidence, RoadmapRequest, RoadmapResult
from .ports import RoadmapExplanation
from .policy_rules import PolicyRule
from .retrieval import source_priority


def _financial_subquestions(question: str) -> list[str]:
    if re.search(r"이직|퇴직|재직", question) and re.search(r"적금|정책", question):
        return [
            "가입 시 확정된 비과세 자격이 변경되는가?",
            "정부기여금 비율이 변경되는가?",
            "이미 받은 정부기여금이 환수되는가?",
            "계좌 유지 또는 중도해지 조건이 변경되는가?",
        ]
    if re.search(r"중도해지|해지", question):
        return ["해지 가능 여부는?", "세금·지원금 환수는?", "예외 사유는?"]
    return [question]


class GeminiEmbeddingClient:
    """Gemini 임베딩 호출을 한곳에 격리한다."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model or os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
        self.dimensions = dimensions or int(os.environ.get("GEMINI_EMBEDDING_DIMENSION", "1536"))
        if self.dimensions != 1536:
            raise ValueError("현재 기능 2 FAISS 인덱스는 1536차원만 지원합니다.")
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not texts:
            return []
        from google.genai import types

        response = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions,
            ),
        )
        vectors = [list(item.values or []) for item in (response.embeddings or [])]
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError("Gemini 임베딩 응답의 개수 또는 차원이 올바르지 않습니다.")
        return vectors


class GeminiPolicyRuleExtractor:
    """공식 문서를 검토 대기 정책 규칙으로 구조화한다."""

    def __init__(self, *, client: Any, model: str | None = None) -> None:
        self.client = client
        self.model = model or os.environ.get("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")

    def extract(
        self,
        *,
        policy_id: str,
        policy_name: str,
        document: str,
        source_url: str,
    ) -> PolicyRule:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(
                {
                    "policy_id": policy_id,
                    "policy_name": policy_name,
                    "source_url": source_url,
                    "document": document,
                },
                ensure_ascii=False,
            ),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "공식 정책 문서에서 계산 가능한 자격 규칙만 JSON으로 추출한다. "
                    "status는 extracted로 둔다. eligibility와 각 tiers.conditions의 field는 "
                    "age, previous_annual_income, financial_income_taxed, is_sme_employee, "
                    "household_median_income_ratio만 허용한다. operator는 eq, lte, gte만 사용한다. "
                    "지원율은 0~1 소수로 쓴다. 문서에 없는 값은 추측하지 말고 조건에서 제외한다. "
                    "sources에는 HTTPS URL, 문서 제목, 섹션, 짧은 근거 문장을 넣는다. "
                    "키는 policy_id, policy_name, effective_year, income_reference_year, status, "
                    "eligibility, tiers, sources다."
                ),
                response_mime_type="application/json",
                max_output_tokens=1800,
            ),
        )
        payload = json.loads((response.text or "").strip())
        payload["policy_id"] = policy_id
        payload["policy_name"] = policy_name
        payload["status"] = "extracted"
        for source in payload.get("sources", []):
            source["url"] = source_url
        return PolicyRule.from_mapping(payload)


class GeminiPolicyGateExtractor:
    """상품 원문에서 4개 하드코딩 필드(financial_income_taxed 등)를 넘어서는
    예/아니오 자격조건("게이트")을 찾는다. 여기서 만드는 건 질문(question)뿐이고,
    이 게이트에 대한 사용자 답변으로 eligible을 계산하는 건 항상
    repositories.py의 결정론적 코드다 — 여기 결과가 곧바로 자격판정에 쓰이지
    않는다(scripts/extract_policy_gates.py가 만든 파일은 사람이 검수해
    status를 verified로 올려야만 DynamicGateRegistry에 실제로 로드된다).
    """

    def __init__(self, *, client: Any, model: str | None = None) -> None:
        self.client = client
        self.model = model or os.environ.get("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")

    def extract(self, *, policy_id: str, policy_name: str, document: str) -> list[dict[str, str]]:
        from google.genai import types

        if not document.strip():
            return []
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(
                {"policy_id": policy_id, "policy_name": policy_name, "document": document},
                ensure_ascii=False,
            ),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "주어진 정책 원문에서, 나이/지역/소득처럼 이미 계산되는 조건 말고 신청자가 "
                    "예/아니오로만 답할 수 있는 **추가** 자격조건(자격증, 인증, 신분, 소속 등)만 "
                    "찾는다. 이미 알려진 4가지 조건(금융소득종합과세 대상 여부, 중소기업 재직 "
                    "여부, 가구 전체 월소득, 직전년도 연 소득)과 같은 내용이면 제외한다. 문서에 "
                    "명시되지 않은 조건은 추측하지 않는다. 각 조건은 반드시 원문에서 인용 가능한 "
                    "근거 문장(source_excerpt)이 있어야 한다.\n"
                    "다음 세 가지는 반드시 지킨다(모두 실제로 잘못 추출되거나 실사용자가 헷갈려한 "
                    "사례가 있었다):\n"
                    "1. 극성(polarity) — question은 이중부정 없는 긍정형 직접 질문으로 만든다. "
                    "원문이 배제 조건(\"~인 경우 제외\", \"~하는 자는 지원 불가\")이어도 그 상태를 "
                    "그대로 긍정문으로 물어라(예: 원문이 \"배우자가 참여기업 사업주인 경우 제외\"면 "
                    "question은 \"배우자, 직계존비속 또는 형제자매가 참여기업 사업주(대표자)인가요?\"로 "
                    "만든다 — \"...가 아니신가요?\"처럼 부정 의문문으로 뒤집지 않는다. \"~하지 않으셨나요?\" "
                    "\"~아니신가요?\" 형태는 금지).\n"
                    "2. disqualify_on_yes — 이 질문에 \"예\"라고 답하면 이 상품에 신청할 수 없는지를 "
                    "true/false로 명시한다. 배제 조건을 그대로 긍정문으로 물었다면(1번 예시처럼) "
                    "\"예\"가 곧 배제 대상이므로 true. 반대로 자격 요건 자체를 긍정문으로 물었다면 "
                    "(예: \"국적을 보유하고 계신가요?\") \"아니오\"가 탈락이므로 false.\n"
                    "3. yes_label/no_label — 신청자가 \"예/아니오\"만 보고 답을 고르지 않도록, question의 "
                    "주어와 내용을 그대로 살려 완전한 문장으로 만든다(예: question이 \"배우자, 직계존비속 "
                    "또는 형제자매가 참여기업 사업주(대표자)인가요?\"면 yes_label은 \"예, 배우자, 직계존비속 "
                    "또는 형제자매가 참여기업 사업주(대표자)입니다.\", no_label은 \"아니요, 배우자, 직계존비속 "
                    "또는 형제자매가 참여기업 사업주(대표자)가 아닙니다.\").\n"
                    "4. 다음 두 종류는 게이트로 뽑지 않는다: (a) 자격 여부 자체가 아니라 가구원 수 "
                    "산정방식·나이요건 가산처럼 계산 방식을 조정하는 규칙(\"~는 가구원 수 산출에서 "
                    "제외\", \"~은 나이 요건에 기간을 가산\" 같은 문구), (b) 사업 안의 여러 선택 가능한 "
                    "세부 트랙(특별선발 분야, 특정 프로그램 등) 중 하나에만 적용되는 조건 — 상품/사업 "
                    "전체의 신청 자격에 적용되는 조건만 추출한다. 이 두 종류에 해당하면 게이트로 "
                    "만들지 말고 건너뛴다.\n"
                    "조건이 없으면 빈 배열을 반환한다. "
                    "gate_id는 영문 소문자 snake_case 짧은 식별자, question은 신청자에게 그대로 "
                    "보여줄 한국어 존댓말 질문, hint는 한 문장 설명이다. JSON 객체만 반환하며 "
                    "키는 gates 하나이고 값은 {gate_id, question, disqualify_on_yes, yes_label, "
                    "no_label, hint, source_excerpt} 객체의 배열이다."
                ),
                response_mime_type="application/json",
                # 게이트가 여러 건이면 JSON이 800토큰 안에 다 안 들어가 응답이
                # 중간에 잘려 파싱 에러가 나는 걸 실제로 확인했다(2026-09-01,
                # "Expecting ',' delimiter" — 잘린 JSON). 여유 있게 잡는다.
                max_output_tokens=2000,
            ),
        )
        text = (response.text or "").strip()
        # response_mime_type="application/json"을 줘도 가끔 ```json 코드펜스로
        # 감싸서 오는 경우가 있어 방어적으로 벗겨낸다.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{policy_id} 게이트 추출 응답이 올바른 JSON이 아닙니다: {exc}"
            ) from exc
        gates = payload.get("gates", [])
        return [
            {
                "gate_id": str(item["gate_id"]),
                "question": str(item["question"]),
                "hint": str(item.get("hint") or ""),
                "source_excerpt": str(item.get("source_excerpt") or ""),
                "disqualify_on_yes": bool(item.get("disqualify_on_yes", False)),
                "yes_label": str(item.get("yes_label") or ""),
                "no_label": str(item.get("no_label") or ""),
            }
            for item in gates
        ]


_WEB_SEARCH_CACHE_MAX = 500


class GeminiRoadmapExplainer:
    """결정론적 결과를 변경하지 않고 사용자용 설명만 생성한다."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        web_search_enabled: bool = False,
        web_search_monthly_limit: int = 1000,
        usage_db_path: str | None = None,
    ):
        self.model = model or os.environ.get("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client
        self.web_search_enabled = web_search_enabled
        self.web_search_monthly_limit = web_search_monthly_limit
        # 다중 워커·인스턴스에서도 한도를 공유하기 위해 공용 DB에 월별 카운터를 둔다.
        # SHARED_DB_PATH가 없으면(로컬 테스트 등) 프로세스 메모리로 폴백한다 — 이
        # 경우에는 프로세스별로 한도가 따로 카운트된다.
        self._usage_db_path = usage_db_path or os.getenv("SHARED_DB_PATH")
        self._web_search_month = date.today().strftime("%Y-%m")
        self._web_search_count = 0
        # 질문 문구를 키로 쓰는 캐시라 프로세스가 오래 떠 있을수록(대화 이력
        # TTL을 늘리면서 재배포 주기도 길어질 수 있음) 트래픽에 비례해 계속
        # 커진다 — 이미 메모리가 빠듯한 서버(RAM 954MB)에서 무한정 늘어나면
        # 위험하니 상한을 두고 오래된 항목부터 밀어낸다.
        self._web_search_cache: dict[str, str] = {}

    def _cache_web_search_answer(self, question: str, answer: str) -> None:
        self._web_search_cache[question] = answer
        while len(self._web_search_cache) > _WEB_SEARCH_CACHE_MAX:
            oldest_key = next(iter(self._web_search_cache))
            del self._web_search_cache[oldest_key]

    def _try_consume_web_search_quota(self) -> bool:
        """이번 달 웹 검색 한도가 남아 있으면 원자적으로 1회 소비하고 True를 반환한다."""
        month = date.today().strftime("%Y-%m")
        path = self._usage_db_path
        if not path:
            if month != self._web_search_month:
                self._web_search_month = month
                self._web_search_count = 0
            if self._web_search_count >= self.web_search_monthly_limit:
                return False
            self._web_search_count += 1
            return True
        connection = sqlite3.connect(path, timeout=5)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS web_search_usage "
                "(year_month TEXT PRIMARY KEY, search_count INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT search_count FROM web_search_usage WHERE year_month = ?", (month,)
            ).fetchone()
            current = row[0] if row else 0
            if current >= self.web_search_monthly_limit:
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO web_search_usage (year_month, search_count) VALUES (?, 1) "
                "ON CONFLICT(year_month) DO UPDATE SET search_count = search_count + 1",
                (month,),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def explain(self, request: RoadmapRequest, result: RoadmapResult) -> RoadmapExplanation:
        from google.genai import types

        payload = {
            "request": asdict(request),
            "result": result.to_dict(),
        }
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(payload, ensure_ascii=False, default=str),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "당신은 사회초년생용 금융교육 설명자다. 입력 JSON의 금액, 비율, 순위, "
                    "상품명과 출처를 절대 변경하거나 새로 만들지 않는다. 반드시 JSON 객체만 반환한다. "
                    "recommended_reason과 alternative_reason은 각각 최대 5문장까지만 작성한다. "
                    "입력에 이미 있는 rationale 문장을 그대로 요약하거나 나열하지 않는다. 대신 "
                    "(1) 이 시나리오가 어떤 상품·자산군 조합인지 처음 보는 사람도 이해할 수 있게 "
                    "풀어서 설명하고, (2) request의 나이·소득·위험성향·목표금액·목표기간·재직 여부 "
                    "등 사용자 조건과 구체적으로 연결지어 왜 이 조합이 이 사용자에게 맞는지 설명하고, "
                    "(3) 예상금액·목표 달성률처럼 이미 계산된 수치를 자연스러운 문장 속에 언급한다. "
                    "친절하고 이해하기 쉬운 어조로 쓰되, 금액·비율·순위·상품명은 입력값과 정확히 "
                    "일치해야 하며 새로운 숫자나 상품을 만들어내지 않는다. 마크다운 제목·목록·강조 "
                    "기호는 사용하지 않는다. "
                    "request.question이 비어 있으면 chat_reply는 null, 질문이 있으면 질문에 대한 "
                    "간결한 한국어 답변을 넣는다. 질문에 대안·회사명·상품명이 있으면 반드시 해당 "
                    "시나리오의 값만 사용한다. 월 납입한도 질문은 해당 시나리오의 monthly_limit만 "
                    "답하고 다른 금융제도 설명을 섞지 않는다. chat_reply는 채팅 말풍선에 표시되고 "
                    "로드맵 상세 카드는 화면 별도 영역(대화창 옆)에 함께 표시되므로, '아래', '위', "
                    "'다음과 같습니다' 처럼 특정 화면 위치를 가리키는 표현은 쓰지 않는다 — 대신 "
                    "'추천 로드맵을 확인해 보세요'처럼 위치를 지칭하지 않는 표현을 쓴다. 키는 "
                    "recommended_reason, alternative_reason, chat_reply다."
                ),
                max_output_tokens=1200,
                response_mime_type="application/json",
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini 설명 응답이 비어 있습니다.")
        try:
            data = json.loads(text)
            recommended_reason = str(data["recommended_reason"]).strip()
            alternative_reason = str(data["alternative_reason"]).strip()
            chat_reply_value = data.get("chat_reply")
            chat_reply = str(chat_reply_value).strip() if chat_reply_value else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemini 설명 응답 형식이 올바르지 않습니다.") from exc
        if not recommended_reason or not alternative_reason:
            raise RuntimeError("Gemini 상품별 추천 이유가 비어 있습니다.")
        return RoadmapExplanation(
            recommended_reason=recommended_reason,
            alternative_reason=alternative_reason,
            chat_reply=chat_reply,
        )

    def answer_financial_question(self, question: str, evidence: list[Evidence]) -> str:
        """검색된 공식 근거 범위 안에서 금융 질문에 직접 답한다."""
        from google.genai import types

        if self.web_search_enabled and question in self._web_search_cache:
            return self._web_search_cache[question]
        # 근거끼리 내용이 다르면 신뢰도가 높은 출처를 먼저 보여줘 LLM이
        # 그쪽을 우선하도록 유도한다(법령 > 시행령 > 공식 안내 > 교육자료).
        ordered_evidence = sorted(
            evidence, key=lambda item: source_priority(item.source_type), reverse=True
        )
        sources = [
            {
                "title": item.title,
                "source_url": item.source_url,
                "content": item.content,
                "parent_context": item.parent_content,
                "source_type": item.source_type,
            }
            for item in ordered_evidence
            if item.content
        ]
        answer = ""
        needs_web_search = not sources
        if sources:
            response = self.client.models.generate_content(
                model=self.model,
                contents=json.dumps(
                    {
                        "question": question,
                        "subquestions": _financial_subquestions(question),
                        "sources": sources,
                    },
                    ensure_ascii=False,
                ),
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "사용자의 금융 질문을 subquestions별로 나누어 sources의 검색 청크와 "
                        "parent_context만으로 답할 수 있는지 판정한다. 확인된 항목과 확인되지 "
                        "않은 항목을 구분한다. "
                        "추측하지 말고 JSON 객체만 반환한다. answer에는 2~4문장의 직접 답변을, "
                        "근거가 질문의 핵심 조건을 확정하지 못하면 needs_web_search=true를 넣는다. "
                        "sources 중 질문과 명확히 관련된 항목이 있으면 그 내용을 근거로 확정적으로 "
                        "답하고 needs_web_search=false로 둔다 — 완벽히 동일한 표현이 아니거나 부수적인 "
                        "세부사항이 빠졌다는 이유만으로 불필요하게 needs_web_search=true를 넣지 않는다. "
                        "sources 안의 서로 다른 항목이 같은 사안에 대해 실제로 상충하는 내용을 말할 "
                        "때만 source_type 기준으로 law > enforcement_decree > tax_guide > "
                        "official_guide_synthesis > finance_education 순으로 신뢰도가 높은 "
                        "쪽을 따르고, 그래도 우열을 가릴 수 없으면 추측하지 말고 확인이 "
                        "필요하다고 답한다. "
                        "키는 answer, needs_web_search다."
                    ),
                    max_output_tokens=500,
                    response_mime_type="application/json",
                ),
            )
            try:
                data = json.loads((response.text or "").strip())
                answer = str(data.get("answer") or "").strip()
                needs_web_search = bool(data.get("needs_web_search"))
            except (TypeError, ValueError, json.JSONDecodeError):
                needs_web_search = True
        if needs_web_search and self.web_search_enabled:
            return self._answer_with_google_search(question, answer)
        if answer:
            return answer
        return "검색된 공식 근거만으로는 질문을 확정할 수 없습니다. 최신 공식 약관이나 운영기관 안내를 확인해 주세요."

    def _answer_with_google_search(self, question: str, fallback_answer: str = "") -> str:
        from google.genai import types

        cached = self._web_search_cache.get(question)
        if cached:
            return cached
        if not self._try_consume_web_search_quota():
            return fallback_answer or "공식 웹 검색 월 한도에 도달했습니다. 내부 근거만으로는 답을 확정할 수 없습니다."

        allowed_domains = (
            "law.go.kr", "fsc.go.kr", "fss.or.kr", "kinfa.or.kr",
            "korea.kr", "moef.go.kr", "nts.go.kr", "bokjiro.go.kr",
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=question,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                system_instruction=(
                    "금융 질문에 먼저 직접 답한다. 정부·공공기관 또는 금융회사 공식 사이트만 "
                    "근거로 사용한다. 블로그, 카페, 언론, 광고, 검색 요약만으로 결론내리지 않는다. "
                    "확인되지 않는 내용은 추측하지 말고 무엇이 부족한지 밝힌다. 2~4문장의 "
                    "간결한 한국어로 답한다."
                ),
                max_output_tokens=700,
            ),
        )
        answer = (response.text or "").strip()
        sources: list[str] = []
        candidates = getattr(response, "candidates", None) or []
        metadata = getattr(candidates[0], "grounding_metadata", None) if candidates else None
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            domain = str(getattr(web, "domain", "") or "").lower().removeprefix("www.")
            if not any(domain == item or domain.endswith(f".{item}") for item in allowed_domains):
                continue
            title = str(getattr(web, "title", "") or domain).strip()
            uri = str(getattr(web, "uri", "") or "").strip()
            source = f"{title} ({uri})" if uri else title
            if source not in sources:
                sources.append(source)
        if not answer or not sources:
            if fallback_answer:
                return (
                    fallback_answer
                    + " 다만 공식 웹 검색에서도 추가 근거를 확인하지 못해 "
                    "이 부분은 확정할 수 없습니다."
                )
            return "공식 웹 출처에서 질문을 확인할 근거를 찾지 못했습니다. 최신 상품 약관이나 운영기관에 확인해 주세요."
        final = answer + "\n\n공식 출처: " + " · ".join(sources[:3])
        self._cache_web_search_answer(question, final)
        return final

    def answer_general_definition(self, question: str) -> str | None:
        """RAG 금융 코퍼스에도, 정책 게이트 문구에도 없는 일반 상식·법률·행정
        용어 정의 질문(예: "직계존비속이 뭐야?")에 웹검색으로 답한다.

        _answer_with_google_search와 달리 공식 도메인(law.go.kr 등)으로
        출처를 제한하지 않는다 — 재무 자문이 아니라 사전적 정의라 신뢰도
        리스크가 낮은데, 그 제한 때문에 이런 질문엔 항상 "확정할 수 없다"고만
        답하던 문제가 있었다(실사용자 피드백 + 서버 로그로 확인). None을
        반환하면 호출부가 기존 실패 문구를 그대로 쓴다.
        """
        if not self.web_search_enabled:
            return None
        from google.genai import types

        cached = self._web_search_cache.get(question)
        if cached:
            return cached
        if not self._try_consume_web_search_quota():
            return None
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=question,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    system_instruction=(
                        "일반 상식·법률·행정 용어의 정의를 묻는 질문에 한국어로 "
                        "간결하게(1~2문장) 답한다. 확인되지 않는 내용은 추측하지 "
                        "않는다. 재무 자문이 아니라 사전적 정의를 알려주는 것이므로 "
                        "특정 도메인으로 출처를 제한하지 않되, 검색 결과를 바탕으로 "
                        "답한다."
                    ),
                    max_output_tokens=300,
                ),
            )
        except Exception:
            return None
        answer = (response.text or "").strip()
        if not answer:
            return None
        self._cache_web_search_answer(question, answer)
        return answer


class GeminiConversationPlanner:
    """규칙으로 해석하지 못한 대화를 제한된 실행 계획 JSON으로 변환한다."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model or os.environ.get("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client

    def plan(self, request: RoadmapRequest, message: str):
        from google.genai import types

        from .conversation import ConversationIntent, ConversationPlan, INTENT_TOOLS

        del request
        redacted_message = re.sub(r"[\w.+-]+@[\w.-]+", "<이메일>", message)
        redacted_message = re.sub(
            r"[\d,.]+\s*(?:억|천만|만)?\s*원", "<금액>", redacted_message
        )
        redacted_message = re.sub(r"\d", "#", redacted_message)
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps({"message": redacted_message}, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "사용자 금융 대화를 실행 계획 JSON으로만 변환한다. intent는 condition_change, "
                    "result_explanation, product_alternatives, product_ranking, policy_eligibility, financial_qa, input_completion, "
                    "unclear 중 "
                    "하나다. tools는 condition_parser, roadmap_calculators, ranking, result_explainer, "
                    "policy_repository, savings_repository, candidate_filter, candidate_scoring, ranking, policy_qualification, "
                    "rag_search, input_validator 중 필요한 것만 "
                    "선택한다. structured_changes에는 monthly_budget, horizon_months, target_amount, "
                    "max_investment_ratio, has_emergency_fund, max_investment_ratio_delta만 사용할 수 "
                    "있다. '더 안전하게'는 max_investment_ratio_delta=-0.1, '위험을 늘려도 된다'는 "
                    "max_investment_ratio_delta=0.1로 표현한다 — 이건 위험도(risk)라는 변경 대상이 "
                    "명확한 경우에만 해당한다. '더 좋은 걸로', '더 나은 걸로', '알아서 해줘'처럼 "
                    "위험도·금액·기간 중 **무엇을** 바꿀지 특정할 수 없는 표현은 intent를 반드시 "
                    "unclear로 하고 structured_changes를 임의로 채우지 않는다. 가려진 숫자를 "
                    "추측하지 말고 값이 불명확하면 intent를 unclear로 하고 "
                    "clarification_question에 한국어 질문 하나를 넣는다."
                ),
                response_mime_type="application/json",
                max_output_tokens=500,
                temperature=0,
                seed=0,
            ),
        )
        try:
            data = json.loads((response.text or "").strip())
            intent = ConversationIntent(str(data["intent"]))
            tools = tuple(str(tool) for tool in data.get("tools", []))
            raw_changes = data.get("structured_changes") or {}
            changes = {str(key): value for key, value in raw_changes.items() if value is not None}
            clarification = data.get("clarification_question")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemini 실행 계획 응답 형식이 올바르지 않습니다.") from exc
        if intent != ConversationIntent.UNCLEAR and not tools:
            tools = INTENT_TOOLS[intent]
        return ConversationPlan(
            intent=intent,
            tools=tools,
            clarification_question=str(clarification).strip() if clarification else None,
            structured_changes=changes or None,
            planned_by="llm",
        )
