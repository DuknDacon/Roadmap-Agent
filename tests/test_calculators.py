import unittest

from roadmap_agent.calculators import (
    achievement_rate,
    allocation_for,
    future_value_of_monthly_payments,
    investment_projection,
    investment_cap,
    savings_product_projection,
    required_monthly_for_savings_product,
)
from roadmap_agent.domain import RiskProfile


class CalculatorsTest(unittest.TestCase):
    def test_savings_product_projection_applies_tax_and_preferential_rate(self):
        result = savings_product_projection(500_000, 12, 0.03, 0.05)
        self.assertEqual(result.contributed, 6_000_000)
        self.assertGreater(result.base, result.contributed)
        self.assertGreater(result.maximum, result.base)

    def test_required_monthly_uses_same_product_formula(self):
        required = required_monthly_for_savings_product(30_000_000, 36, 0.028)
        projection = savings_product_projection(required, 36, 0.028, 0.028)
        self.assertGreaterEqual(projection.base, 30_000_000)
        previous = savings_product_projection(required - 1, 36, 0.028, 0.028)
        self.assertLess(previous.base, 30_000_000)

    def test_zero_rate_equals_contributions(self):
        self.assertEqual(future_value_of_monthly_payments(500_000, 12, 0), 6_000_000)

    def test_investment_projection_keeps_range_order(self):
        result = investment_projection(500_000, 36, RiskProfile.BALANCED)
        self.assertLess(result.minimum, result.base)
        self.assertLess(result.base, result.maximum)

    def test_no_emergency_fund_uses_safer_allocation(self):
        self.assertEqual(allocation_for(RiskProfile.AGGRESSIVE, False), (85, 15))

    def test_achievement_rate(self):
        self.assertEqual(achievement_rate(15_000_000, 30_000_000), 50.0)

    def test_short_horizon_excludes_investment(self):
        self.assertEqual(investment_cap(RiskProfile.AGGRESSIVE, 11, None), 0)

    def test_user_investment_cap_wins(self):
        self.assertEqual(investment_cap(RiskProfile.AGGRESSIVE, 60, 0.32), 0.30)


if __name__ == "__main__":
    unittest.main()
