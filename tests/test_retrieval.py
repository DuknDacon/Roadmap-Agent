import tempfile
import unittest
from pathlib import Path

from roadmap_agent.retrieval import LocalRagRetriever
from roadmap_agent.retrieval import FallbackRagRetriever


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
            self.assertIn("ISA 손익통산", result[0].content)

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

    def test_youth_future_savings_job_change_prefers_specific_policy_law(self):
        root = Path(__file__).parents[1] / "data" / "rag"
        result = LocalRagRetriever(root).search(
            "청년미래적금 가입 후 중소기업에서 대기업으로 이직하면 어떻게 돼?",
            limit=5,
        )

        # data/rag는 실데이터 코퍼스라 금융꿀팁 문서가 늘어날수록 "가입"처럼 흔한
        # 단어의 키워드 빈도 점수가 근소하게 앞설 수 있다. 그래도 청년미래적금
        # 이직 관련 조문은 상위 결과 안에는 남아 있어야 한다.
        matches = [item for item in result if "청년미래적금" in item.title]
        assert matches, [item.title for item in result]
        assert "가입일 직전 과세기간" in matches[0].content

    def test_vector_results_keep_one_specific_local_official_document(self):
        class VectorRetriever:
            def search(self, query, limit=3):
                from roadmap_agent.domain import Evidence
                return [Evidence("관련성 낮은 벡터 문서", "vector", "db", 900, "ISA")]

        root = Path(__file__).parents[1] / "data" / "rag"
        retriever = FallbackRagRetriever(VectorRetriever(), LocalRagRetriever(root))
        result = retriever.search("청년미래적금 대기업 이직", limit=3)

        assert "청년미래적금" in result[0].title


if __name__ == "__main__":
    unittest.main()
