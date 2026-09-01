from __future__ import annotations

import os
import pickle
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import Evidence
from .rag_chunking import chunk_markdown


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
STOP_TOKENS = {"어떻게", "관련", "대한", "경우", "현재", "질문", "알려줘"}

# 문서 간 답변이 충돌할 때 우선순위를 정하는 기준. 값이 높을수록 우선한다.
# 법령 원문 > 시행령 > 국세청 등 공식 안내 > 팀이 통합·요약한 안내 > 일반 교육자료.
SOURCE_TYPE_PRIORITY = {
    "law": 5,
    "enforcement_decree": 4,
    "tax_guide": 3,
    "official_guide_synthesis": 2,
    "finance_education": 1,
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def source_priority(source_type: str) -> int:
    return SOURCE_TYPE_PRIORITY.get(source_type, 0)


def is_effective_now(effective_date: str, *, today: str | None = None) -> bool:
    """effective_date가 아직 시행되지 않은 미래 날짜면 False, 그 외(빈 값·해석 불가·현재/과거)는 True.

    문서 메타데이터의 effective_date는 형식이 자유로운 필드라 값이 없거나
    ISO 날짜가 아닌 경우도 있다. 그런 값을 근거 없이 걸러내면 정상 문서가
    사라지므로, 확실히 미래로 해석되는 ISO 날짜일 때만 제외한다.
    """
    if not effective_date or not _ISO_DATE_RE.match(effective_date):
        return True
    reference = today or time.strftime("%Y-%m-%d")
    return effective_date <= reference


@dataclass(frozen=True)
class Document:
    path: Path
    title: str
    source_url: str
    text: str
    source_type: str
    status: str = ""
    effective_date: str = ""


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
    # "가입"처럼 짧고 흔한 단어의 반복보다 "청년미래적금"처럼 긴 고유어를
    # 우선한다. 별도 통계 인덱스가 없는 로컬 폴백에서 IDF에 가까운 역할을 한다.
    title_score = sum(
        20 * len(token) * lowered_title.count(token) for token in tokens
    )
    content_score = sum(
        len(token) * lowered_content.count(token) for token in tokens
    )
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
    """하이브리드 인덱스 장애 시 공식 Markdown을 검색하는 결정론적 폴백."""

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
            status = _metadata(text, "status")
            effective_date = _metadata(text, "effective_date")
            documents.append(
                Document(path, title, _source_url(text), text, source_type, status, effective_date)
            )
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
        # 긴 문서의 유사 청크가 결과 전체를 독점하지 않도록 문서별 최고 청크만
        # 남긴다. 공식 근거의 다양성을 확보하고 희소한 정책 문서를 보존한다.
        best_by_document: dict[Path, Evidence] = {}
        for chunk in self.chunks:
            if not is_effective_now(chunk.document.effective_date):
                continue
            score = _lexical_score(query, chunk.document.title, chunk.content)
            if score:
                evidence = Evidence(
                    chunk.document.title,
                    chunk.document.source_url,
                    f"{chunk.document.path}#{chunk.section}",
                    score,
                    chunk.content,
                    chunk.parent_content,
                    chunk.document.source_type,
                    chunk.document.status,
                    chunk.document.effective_date,
                )
                current = best_by_document.get(chunk.document.path)
                if current is None or evidence.score > current.score:
                    best_by_document[chunk.document.path] = evidence
        ranked = list(best_by_document.values())
        return sorted(ranked, key=lambda item: (-item.score, item.title))[:limit]


class QueryEmbeddingClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HybridRagRetriever:
    """기능 1과 같은 FAISS dense + BM25 sparse + RRF 파일 인덱스 검색기."""

    INDEX_FILE = "index.faiss"
    DOCUMENTS_FILE = "documents.pkl"

    def __init__(self, index_dir: str | Path, embedding_client: QueryEmbeddingClient):
        import faiss
        from rank_bm25 import BM25Okapi

        self.index_dir = Path(index_dir)
        self.embedding_client = embedding_client
        index_path = self.index_dir / self.INDEX_FILE
        documents_path = self.index_dir / self.DOCUMENTS_FILE
        if not index_path.is_file() or not documents_path.is_file():
            raise FileNotFoundError(
                f"RAG 인덱스가 없습니다: {self.index_dir}. scripts/build_rag_index.py를 실행하세요."
            )
        self.index = faiss.read_index(str(index_path))
        with documents_path.open("rb") as stream:
            payload = pickle.load(stream)
        self.entries: list[dict[str, str]] = payload["entries"]
        self.tokenized_documents: list[list[str]] = payload["tokenized_documents"]
        self.bm25 = BM25Okapi(self.tokenized_documents)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        from kiwipiepy import Kiwi

        if not hasattr(HybridRagRetriever, "_kiwi"):
            HybridRagRetriever._kiwi = Kiwi()
        kept = {"NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "VX", "XR", "SL", "SH", "SN"}
        return [
            token.form.casefold()
            for token in HybridRagRetriever._kiwi.tokenize(text)
            if token.tag in kept
        ]

    @classmethod
    def build(
        cls,
        source_root: str | Path,
        index_dir: str | Path,
        embedding_client: QueryEmbeddingClient,
        *,
        batch_size: int = 20,
    ) -> None:
        import faiss
        import numpy as np

        local = LocalRagRetriever(Path(source_root))
        entries = [
            {
                "title": chunk.document.title,
                "source_url": chunk.document.source_url,
                "path": f"{chunk.document.path}#{chunk.section}",
                "content": chunk.content,
                "parent_content": chunk.parent_content,
                "source_type": chunk.document.source_type,
                "status": chunk.document.status,
                "effective_date": chunk.document.effective_date,
            }
            for chunk in local.chunks
        ]
        if not entries:
            raise RuntimeError("RAG 인덱스를 만들 문서가 없습니다.")
        texts = [f"{entry['title']}\n{entry['content']}" for entry in entries]
        embedded: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            embedded.extend(embedding_client.embed_documents(texts[start : start + batch_size]))
        vectors = np.asarray(embedded, dtype="float32")
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        tokenized = [cls._tokens(text) for text in texts]
        target = Path(index_dir)
        target.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(target / cls.INDEX_FILE))
        with (target / cls.DOCUMENTS_FILE).open("wb") as stream:
            pickle.dump(
                {"version": 1, "entries": entries, "tokenized_documents": tokenized},
                stream,
            )

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        import faiss
        import numpy as np

        fetch = min(max(limit * 5, 20), len(self.entries))
        query_vector = np.asarray(self.embedding_client.embed_query(query), dtype="float32")[None, :]
        faiss.normalize_L2(query_vector)
        dense_scores, dense_indices = self.index.search(query_vector, fetch)
        dense = {
            int(index): (rank, float(score))
            for rank, (index, score) in enumerate(
                zip(dense_indices[0], dense_scores[0]), start=1
            )
            if index >= 0
        }
        sparse_scores = self.bm25.get_scores(self._tokens(query))
        sparse_order = np.argsort(sparse_scores)[::-1][:fetch]
        sparse = {
            int(index): (rank, float(sparse_scores[index]))
            for rank, index in enumerate(sparse_order, start=1)
            if sparse_scores[index] > 0
        }
        dense_weight = float(os.getenv("DENSE_WEIGHT", "0.6"))
        sparse_weight = float(os.getenv("BM25_WEIGHT", "0.4"))
        ranked: list[tuple[float, int]] = []
        for index in set(dense) | set(sparse):
            if not is_effective_now(self.entries[index].get("effective_date", "")):
                continue
            score = 0.0
            if index in dense:
                score += dense_weight / (60 + dense[index][0])
            if index in sparse:
                score += sparse_weight / (60 + sparse[index][0])
            ranked.append((score, index))
        results = []
        for score, index in sorted(ranked, reverse=True)[:limit]:
            entry = self.entries[index]
            results.append(
                Evidence(
                    entry["title"], entry["source_url"], entry["path"],
                    round(score * 1_000_000), entry["content"], entry["parent_content"],
                    entry.get("source_type", ""), entry.get("status", ""),
                    entry.get("effective_date", ""),
                )
            )
        return results


class FallbackRagRetriever:
    """외부 임베딩/DB 장애 시 로컬 키워드 검색을 유지한다."""

    def __init__(self, primary: Any, fallback: Any):
        self.primary = primary
        self.fallback = fallback
        self.primary_used = False
        self.fallback_used = False
        self.fallback_reason: str | None = None

    def search(self, query: str, limit: int = 3) -> list[Evidence]:
        t0 = time.monotonic()
        print(f"[RAG-01] search 진입 | query={query[:60]!r} limit={limit}")
        try:
            primary_results = self.primary.search(query, limit)
            local_results = self.fallback.search(query, limit)
            if primary_results:
                self.primary_used = True
            if local_results:
                self.fallback_used = True
            if not primary_results:
                self.fallback_reason = "벡터 검색 결과 없음"
                print(
                    f"[RAG-02] ⚠︎ primary 결과 없음 → local fallback만 사용 | "
                    f"fallback={len(local_results)}건 | {time.monotonic()-t0:.2f}s"
                )
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
            result = unique[:limit]
            top_score = result[0].score if result else None
            print(
                f"[RAG-02] primary={len(primary_results)}건 local={len(local_results)}건 "
                f"→ 병합 {len(result)}건 top_score={top_score} | {time.monotonic()-t0:.2f}s"
            )
            for i, item in enumerate(result, 1):
                print(
                    f"[RAG-03]   {i}. {item.title!r} (score={item.score}) "
                    f"| {item.source_url or '출처 없음'}"
                )
            return result
        except Exception as exc:
            self.fallback_used = True
            self.fallback_reason = type(exc).__name__
            results = self.fallback.search(query, limit)
            print(
                f"[RAG-02] ⚠︎ primary 예외 → local fallback 전환 | {type(exc).__name__} | "
                f"fallback={len(results)}건 | {time.monotonic()-t0:.2f}s"
            )
            return results
