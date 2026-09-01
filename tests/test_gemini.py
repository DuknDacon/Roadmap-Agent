import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from roadmap_agent.domain import Evidence, RiskProfile, RoadmapRequest, RoadmapResult, Scenario
from roadmap_agent.gemini import (
    GeminiConversationPlanner,
    GeminiEmbeddingClient,
    GeminiPolicyGateExtractor,
    GeminiRoadmapExplainer,
)
from roadmap_agent.conversation import ConversationIntent


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
        return SimpleNamespace(
            text=(
                '{"recommended_reason":"월 80만 원을 안정적으로 적립합니다.",'
                '"alternative_reason":"대안은 예상액이 조금 낮습니다.",'
                '"chat_reply":null}'
            )
        )


class GeminiTest(unittest.TestCase):
    def test_embedding_uses_configured_dimension_and_task_types(self):
        models = FakeModels()
        client = SimpleNamespace(models=models)
        embedder = GeminiEmbeddingClient(client=client, dimensions=1536)

        self.assertEqual(len(embedder.embed_query("ISA")), 1536)
        self.assertEqual(len(embedder.embed_documents(["문서1", "문서2"])), 2)
        self.assertEqual(models.embed_calls[0]["config"].task_type, "RETRIEVAL_QUERY")
        self.assertEqual(models.embed_calls[1]["config"].task_type, "RETRIEVAL_DOCUMENT")

    def test_policy_gate_extractor_parses_gates(self):
        models = SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text=(
                    '{"gates":[{"gate_id":"artist_certification",'
                    '"question":"예술활동증명을 받으셨나요?","hint":"문체부 인증",'
                    '"source_excerpt":"예술활동증명을 완료한 예술인"}]}'
                )
            )
        )
        extractor = GeminiPolicyGateExtractor(client=SimpleNamespace(models=models))

        gates = extractor.extract(policy_id="P1", policy_name="테스트 상품", document="예술활동증명을 완료한 예술인")
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["gate_id"], "artist_certification")

    def test_policy_gate_extractor_strips_markdown_code_fence(self):
        """response_mime_type="application/json"을 줘도 가끔 ```json 코드펜스로
        감싸서 오는 응답을 실제로 관찰했다 — 방어적으로 벗겨내는지 확인."""
        models = SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text='```json\n{"gates":[]}\n```'
            )
        )
        extractor = GeminiPolicyGateExtractor(client=SimpleNamespace(models=models))

        gates = extractor.extract(policy_id="P1", policy_name="테스트 상품", document="문서")
        self.assertEqual(gates, [])

    def test_policy_gate_extractor_raises_clear_error_on_malformed_json(self):
        """상품 하나의 응답이 중간에 잘려도(실측: max_output_tokens 부족으로
        JSON이 잘린 사례) 어떤 상품에서 실패했는지 알 수 있는 에러를 던져야
        한다 — 배치 스크립트가 이 상품만 건너뛰고 계속 진행할 수 있게."""
        models = SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text='{"gates":[{"gate_id":"broken"'  # 잘린 JSON
            )
        )
        extractor = GeminiPolicyGateExtractor(client=SimpleNamespace(models=models))

        with self.assertRaises(RuntimeError) as ctx:
            extractor.extract(policy_id="P404", policy_name="테스트 상품", document="문서")
        self.assertIn("P404", str(ctx.exception))

    def test_policy_gate_extractor_returns_empty_for_blank_document(self):
        extractor = GeminiPolicyGateExtractor(client=SimpleNamespace(models=None))
        self.assertEqual(
            extractor.extract(policy_id="P1", policy_name="테스트 상품", document="   "), []
        )

    def test_explainer_returns_structured_reasons_without_changing_result(self):
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

        explanation = explainer.explain(request, result)
        self.assertIn("80만 원", explanation.recommended_reason)
        self.assertIn("대안", explanation.alternative_reason)
        self.assertIsNone(explanation.chat_reply)
        self.assertEqual(result.recommended.expected_base, 29_800_000)

    def test_conversation_planner_returns_structured_whitelisted_plan(self):
        class PlannerModels:
            def __init__(self):
                self.contents = None

            def generate_content(self, **kwargs):
                self.contents = kwargs["contents"]
                return SimpleNamespace(text=(
                    '{"intent":"condition_change","tools":["condition_parser",'
                    '"roadmap_calculators","ranking"],"structured_changes":'
                    '{"max_investment_ratio":0.1},"clarification_question":null}'
                ))

        models = PlannerModels()
        planner = GeminiConversationPlanner(client=SimpleNamespace(models=models))
        plan = planner.plan(
            RoadmapRequest(800_000, 36, None, RiskProfile.BALANCED),
            "월 80만 원인데 당분간 더 안전하게 가고 싶어 test@example.com",
        )
        self.assertEqual(plan.intent, ConversationIntent.CONDITION_CHANGE)
        self.assertEqual(plan.structured_changes, {"max_investment_ratio": 0.1})
        self.assertNotIn("80만 원", models.contents)
        self.assertNotIn("test@example.com", models.contents)

    def test_financial_qa_falls_back_to_grounded_official_web_search_and_caches(self):
        class WebModels:
            def __init__(self):
                self.calls = []

            def generate_content(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return SimpleNamespace(
                        text='{"answer":"내부 근거 부족","needs_web_search":true}'
                    )
                web = SimpleNamespace(
                    domain="law.go.kr",
                    title="국가법령정보센터",
                    uri="https://www.law.go.kr/example",
                )
                metadata = SimpleNamespace(
                    grounding_chunks=[SimpleNamespace(web=web)]
                )
                return SimpleNamespace(
                    text="가입 자격은 가입 시점을 기준으로 확인합니다.",
                    candidates=[SimpleNamespace(grounding_metadata=metadata)],
                )

        models = WebModels()
        explainer = GeminiRoadmapExplainer(
            client=SimpleNamespace(models=models), web_search_enabled=True
        )
        evidence = [Evidence("내부 문서", "", "doc", 1, "관련 규정이 부족합니다.")]

        first = explainer.answer_financial_question("이직하면 어떻게 돼?", evidence)
        second = explainer.answer_financial_question("이직하면 어떻게 돼?", evidence)

        self.assertIn("가입 자격", first)
        self.assertIn("국가법령정보센터", first)
        self.assertEqual(first, second)
        self.assertEqual(len(models.calls), 2)

    def test_conflicting_evidence_is_ordered_by_source_priority_with_instruction(self):
        class Models:
            def __init__(self):
                self.calls = []

            def generate_content(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(text='{"answer":"정리된 답변","needs_web_search":false}')

        models = Models()
        explainer = GeminiRoadmapExplainer(client=SimpleNamespace(models=models))
        # 낮은 우선순위(교육자료) 근거를 먼저 넣어도, 법령 근거가 sources에서
        # 앞으로 와야 LLM이 그쪽을 우선하도록 유도된다.
        evidence = [
            Evidence(
                "금융꿀팁 교육자료", "", "tip.md", 5, "일반적으로 이렇게 알려져 있다.",
                source_type="finance_education",
            ),
            Evidence(
                "청년미래적금 법률", "", "law.md", 10, "법률상 요건은 다음과 같다.",
                source_type="law",
            ),
        ]

        explainer.answer_financial_question("법과 꿀팁이 다르면?", evidence)

        sent = models.calls[0]
        contents = sent["contents"]
        self.assertLess(contents.index('"청년미래적금 법률"'), contents.index('"금융꿀팁 교육자료"'))
        instruction = sent["config"].system_instruction
        self.assertIn("law", instruction)
        self.assertIn("finance_education", instruction)

    def test_web_search_rejects_non_official_grounding_sources(self):
        class WebModels:
            def generate_content(self, **kwargs):
                web = SimpleNamespace(
                    domain="example-blog.test",
                    title="개인 블로그",
                    uri="https://example-blog.test/post",
                )
                return SimpleNamespace(
                    text="확정적인 답변",
                    candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
                        grounding_chunks=[SimpleNamespace(web=web)]
                    ))],
                )

        explainer = GeminiRoadmapExplainer(
            client=SimpleNamespace(models=WebModels()), web_search_enabled=True
        )
        answer = explainer.answer_financial_question("최신 정책은?", [])

        self.assertIn("공식 웹 출처", answer)
        self.assertNotIn("확정적인 답변", answer)

    def test_failed_web_search_preserves_partial_rag_answer(self):
        class Models:
            def __init__(self):
                self.calls = 0

            def generate_content(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(text=(
                        '{"answer":"비과세 가입요건은 가입일 직전 과세기간 기준입니다.",'
                        '"needs_web_search":true}'
                    ))
                return SimpleNamespace(text="", candidates=[])

        explainer = GeminiRoadmapExplainer(
            client=SimpleNamespace(models=Models()), web_search_enabled=True
        )
        evidence = [Evidence("청년미래적금 법률", "", "law", 10, "가입일 직전 과세기간")]

        answer = explainer.answer_financial_question("대기업으로 이직하면?", evidence)

        self.assertIn("가입일 직전 과세기간", answer)
        self.assertIn("이 부분은 확정할 수 없습니다", answer)

    def test_web_search_quota_is_shared_across_instances_via_db(self):
        """다중 워커·인스턴스 시나리오: 같은 DB를 보는 서로 다른 explainer도 월 한도를 공유해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "shared.sqlite")
            explainer_a = GeminiRoadmapExplainer(
                client=SimpleNamespace(models=FakeModels()),
                web_search_enabled=True,
                web_search_monthly_limit=2,
                usage_db_path=db_path,
            )
            explainer_b = GeminiRoadmapExplainer(
                client=SimpleNamespace(models=FakeModels()),
                web_search_enabled=True,
                web_search_monthly_limit=2,
                usage_db_path=db_path,
            )

            self.assertTrue(explainer_a._try_consume_web_search_quota())
            self.assertTrue(explainer_b._try_consume_web_search_quota())
            # 두 인스턴스가 합쳐서 한도(2)에 도달했으므로 어느 쪽이 다시 시도해도 실패해야 한다.
            self.assertFalse(explainer_a._try_consume_web_search_quota())
            self.assertFalse(explainer_b._try_consume_web_search_quota())

    def test_web_search_quota_falls_back_to_process_memory_without_db(self):
        explainer = GeminiRoadmapExplainer(
            client=SimpleNamespace(models=FakeModels()),
            web_search_enabled=True,
            web_search_monthly_limit=1,
            usage_db_path=None,
        )
        self.assertTrue(explainer._try_consume_web_search_quota())
        self.assertFalse(explainer._try_consume_web_search_quota())


if __name__ == "__main__":
    unittest.main()
