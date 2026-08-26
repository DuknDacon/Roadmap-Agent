from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from roadmap_agent.config import load_env_file
from roadmap_agent.gemini import GeminiEmbeddingClient
from roadmap_agent.rag_chunking import chunk_markdown as semantic_chunk_markdown
from roadmap_agent.retrieval import HybridRagRetriever, _metadata, _source_url


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


def index_documents(
    root: Path,
    *,
    index_dir: Path = Path("data/rag_index"),
    batch_size: int = 20,
    force: bool = False,
) -> tuple[int, int]:
    del force
    documents = collect_documents(root)
    HybridRagRetriever.build(
        root, index_dir, GeminiEmbeddingClient(), batch_size=batch_size
    )
    return len(documents), len(documents)


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 Markdown으로 FAISS+BM25 인덱스 생성")
    parser.add_argument("--rag-root", type=Path, default=Path("data/rag"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--index-dir", type=Path, default=Path("data/rag_index"))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--force", action="store_true", help="변경 여부와 무관하게 전체 재임베딩")
    args = parser.parse_args()
    load_env_file(args.env_file)
    total, embedded = index_documents(
        args.rag_root,
        index_dir=args.index_dir,
        batch_size=args.batch_size,
        force=args.force,
    )
    print(f"RAG 청크 총 {total}건, 신규·변경 {embedded}건 임베딩 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
