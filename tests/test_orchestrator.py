import tempfile
import unittest
from pathlib import Path

from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.orchestrator import run_roadmap
from roadmap_agent.ports import PolicyBenefit, SavingsProduct


class PolicyRepositoryStub:
    def find_candidates(self, request):
        return [
            PolicyBenefit(
                policy_id="P1",
                name="검증 정책",
                eligible=True,
                monthly_limit=500_000,
                estimated_support=3_600_000,
                maturity_months=36,
                source_url="https://example.test/policy",
                effective_date="2026-01-01",
                reason="목 데이터 자격 충족",
                support_rate=0.06,
                preferential_support_rate=0.12,
            )
        ]


class SavingsRepositoryStub:
    def find_candidates(self, request):
        return [
            SavingsProduct(
                product_id="S1",
                company_name="테스트은행",
                product_name="실데이터적금",
                annual_base_rate=0.03,
                annual_preferential_rate=0.05,
                maximum_monthly_payment=500_000,
                term_months=36,
                tax_rate=0.154,
                source_url="https://example.test/savings",
                effective_date="2026-07-20",
            )
        ]


class MultipleSavingsRepositoryStub:
    def find_candidates(self, request):
        return [
            SavingsProduct(
                product_id=f"S{index}",
                company_name=f"테스트은행{index}",
                product_name=f"테스트적금{index}",
                annual_base_rate=rate,
                annual_preferential_rate=rate,
                maximum_monthly_payment=800_000,
                term_months=36,
                tax_rate=0.154,
                source_url=f"https://example.test/savings/{index}",
                effective_date="2026-07-20",
            )
            for index, rate in enumerate((0.05, 0.04, 0.03), start=1)
        ]


class DuplicateOptionsSavingsRepositoryStub:
    def find_candidates(self, request):
        return [
            SavingsProduct(
                product_id="202607:001:SAME:S:F:36",
                company_name="같은은행",
                product_name="같은적금",
                annual_base_rate=0.028,
                annual_preferential_rate=0.028,
                maximum_monthly_payment=800_000,
                term_months=36,
                tax_rate=0.154,
                source_url="https://example.test/same",
                effective_date="2026-07-20",
            ),
            SavingsProduct(
                product_id="202607:001:SAME:S:S:36",
                company_name="같은은행",
                product_name="같은적금",
                annual_base_rate=0.0265,
                annual_preferential_rate=0.0265,
                maximum_monthly_payment=800_000,
                term_months=36,
                tax_rate=0.154,
                source_url="https://example.test/same",
                effective_date="2026-07-20",
            ),
            SavingsProduct(
                product_id="202607:002:OTHER:S:S:36",
                company_name="다른은행",
                product_name="다른적금",
                annual_base_rate=0.025,
                annual_preferential_rate=0.025,
                maximum_monthly_payment=800_000,
                term_months=36,
                tax_rate=0.154,
                source_url="https://example.test/other",
                effective_date="2026-07-20",
            ),
        ]


class OrchestratorTest(unittest.TestCase):
    def test_returns_recommendation_and_one_alternative_without_llm(self):
        request = RoadmapRequest(
            monthly_budget=800_000,
            horizon_months=36,
            target_amount=30_000_000,
            risk_profile=RiskProfile.BALANCED,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(request, Path(temp_dir))
        self.assertEqual(1 + len(result.alternatives), 2)
        self.assertNotEqual(result.recommended.kind, "policy")
        self.assertEqual(sum(result.recommended.monthly_allocation.values()), 800_000)
        self.assertTrue(result.assumptions["investment_values_are_scenarios_not_forecasts"])

    def test_policy_scenario_only_exists_with_eligible_structured_data(self):
        request = RoadmapRequest(
            800_000,
            36,
            None,
            RiskProfile.CONSERVATIVE,
            is_sme_employee=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(request, Path(temp_dir), PolicyRepositoryStub())
        kinds = {result.recommended.kind, *(item.kind for item in result.alternatives)}
        self.assertIn("policy", kinds)
        policy = next(
            item
            for item in (result.recommended, *result.alternatives)
            if item.kind == "policy"
        )
        self.assertEqual(policy.expected_min, 29_880_000)
        self.assertEqual(policy.expected_max, 30_960_000)

    def test_savings_scenario_includes_unallocated_cash_in_total(self):
        request = RoadmapRequest(800_000, 36, None, RiskProfile.CONSERVATIVE)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(
                request,
                Path(temp_dir),
                savings_repository=SavingsRepositoryStub(),
            )
        savings = next(
            item
            for item in (result.recommended, *result.alternatives)
            if item.kind == "savings"
        )
        self.assertEqual(savings.principal, 28_800_000)
        self.assertEqual(savings.unallocated_cash, 10_800_000)
        self.assertGreater(savings.expected_base, 28_800_000)

    def test_rejects_invalid_budget(self):
        request = RoadmapRequest(0, 12, None, RiskProfile.CONSERVATIVE)
        with self.assertRaises(ValueError):
            run_roadmap(request)

    def test_structured_savings_repository_replaces_mock_rate(self):
        request = RoadmapRequest(800_000, 36, None, RiskProfile.CONSERVATIVE)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(
                request,
                Path(temp_dir),
                savings_repository=SavingsRepositoryStub(),
            )
        scenarios = [result.recommended, *result.alternatives]
        savings = next(item for item in scenarios if item.kind == "savings")
        self.assertIn("실데이터적금", savings.title)
        self.assertEqual(savings.data_status, "structured_finlife_candidate")
        self.assertEqual(savings.evidence[0].source_url, "https://example.test/savings")
        self.assertIsNone(savings.additional_months)
        self.assertTrue(result.assumptions["structured_product_data_connected"])
        self.assertEqual(
            result.assumptions["savings_calculation"],
            "structured_product_rate_and_tax",
        )

    def test_zero_investment_cap_excludes_investment_and_balanced_scenarios(self):
        request = RoadmapRequest(
            800_000,
            36,
            None,
            RiskProfile.BALANCED,
            max_investment_ratio=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(
                request,
                Path(temp_dir),
                savings_repository=SavingsRepositoryStub(),
            )
        scenarios = [result.recommended, *result.alternatives]
        self.assertEqual({item.kind for item in scenarios}, {"savings"})
        self.assertNotIn("균형", " ".join(item.title for item in scenarios))

    def test_zero_investment_cap_reranks_multiple_savings_candidates(self):
        request = RoadmapRequest(
            800_000,
            36,
            None,
            RiskProfile.BALANCED,
            max_investment_ratio=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(
                request,
                Path(temp_dir),
                savings_repository=MultipleSavingsRepositoryStub(),
            )
        scenarios = [result.recommended, *result.alternatives]
        self.assertEqual(len(scenarios), 2)
        self.assertTrue(all(item.kind == "savings" for item in scenarios))
        self.assertNotEqual(scenarios[0].title, scenarios[1].title)

    def test_duplicate_options_of_same_product_are_not_two_recommendations(self):
        request = RoadmapRequest(
            800_000,
            36,
            None,
            RiskProfile.BALANCED,
            max_investment_ratio=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_roadmap(
                request,
                Path(temp_dir),
                savings_repository=DuplicateOptionsSavingsRepositoryStub(),
            )
        titles = [result.recommended.title, *(item.title for item in result.alternatives)]
        self.assertEqual(sum("같은적금" in title for title in titles), 1)
        self.assertEqual(sum("다른적금" in title for title in titles), 1)


if __name__ == "__main__":
    unittest.main()
