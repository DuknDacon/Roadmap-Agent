from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from roadmap_agent.config import load_env_file
from roadmap_agent.gemini import GeminiEmbeddingClient
from roadmap_agent.repositories import postgres_connection_factory_from_env
from roadmap_agent.rag_chunking import chunk_markdown as semantic_chunk_markdown
from roadmap_agent.retrieval import _metadata, _source_url


def chunk_markdown(text: str, *, maximum_chars: int = 1800) -> list[tuple[str, str]]:
    """기존 스크립트 호출부와 테스트를 위한 호환 래퍼."""
    return [
        (chunk.section, chunk.content)
        for chunk in semantic_chunk_markdown(text, maximum_chars=maximum_chars)
    ]


def collect_documents(root: Path) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.casefold() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root)
        source_id = str(relative)
        source_type = relative.parts[0] if len(relative.parts) > 1 else "rag"
        title = _metadata(text, "title") or path.stem
        url = _source_url(text)
        metadata_source_type = _metadata(text, "source_type") or source_type
        for chunk in semantic_chunk_markdown(text, source_type=metadata_source_type):
            content_hash = hashlib.sha256(
                f"{chunk.content}\n{chunk.parent_content}".encode()
            ).hexdigest()
            documents.append(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "title": title,
                    "section": chunk.section,
                    "content": chunk.content,
                    "source_url": url,
                    "parent_section": chunk.parent_section,
                    "parent_content": chunk.parent_content,
                    "content_hash": content_hash,
                }
            )
    return documents


def index_documents(root: Path, *, batch_size: int = 20, force: bool = False) -> tuple[int, int]:
    documents = collect_documents(root)
    connect = postgres_connection_factory_from_env()
    sql = """
        INSERT INTO rag_documents
            (source_type, source_id, title, section, content, source_url, metadata, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
        ON CONFLICT (source_type, source_id, section) DO UPDATE SET
            title = EXCLUDED.title,
            content = EXCLUDED.content,
            source_url = EXCLUDED.source_url,
            metadata = EXCLUDED.metadata,
            embedding = EXCLUDED.embedding,
            created_at = now()
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_type, source_id, section, metadata->>'content_hash' AS content_hash "
                "FROM rag_documents"
            )
            existing = {
                (row["source_type"], row["source_id"], row["section"]): row["content_hash"]
                for row in cursor.fetchall()
            }
            desired_keys = {
                (item["source_type"], item["source_id"], item["section"])
                for item in documents
            }
            managed_documents = {(key[0], key[1]) for key in desired_keys}
            stale_keys = {
                key
                for key in set(existing) - desired_keys
                if (key[0], key[1]) in managed_documents
            }
            if stale_keys:
                cursor.executemany(
                    "DELETE FROM rag_documents "
                    "WHERE source_type = %s AND source_id = %s AND section = %s",
                    sorted(stale_keys),
                )
                connection.commit()
        pending = [
            item
            for item in documents
            if force
            or existing.get((item["source_type"], item["source_id"], item["section"]))
            != item["content_hash"]
        ]
        if not pending:
            return len(documents), 0
        embedder = GeminiEmbeddingClient()
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = embedder.embed_documents([item["content"] for item in batch])
            with connection.cursor() as cursor:
                for item, vector in zip(batch, vectors, strict=True):
                    vector_literal = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
                    cursor.execute(
                        sql,
                        (
                            item["source_type"],
                            item["source_id"],
                            item["title"],
                            item["section"],
                            item["content"],
                            item["source_url"],
                            json.dumps({
                                "content_hash": item["content_hash"],
                                "parent_section": item["parent_section"],
                                "parent_content": item["parent_content"],
                            }, ensure_ascii=False),
                            vector_literal,
                        ),
                    )
            connection.commit()
    return len(documents), len(pending)


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 Markdown을 Gemini로 임베딩해 pgvector에 적재")
    parser.add_argument("--rag-root", type=Path, default=Path("data/rag"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--force", action="store_true", help="변경 여부와 무관하게 전체 재임베딩")
    args = parser.parse_args()
    load_env_file(args.env_file)
    total, embedded = index_documents(args.rag_root, batch_size=args.batch_size, force=args.force)
    print(f"RAG 청크 총 {total}건, 신규·변경 {embedded}건 임베딩 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
