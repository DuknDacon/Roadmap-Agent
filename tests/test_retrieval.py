import tempfile
import unittest
from pathlib import Path

from roadmap_agent.retrieval import LocalRagRetriever


class RetrievalTest(unittest.TestCase):
    def test_readme_is_not_loaded_as_rag_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("ISA ISA ISA", encoding="utf-8")
            (root / "guide.md").write_text(
                "---\ntitle: ISA 공식 안내\nsource_url: https://example.test\n---\nISA",
                encoding="utf-8",
            )
            retriever = LocalRagRetriever(root)
            self.assertEqual(len(retriever.documents), 1)
            self.assertEqual(retriever.search("ISA")[0].title, "ISA 공식 안내")

    def test_returns_source_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "isa.md"
            path.write_text(
                "---\ntitle: ISA 안내\nsource_url: https://example.test/isa\n---\nISA 손익통산",
                encoding="utf-8",
            )
            result = LocalRagRetriever(Path(temp_dir)).search("ISA 손익통산")
            self.assertEqual(result[0].title, "ISA 안내")
            self.assertEqual(result[0].source_url, "https://example.test/isa")

    def test_uses_first_url_from_source_urls_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guide.md"
            path.write_text(
                "---\ntitle: 통합 안내\nsource_urls:\n  - https://example.test/first\n"
                "  - https://example.test/second\n---\nISA 안내",
                encoding="utf-8",
            )
            result = LocalRagRetriever(Path(temp_dir)).search("ISA")
            self.assertEqual(result[0].source_url, "https://example.test/first")


if __name__ == "__main__":
    unittest.main()
