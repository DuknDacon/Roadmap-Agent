import unittest
from types import SimpleNamespace

from roadmap_agent.domain import RiskProfile, RoadmapRequest, RoadmapResult, Scenario
from roadmap_agent.gemini import GeminiEmbeddingClient, GeminiRoadmapExplainer


class FakeModels:
    def __init__(self):
        self.embed_calls = []
        self.generate_calls = []

    def embed_content(self, **kwargs):
        self.embed_calls.append(kwargs)
        contents = kwargs["contents"]
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.1] * 1536) for _ in contents]
        )

    def generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        return SimpleNamespace(text="월 80만 원 적립 시 목표에 조금 부족합니다.")


class GeminiTest(unittest.TestCase):
    def test_embedding_uses_configured_dimension_and_task_types(self):
        models = FakeModels()
        client = SimpleNamespace(models=models)
        embedder = GeminiEmbeddingClient(client=client, dimensions=1536)

        self.assertEqual(len(embedder.embed_query("ISA")), 1536)
        self.assertEqual(len(embedder.embed_documents(["문서1", "문서2"])), 2)
        self.assertEqual(models.embed_calls[0]["config"].task_type, "RETRIEVAL_QUERY")
        self.assertEqual(models.embed_calls[1]["config"].task_type, "RETRIEVAL_DOCUMENT")

    def test_explainer_returns_text_without_changing_result(self):
        models = FakeModels()
        request = RoadmapRequest(800_000, 36, 30_000_000, RiskProfile.BALANCED)
        scenario = Scenario(
            kind="savings",
            title="적금",
            monthly_allocation={"적금": 800_000},
            expected_min=28_800_000,
            expected_base=29_800_000,
            expected_max=29_800_000,
            goal_achievement_rate=99.3,
            principal=28_800_000,
            rationale=["계산 근거"],
            warnings=["조건 확인"],
        )
        result = RoadmapResult(scenario, [], {}, "참고용")
        explainer = GeminiRoadmapExplainer(client=SimpleNamespace(models=models))

        self.assertIn("80만 원", explainer.explain(request, result))
        self.assertEqual(result.recommended.expected_base, 29_800_000)


if __name__ == "__main__":
    unittest.main()
