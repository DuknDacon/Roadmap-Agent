from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import Evidence


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


@dataclass(frozen=True)
class Document:
    path: Path
    title: str
    source_url: str
    text: str


def _metadata(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}:\s*(.+)$", text)
    return match.group(1).strip().strip('"') if match else ""


def _source_url(text: str) -> str:
    direct = _metadata(text, "source_url")
    if direct:
        return direct
    block = re.search(r"(?ms)^source_urls:\s*\n((?:\s+-\s+.*\n?)+)", text)
    if not block:
        return ""
    first = re.search(r"(?m)^\s+-\s+(.+)$", block.group(1))
    return first.group(1).strip() if first else ""


class LocalRagRetriever:
    """pgvector 연결 전에도 공식 Markdown을 검색할 수 있는 결정론적 폴백."""

    def __init__(self, root: Path):
        self.root = root
        self.documents = self._load()

    def _load(self) -> list[Document]:
        documents = []
        for path in sorted(self.root.rglob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            text = path.read_text(encoding="utf-8")
            title = _metadata(text, "title") or path.stem
            documents.append(Document(path, title, _source_url(text), text))
        return documents

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        tokens = {token.casefold() for token in TOKEN_RE.findall(query)}
        ranked: list[Evidence] = []
        for document in self.documents:
            haystack = document.text.casefold()
            score = sum(haystack.count(token) for token in tokens)
            if score:
                ranked.append(
                    Evidence(document.title, document.source_url, str(document.path), score)
                )
        return sorted(ranked, key=lambda item: (-item.score, item.title))[:limit]


class QueryEmbeddingClient(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class PostgresVectorRagRetriever:
    """Gemini 쿼리 임베딩과 pgvector cosine distance를 사용하는 검색기."""

    def __init__(self, connection_factory: Any, embedding_client: QueryEmbeddingClient):
        self.connection_factory = connection_factory
        self.embedding_client = embedding_client

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        vector = self.embedding_client.embed_query(query)
        vector_literal = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
        sql = """
            SELECT title, source_url, source_type, source_id, section,
                   1 - (embedding <=> %s::vector) AS similarity
              FROM rag_documents
             WHERE embedding IS NOT NULL
             ORDER BY embedding <=> %s::vector
             LIMIT %s
        """
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (vector_literal, vector_literal, limit))
                rows = cursor.fetchall()
        return [
            Evidence(
                title=str(row["title"]),
                source_url=str(row.get("source_url") or ""),
                path=f"postgres:{row['source_type']}:{row['source_id']}:{row['section']}",
                score=round(float(row["similarity"]) * 1000),
            )
            for row in rows
        ]


class FallbackRagRetriever:
    """외부 임베딩/DB 장애 시 로컬 키워드 검색을 유지한다."""

    def __init__(self, primary: Any, fallback: Any):
        self.primary = primary
        self.fallback = fallback
        self.primary_used = False
        self.fallback_used = False
        self.fallback_reason: str | None = None

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        try:
            results = self.primary.search(query, limit)
            if results:
                self.primary_used = True
                return results
            self.fallback_used = True
            self.fallback_reason = "벡터 검색 결과 없음"
            return self.fallback.search(query, limit)
        except Exception as exc:
            self.fallback_used = True
            self.fallback_reason = type(exc).__name__
            return self.fallback.search(query, limit)
