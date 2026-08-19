import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "index_rag_documents.py"
SPEC = importlib.util.spec_from_file_location("index_rag_documents", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RagChunkTest(unittest.TestCase):
    def test_splits_on_headings_without_overlap(self):
        chunks = MODULE.chunk_markdown("# 가입대상\n\n청년 대상\n\n# 혜택\n\n비과세")
        self.assertEqual([item[0] for item in chunks], ["가입대상#1", "혜택#1"])
        self.assertNotIn("청년 대상", chunks[1][1])

    def test_splits_long_section_at_configured_size(self):
        chunks = MODULE.chunk_markdown("# 안내\n\n" + "가" * 21, maximum_chars=10)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(content) <= 10 for _, content in chunks))

    def test_law_source_splits_circled_paragraphs_and_keeps_parent_context(self):
        from roadmap_agent.rag_chunking import chunk_markdown

        chunks = chunk_markdown(
            "# 제1조\n\n① 가입 요건이다.\n\n② 해지 요건이다.",
            source_type="law",
        )

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].content.startswith("①"))
        self.assertIn("② 해지 요건", chunks[0].parent_content)

    def test_frontmatter_is_metadata_not_search_content(self):
        chunks = MODULE.chunk_markdown(
            "---\ntitle: 비밀 메타데이터\n---\n# 본문\n\n검색 내용"
        )

        self.assertEqual(chunks, [("본문#1", "검색 내용")])


if __name__ == "__main__":
    unittest.main()
