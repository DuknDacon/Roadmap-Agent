import unittest
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.repositories import (
    SqliteSavingsProductRepository,
    map_savings_row,
    map_welfare_policy_row,
    map_youth_policy_row,
    sqlite_connection_factory,
)


class RepositoriesTest(unittest.TestCase):
    def setUp(self):
        self.request = RoadmapRequest(
            monthly_budget=800_000,
            horizon_months=36,
            target_amount=30_000_000,
            risk_profile=RiskProfile.BALANCED,
            age=28,
            annual_income=40_000_000,
            region_code="11:11110",
        )

    def test_maps_finlife_rate_percent_and_monthly_limit(self):
        product = map_savings_row(
            {
                "dcls_month": "202607", "fin_co_no": "001", "fin_prdt_cd": "A",
                "kor_co_nm": "테스트은행", "fin_prdt_nm": "테스트적금",
                "etc_note": "가입금액 : 월 50만원 이내", "dcls_strt_day": "20260720",
                "source_url": "https://example.test", "intr_rate_type": "S",
                "rsrv_type": "F", "save_trm": "36", "intr_rate": 2.4, "intr_rate2": 3.8,
            }
        )
        self.assertIsNotNone(product)
        assert product is not None
        self.assertEqual(product.maximum_monthly_payment, 500_000)
        self.assertEqual(product.annual_base_rate, 0.024)
        self.assertEqual(product.savings_type_name, "자유적립식")
        self.assertEqual(product.interest_type_name, "단리")

    def test_sqlite_repository_reads_shared_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seedup.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                "CREATE TABLE finlife_saving_base (dcls_month TEXT, fin_co_no TEXT, "
                "fin_prdt_cd TEXT, kor_co_nm TEXT, fin_prdt_nm TEXT, etc_note TEXT, "
                "dcls_strt_day TEXT, source_url TEXT);"
                "CREATE TABLE finlife_saving_option (dcls_month TEXT, fin_co_no TEXT, "
                "fin_prdt_cd TEXT, intr_rate_type TEXT, intr_rate_type_nm TEXT, "
                "rsrv_type TEXT, rsrv_type_nm TEXT, save_trm TEXT, intr_rate REAL, "
                "intr_rate2 REAL);"
                "INSERT INTO finlife_saving_base VALUES "
                "('202608','001','A','테스트은행','공용적금','월 50만원','20260801','https://example.test');"
                "INSERT INTO finlife_saving_option VALUES "
                "('202608','001','A','S','단리','F','자유적립식','36',2.5,3.5);"
            )
            connection.commit()
            connection.close()
            repository = SqliteSavingsProductRepository(sqlite_connection_factory(path))
            products = repository.find_candidates(self.request)
            self.assertEqual(len(products), 1)
            self.assertEqual(products[0].product_name, "공용적금")

    def test_youth_policy_uses_only_explicit_support_formula(self):
        policy = map_youth_policy_row(
            {
                "plcyNo": "P1", "plcyNm": "매칭통장", "plcyExplnCn": "24개월 상품",
                "plcySprtCn": "월 10만원 저축 시 1:1 매칭 지원",
                "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34", "earnMaxAmt": "5000",
                "aplyYmd": "", "source_url": "https://example.test",
            },
            self.request,
            as_of=date(2026, 8, 13),
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertEqual(policy.monthly_limit, 100_000)
        self.assertEqual(policy.estimated_support, 2_400_000)
        self.assertEqual(policy.support_rate, 1.0)

    def test_closed_youth_policy_remains_comparable_but_marks_availability(self):
        policy = map_youth_policy_row(
            {
                "plcyNo": "P2", "plcyNm": "청년미래적금",
                "plcyExplnCn": "3년 만기, 월 50만원 한도",
                "plcySprtCn": "일반형 6%, 우대형 12% 정부기여금 지원",
                "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34",
                "earnEtcCn": "가구 중위소득 200% 이하",
                "etcMttrCn": "금리: 취급 금융기관 자율 결정",
                "aplyYmd": "20260622 ~ 20260703",
            },
            self.request,
            as_of=date(2026, 8, 13),
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertFalse(policy.application_open)
        self.assertEqual(policy.estimated_support, 1_080_000)
        self.assertEqual(policy.monthly_limit, 500_000)
        self.assertEqual(policy.support_rate, 0.06)
        self.assertEqual(policy.preferential_support_rate, 0.12)
        self.assertIn("신청기간은 종료", policy.reason)
        self.assertIn("중위소득", policy.reason)
        self.assertIn("은행이자 미포함", policy.reason)
        self.assertEqual(policy.qualification_status, "needs_input")
        self.assertEqual(policy.benefit_tier, "standard")
        self.assertEqual(
            policy.missing_qualification_fields,
            ("household_monthly_income", "financial_income_taxed", "is_sme_employee"),
        )

    def test_preferential_rate_is_not_confirmed_from_sme_flag_alone(self):
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "household_size": 1,
                "household_monthly_income": 3_000_000,
                "financial_income_taxed": False,
                "is_sme_employee": True,
            }
        )
        policy = map_youth_policy_row(
            {
                "plcyNo": "P3", "plcyNm": "청년미래적금",
                "plcyExplnCn": "3년 만기, 월 50만원 한도",
                "plcySprtCn": "일반형 6%, 우대형 12% 정부기여금 지원",
                "earnEtcCn": "가구 중위소득 200% 이하",
            },
            request,
            as_of=date(2026, 8, 13),
        )

        assert policy is not None
        self.assertEqual(policy.qualification_status, "needs_verification")
        self.assertEqual(policy.benefit_tier, "preferential_possible")
        self.assertEqual(policy.missing_qualification_fields, ())
        self.assertIn("5,128,476원 이하", policy.reason)

    def test_median_income_limit_uses_year_and_household_size(self):
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "household_size": 1,
                "household_monthly_income": 5_200_000,
                "financial_income_taxed": False,
                "is_sme_employee": False,
            }
        )
        policy = map_youth_policy_row(
            {
                "plcyNo": "P5", "plcyNm": "청년미래적금",
                "plcyExplnCn": "3년 만기, 월 50만원 한도",
                "plcySprtCn": "일반형 6%, 우대형 12% 정부기여금 지원",
                "earnEtcCn": "가구 중위소득 200% 이하",
            },
            request,
            as_of=date(2026, 8, 13),
        )

        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertEqual(policy.qualification_status, "ineligible")
        self.assertIn("5,128,476원을 초과", policy.reason)

    def test_financial_income_tax_history_marks_policy_ineligible(self):
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "financial_income_taxed": True,
                "is_sme_employee": False,
            }
        )
        policy = map_youth_policy_row(
            {
                "plcyNo": "P4", "plcyNm": "청년미래적금",
                "plcyExplnCn": "3년 만기, 월 50만원 한도",
                "plcySprtCn": "일반형 6%, 우대형 12% 정부기여금 지원",
            },
            request,
            as_of=date(2026, 8, 13),
        )

        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertEqual(policy.qualification_status, "ineligible")

    def test_welfare_income_condition_is_not_assumed_eligible(self):
        policy = map_welfare_policy_row(
            {
                "servId": "W1", "servNm": "청년통장",
                "tgtrDtlCn": "만 19세~34세, 기준 중위소득 100% 이하",
                "alwServCn": "월 10만원을 3년간 1:1 매칭 지원", "crtrYr": "2026",
            },
            self.request,
            as_of=date(2026, 8, 13),
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertIn("중위소득", policy.reason)


if __name__ == "__main__":
    unittest.main()
