import unittest

from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.ui_state import apply_conversation_change


class UiStateTest(unittest.TestCase):
    def setUp(self):
        self.request = RoadmapRequest(
            monthly_budget=800_000,
            horizon_months=36,
            target_amount=30_000_000,
            risk_profile=RiskProfile.BALANCED,
        )

    def test_changes_monthly_budget_and_target(self):
        updated, descriptions = apply_conversation_change(
            self.request, "월 저축액을 60만 원, 목표금액은 4천만 원으로 바꿔줘"
        )
        self.assertEqual(updated.monthly_budget, 600_000)
        self.assertEqual(updated.target_amount, 40_000_000)
        self.assertEqual(len(descriptions), 2)

        reversed_order, _ = apply_conversation_change(
            self.request, "목표금액 4천만 원으로 하고 월 저축액은 60만 원으로 바꿔줘"
        )
        self.assertEqual(reversed_order.monthly_budget, 600_000)

    def test_changes_investment_cap_and_emergency_fund(self):
        updated, _ = apply_conversation_change(
            self.request, "투자 비중은 10%로 하고 비상금은 없다고 가정해줘"
        )
        self.assertEqual(updated.max_investment_ratio, 0.1)
        self.assertFalse(updated.has_emergency_fund)

    def test_rejects_unstructured_request(self):
        with self.assertRaises(ValueError):
            apply_conversation_change(self.request, "더 좋은 걸로 해줘")


if __name__ == "__main__":
    unittest.main()
