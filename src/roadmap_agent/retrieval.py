from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import Evidence
from .rag_chunking import chunk_markdown


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
STOP_TOKENS = {"어떻게", "관련", "대한", "경우", "현재", "질문", "알려줘"}


@dataclass(frozen=True)
class Document:
    path: Path
    title: str
    source_url: str
    text: str
    source_type: str


@dataclass(frozen=True)
class DocumentChunk:
    document: Document
    section: str
    content: str
    parent_content: str


def _query_tokens(query: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(query)
        if token.casefold() not in STOP_TOKENS
    }


def _lexical_score(query: str, title: str, content: str) -> int:
    tokens = _query_tokens(query)
    lowered_title = title.casefold()
    lowered_content = content.casefold()
    title_score = 80 * sum(lowered_title.count(token) for token in tokens)
    content_score = sum(lowered_content.count(token) for token in tokens)
    return title_score + content_score


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
        self.chunks = self._chunk_documents()

    def _load(self) -> list[Document]:
        documents = []
        for path in sorted(self.root.rglob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            text = path.read_text(encoding="utf-8")
            title = _metadata(text, "title") or path.stem
            source_type = _metadata(text, "source_type") or (
                path.relative_to(self.root).parts[0]
                if len(path.relative_to(self.root).parts) > 1
                else "rag"
            )
            documents.append(Document(path, title, _source_url(text), text, source_type))
        return documents

    def _chunk_documents(self) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for document in self.documents:
            for chunk in chunk_markdown(document.text, source_type=document.source_type):
                chunks.append(DocumentChunk(
                    document, chunk.section, chunk.content, chunk.parent_content
                ))
        return chunks

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        tokens = _query_tokens(query)
        ranked: list[Evidence] = []
        for chunk in self.chunks:
            score = _lexical_score(query, chunk.document.title, chunk.content)
            if score:
                ranked.append(
                    Evidence(
                        chunk.document.title,
                        chunk.document.source_url,
                        f"{chunk.document.path}#{chunk.section}",
                        score,
                        chunk.content,
                        chunk.parent_content,
                    )
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
        vector_sql = """
            SELECT title, source_url, source_type, source_id, section, content, metadata,
                   1 - (embedding <=> %s::vector) AS similarity
              FROM rag_documents
             WHERE embedding IS NOT NULL
             ORDER BY embedding <=> %s::vector
             LIMIT %s
        """
        tokens = sorted(_query_tokens(query), key=len, reverse=True)[:8]
        patterns = [f"%{token}%" for token in tokens]
        lexical_sql = """
            SELECT title, source_url, source_type, source_id, section, content, metadata,
                   0 AS similarity
              FROM rag_documents
             WHERE title ILIKE ANY(%s) OR content ILIKE ANY(%s)
             LIMIT %s
        """
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(vector_sql, (vector_literal, vector_literal, max(limit * 4, 12)))
                rows = list(cursor.fetchall())
                if patterns:
                    cursor.execute(lexical_sql, (patterns, patterns, max(limit * 4, 12)))
                    rows.extend(cursor.fetchall())
        candidates: dict[tuple[str, str, str], Evidence] = {}
        for row in rows:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)
            vector_score = round(float(row.get("similarity") or 0) * 1000)
            lexical_score = _lexical_score(query, str(row["title"]), str(row.get("content") or ""))
            evidence = Evidence(
                title=str(row["title"]),
                source_url=str(row.get("source_url") or ""),
                path=f"postgres:{row['source_type']}:{row['source_id']}:{row['section']}",
                score=vector_score + lexical_score,
                content=str(row.get("content") or ""),
                parent_content=str(metadata.get("parent_content") or ""),
            )
            key = (evidence.title, evidence.source_url, str(row["section"]))
            current = candidates.get(key)
            if current is None or evidence.score > current.score:
                candidates[key] = evidence
        return sorted(candidates.values(), key=lambda item: -item.score)[:limit]


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
            primary_results = self.primary.search(query, limit)
            local_results = self.fallback.search(query, limit)
            if primary_results:
                self.primary_used = True
            if local_results:
                self.fallback_used = True
            if not primary_results:
                self.fallback_reason = "벡터 검색 결과 없음"
                return local_results

            # 벡터 검색은 항상 결과를 반환할 수 있으므로, 구체적인 상품명이 일치하는
            # 로컬 공식 문서 1건을 함께 보존해 최신 미인덱스 문서도 답변에 사용한다.
            merged = [*local_results[:1], *primary_results]
            unique: list[Evidence] = []
            seen: set[tuple[str, str]] = set()
            for item in merged:
                key = (item.title, item.source_url)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            return unique[:limit]
        except Exception as exc:
            self.fallback_used = True
            self.fallback_reason = type(exc).__name__
            return self.fallback.search(query, limit)
