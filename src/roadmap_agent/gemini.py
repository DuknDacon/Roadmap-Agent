from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from .domain import RoadmapRequest, RoadmapResult


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
            raise ValueError("현재 rag_documents.embedding 스키마는 1536차원만 지원합니다.")
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


class GeminiRoadmapExplainer:
    """결정론적 결과를 변경하지 않고 사용자용 설명만 생성한다."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, client: Any | None = None):
        self.model = model or os.environ.get("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client

    def explain(self, request: RoadmapRequest, result: RoadmapResult) -> str:
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
                    "상품명과 출처를 절대 변경하거나 새로 만들지 않는다. 추천 결과를 한국어로 "
                    "간결하게 설명하고, 목표 부족액과 위험 경고 및 공식 출처 확인 필요성을 포함한다."
                ),
                max_output_tokens=700,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini 설명 응답이 비어 있습니다.")
        return text
