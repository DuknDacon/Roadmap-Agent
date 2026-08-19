from __future__ import annotations

from dataclasses import dataclass
import re


HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n?", re.DOTALL)
LEGAL_UNIT_RE = re.compile(r"^(?:[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\d+[.)])\s*")


@dataclass(frozen=True)
class RagChunk:
    section: str
    content: str
    parent_section: str
    parent_content: str


def _sections(text: str) -> list[tuple[str, str]]:
    body = FRONTMATTER_RE.sub("", text).strip()
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return [("본문", body)] if body else []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[match.end() : end].strip()
        sections.append((match.group(1).strip(), content))
    return sections


def chunk_markdown(
    text: str,
    *,
    source_type: str = "rag",
    maximum_chars: int = 1800,
    maximum_parent_chars: int = 6000,
) -> list[RagChunk]:
    """제목과 법령 조·항 경계를 보존하는 공용 RAG 청커."""
    chunks: list[RagChunk] = []
    legal_source = source_type in {"law", "enforcement_decree", "policy"}
    for heading, content in _sections(text):
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
        parent = f"# {heading}\n\n{content}".strip()[:maximum_parent_chars]
        current = ""
        part = 1

        def flush() -> None:
            nonlocal current, part
            if not current:
                return
            chunks.append(RagChunk(f"{heading}#{part}", current, heading, parent))
            current = ""
            part += 1

        for paragraph in paragraphs:
            if legal_source and LEGAL_UNIT_RE.match(paragraph):
                flush()
            if len(paragraph) > maximum_chars:
                flush()
                for start in range(0, len(paragraph), maximum_chars):
                    piece = paragraph[start : start + maximum_chars]
                    chunks.append(RagChunk(f"{heading}#{part}", piece, heading, parent))
                    part += 1
                continue
            if current and len(current) + len(paragraph) + 2 > maximum_chars:
                flush()
            current = f"{current}\n\n{paragraph}".strip()
        flush()
    return chunks

