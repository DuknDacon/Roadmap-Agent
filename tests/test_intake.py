import unittest
from datetime import date

from roadmap_agent.domain import RiskProfile
from roadmap_agent.intake import IntakeRequest, RiskAnswers


class IntakeTest(unittest.TestCase):
    def test_missing_fields_drive_needs_input(self):
        missing = IntakeRequest().missing_initial_fields()
        self.assertIn("birth_date", missing)
        self.assertIn("has_emergency_fund", missing)
        self.assertNotIn("monthly_take_home", missing)
        self.assertIn("household_size", missing)
        self.assertIn("is_married", missing)
        self.assertIn("budget_after_essentials_confirmed", missing)

    def test_employment_details_are_conditional(self):
        unemployed = IntakeRequest(is_employed=False).missing_initial_fields()
        employed = IntakeRequest(is_employed=True).missing_initial_fields()
        self.assertNotIn("employment_type", unemployed)
        self.assertNotIn("is_sme_employee", unemployed)
        self.assertIn("employment_type", employed)
        self.assertIn("is_sme_employee", employed)

    def test_optional_profile_fields_do_not_block_normalization(self):
        intake = IntakeRequest(
            birth_date=date(2000, 1, 1),
            annual_income=36_000_000,
            region_province_code="11",
            region_district_code="110",
            is_employed=False,
            household_size=1,
            is_married=False,
            monthly_budget=800_000,
            target_year=2029,
            target_month=8,
            risk_answers=RiskAnswers(2, 2, 0.35),
            budget_after_essentials_confirmed=True,
            has_emergency_fund=False,
        )
        request = intake.normalize(date(2026, 8, 13))
        self.assertEqual(request.household_size, 1)
        self.assertIsNone(request.monthly_take_home)
        self.assertIsNone(request.target_amount)

    def test_normalizes_ui_input_to_internal_request(self):
        intake = IntakeRequest(
            birth_date=date(2000, 1, 1),
            annual_income=36_000_000,
            monthly_take_home=2_600_000,
            region_province_code="11",
            region_district_code="110",
            employment_type="employee",
            is_employed=True,
            is_sme_employee=True,
            household_size=1,
            dependents=0,
            is_married=False,
            monthly_budget=800_000,
            target_year=2029,
            target_month=8,
            target_amount=30_000_000,
            risk_answers=RiskAnswers(2, 2, 0.35),
            budget_after_essentials_confirmed=True,
            has_emergency_fund=True,
        )
        request = intake.normalize(date(2026, 8, 13))
        self.assertEqual(request.horizon_months, 36)
        self.assertEqual(request.risk_profile, RiskProfile.BALANCED)
        self.assertEqual(request.max_investment_ratio, 0.35)
        self.assertTrue(request.has_emergency_fund)
        self.assertEqual(request.monthly_take_home, 2_600_000)


if __name__ == "__main__":
    unittest.main()
