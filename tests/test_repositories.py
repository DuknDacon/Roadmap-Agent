import unittest
from datetime import date

from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.repositories import map_savings_row, map_welfare_policy_row, map_youth_policy_row


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
