import unittest
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from roadmap_agent.agents import policy_candidate_scenarios
from roadmap_agent.domain import RiskProfile, RoadmapRequest
from roadmap_agent.dynamic_gates import DynamicGate, DynamicGateRegistry
from roadmap_agent.repositories import (
    SqliteSavingsProductRepository,
    map_savings_row,
    map_welfare_policy_row,
    map_youth_policy_row,
    sqlite_connection_factory,
)


class _FakeRetriever:
    def search(self, query, limit=3):
        return []


class _FakePolicyRepository:
    def __init__(self, policies):
        self._policies = policies

    def find_candidates(self, request):
        return self._policies


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

    def _row_with_dynamic_gate(self):
        return {
            "plcyNo": "P1", "plcyNm": "매칭통장", "plcyExplnCn": "24개월 상품",
            "plcySprtCn": "월 10만원 저축 시 1:1 매칭 지원",
            "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34",
            "aplyYmd": "", "source_url": "https://example.test",
        }

    def test_dynamic_gate_unanswered_adds_composite_missing_field(self):
        """LLM이 발견한 게이트(4개 하드코딩 필드를 넘어서는 조건)에 사용자가 아직
        답하지 않았으면 "policy_id:gate_id" 합성 키가 missing_qualification_fields에
        들어가야 한다 — 기존 4개 필드와 같은 방식(union 후 사전 체크에서 되묻기)."""
        registry = DynamicGateRegistry(
            {
                "P1": [
                    DynamicGate(
                        policy_id="P1",
                        gate_id="artist_certification",
                        question="예술활동증명을 받으셨나요?",
                        hint="문체부 인증입니다.",
                    )
                ]
            }
        )
        policy = map_youth_policy_row(
            self._row_with_dynamic_gate(), self.request, as_of=date(2026, 8, 13),
            gate_registry=registry,
        )
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertIn("P1:artist_certification", policy.missing_qualification_fields)
        self.assertEqual(policy.qualification_status, "needs_input")

    def test_dynamic_gate_answered_false_makes_ineligible(self):
        """LLM은 질문만 만들고, 답변으로 eligible을 계산하는 건 항상 이 결정론적
        코드다 — "아니오" 답변이면 eligible=False로 바뀌어야 한다."""
        registry = DynamicGateRegistry(
            {
                "P1": [
                    DynamicGate(
                        policy_id="P1",
                        gate_id="artist_certification",
                        question="예술활동증명을 받으셨나요?",
                        hint="",
                    )
                ]
            }
        )
        request = RoadmapRequest(
            **{**self.request.__dict__, "dynamic_gate_answers": {"P1:artist_certification": False}}
        )
        policy = map_youth_policy_row(
            self._row_with_dynamic_gate(), request, as_of=date(2026, 8, 13),
            gate_registry=registry,
        )
        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertEqual(policy.qualification_status, "ineligible")
        self.assertNotIn("P1:artist_certification", policy.missing_qualification_fields)

    def test_dynamic_gate_answered_true_keeps_eligible(self):
        registry = DynamicGateRegistry(
            {
                "P1": [
                    DynamicGate(
                        policy_id="P1",
                        gate_id="artist_certification",
                        question="예술활동증명을 받으셨나요?",
                        hint="",
                    )
                ]
            }
        )
        request = RoadmapRequest(
            **{**self.request.__dict__, "dynamic_gate_answers": {"P1:artist_certification": True}}
        )
        policy = map_youth_policy_row(
            self._row_with_dynamic_gate(), request, as_of=date(2026, 8, 13),
            gate_registry=registry,
        )
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertNotIn("P1:artist_certification", policy.missing_qualification_fields)

    def test_dynamic_gate_first_false_answer_stops_asking_remaining_gates(self):
        """상품 하나에 게이트가 여러 개일 때, 앞 게이트에서 이미 탈락이 확정되면
        같은 상품의 나머지 게이트는 더 묻지 않는다 — 이미 못 받는 상품인데
        질문만 계속 쌓이는 걸 막는다(인천 청년월세 지원사업처럼 게이트가
        6~8개인 실제 사례에서 발견된 문제)."""
        registry = DynamicGateRegistry(
            {
                "P1": [
                    DynamicGate(
                        policy_id="P1", gate_id="gate_a", question="조건 A를 만족하나요?", hint=""
                    ),
                    DynamicGate(
                        policy_id="P1", gate_id="gate_b", question="조건 B를 만족하나요?", hint=""
                    ),
                ]
            }
        )
        request = RoadmapRequest(
            **{**self.request.__dict__, "dynamic_gate_answers": {"P1:gate_a": False}}
        )
        policy = map_youth_policy_row(
            self._row_with_dynamic_gate(), request, as_of=date(2026, 8, 13),
            gate_registry=registry,
        )
        assert policy is not None
        self.assertFalse(policy.eligible)
        # gate_a에서 이미 탈락 확정 — 아직 답 안 한 gate_b는 물어보지 않는다.
        self.assertNotIn("P1:gate_b", policy.missing_qualification_fields)

    def test_legacy_field_exclusion_skips_dynamic_gate_question(self):
        """나이 조건처럼 온보딩만으로 항상 판정되는 하드 조건이 이미 이 상품을
        탈락시켰으면, 동적 게이트 질문도 더 묻지 않는다."""
        registry = DynamicGateRegistry(
            {
                "P1": [
                    DynamicGate(
                        policy_id="P1", gate_id="gate_a", question="조건 A를 만족하나요?", hint=""
                    ),
                ]
            }
        )
        request = RoadmapRequest(**{**self.request.__dict__, "age": 50})  # min19~max34 불충족
        policy = map_youth_policy_row(
            self._row_with_dynamic_gate(), request, as_of=date(2026, 8, 13),
            gate_registry=registry,
        )
        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertNotIn("P1:gate_a", policy.missing_qualification_fields)

    def test_youth_policy_prefers_application_url_over_reference_url(self):
        """실제 데이터: aplyUrlAddr(신청 URL)이 있으면 refUrlAddr(참고 URL, 대개
        소관기관 홈페이지)보다 우선해야 사용자가 실제 신청 페이지로 이동한다."""
        policy = map_youth_policy_row(
            {
                "plcyNo": "P3", "plcyNm": "산림창업가 캠프",
                "plcyExplnCn": "24개월 상품",
                "plcySprtCn": "월 10만원 저축 시 1:1 매칭 지원",
                "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34",
                "aplyUrlAddr": "https://forms.gle/example",
                "refUrlAddr1": "https://www.kofpi.or.kr",
                "aplyYmd": "",
            },
            self.request,
            as_of=date(2026, 8, 13),
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual(policy.source_url, "https://forms.gle/example")

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

    def test_preferential_rate_applies_once_sme_flag_confirmed(self):
        """운영기관의 최종 확인이 남아있다는 안내(qualification_status)는 계속 뜨지만,
        사용자가 중소기업 재직 여부를 답한 이상 실제 점수 계산에는 우대형 요율을
        반영해야 한다 — 그래야 우대형 적용 시 목표를 달성할 수 있는 상품이 순위·
        후보 필터에서 부당하게 밀려나지 않는다."""
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
        # 라벨은 여전히 "확인 필요"로 정직하게 유지된다 — 바뀐 건 benefit_tier뿐.
        self.assertEqual(policy.qualification_status, "needs_verification")
        self.assertEqual(policy.benefit_tier, "preferential")
        self.assertEqual(policy.missing_qualification_fields, ())
        self.assertIn("5,128,476원 이하", policy.reason)

    def test_preferential_rate_not_applied_when_ineligible(self):
        """is_sme_employee=True여도 다른 사유로 이미 자격이 없는 상품에까지
        우대형 요율을 적용해서는 안 된다."""
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "household_size": 1,
                "household_monthly_income": 3_000_000,
                "financial_income_taxed": True,
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
        self.assertFalse(policy.eligible)
        self.assertEqual(policy.benefit_tier, "preferential_possible")

    def test_preferential_rate_changes_scenario_scoring_not_just_the_flag(self):
        """benefit_tier만 바뀌고 실제 점수 계산에는 반영되지 않으면 의미가 없다 —
        policy_candidate_scenarios가 만드는 시나리오의 예상액이 실제로 우대형
        12% 요율을 쓰는지까지 끝까지 확인한다."""
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "monthly_budget": 500_000,
                "horizon_months": 24,
                "household_size": 1,
                "household_monthly_income": 3_000_000,
                "financial_income_taxed": False,
                "is_sme_employee": True,
            }
        )
        policy = map_youth_policy_row(
            {
                "plcyNo": "P3", "plcyNm": "청년미래적금",
                "plcyExplnCn": "24개월 만기, 월 50만원 한도",
                "plcySprtCn": "일반형 6%, 우대형 12% 정부기여금 지원",
                "earnEtcCn": "가구 중위소득 200% 이하",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        assert policy is not None
        self.assertEqual(policy.benefit_tier, "preferential")

        scenarios = policy_candidate_scenarios(
            request, _FakeRetriever(), _FakePolicyRepository([policy])
        )

        self.assertEqual(len(scenarios), 1)
        monthly = min(request.monthly_budget, policy.monthly_limit)
        principal = request.monthly_budget * request.horizon_months
        preferential_support = round(monthly * policy.maturity_months * 0.12)
        standard_support = round(monthly * policy.maturity_months * 0.06)
        self.assertEqual(scenarios[0].expected_base, principal + preferential_support)
        self.assertNotEqual(scenarios[0].expected_base, principal + standard_support)

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

    def test_welfare_median_income_condition_asks_instead_of_assuming_ineligible(self):
        """이전엔 "중위소득" 문구만 있으면 실제 소득 비교 없이 무조건 eligible=False로
        고정돼, welfare_service 레코드가 어떤 프로필로도 후보가 될 수 없었다(죽은
        데이터). 이제는 youth_policy와 같은 방식으로 실제 값이 없으면 확인을
        요청(needs_input)할 뿐, 자격을 임의로 단정하지 않는다."""
        request = RoadmapRequest(**{**self.request.__dict__, "age": 28})
        policy = map_welfare_policy_row(
            {
                "servId": "W1", "servNm": "청년통장",
                "tgtrDtlCn": "만 19세~34세, 기준 중위소득 100% 이하",
                "alwServCn": "월 10만원을 3년간 1:1 매칭 지원", "crtrYr": "2026",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertEqual(policy.qualification_status, "needs_input")
        self.assertIn("household_monthly_income", policy.missing_qualification_fields)

    def test_welfare_median_income_condition_uses_real_comparison_once_income_known(self):
        request = RoadmapRequest(
            **{
                **self.request.__dict__,
                "age": 28,
                "household_size": 1,
                "household_monthly_income": 5_200_000,
            }
        )
        policy = map_welfare_policy_row(
            {
                "servId": "W1", "servNm": "청년통장",
                "tgtrDtlCn": "만 19세~34세, 기준 중위소득 100% 이하",
                "alwServCn": "월 10만원을 3년간 1:1 매칭 지원", "crtrYr": "2026",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        assert policy is not None
        self.assertFalse(policy.eligible)
        self.assertEqual(policy.qualification_status, "ineligible")
        self.assertIn("초과", policy.reason)

    def test_welfare_special_target_group_is_flagged_for_verification_not_excluded(self):
        """북한이탈주민/농업인/어업인/무주택 여부를 물어볼 프로필 필드가 아직 없어,
        예전처럼 무조건 배제하지 않고 "확인 필요" 상태로만 남겨 후보에서 사라지지
        않게 한다(영구 배제 방지)."""
        request = RoadmapRequest(**{**self.request.__dict__, "age": 28})
        policy = map_welfare_policy_row(
            {
                "servId": "W2", "servNm": "북한이탈주민 자산형성지원제도",
                "tgtrDtlCn": "만 19세~34세, 북향민",
                "alwServCn": "월 10만원을 3년간 1:1 매칭 지원", "crtrYr": "2026",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        assert policy is not None
        self.assertTrue(policy.eligible)
        self.assertEqual(policy.qualification_status, "needs_verification")
        self.assertIn("특수조건", policy.reason)

    def test_stipend_program_is_flagged_not_treated_as_matching_savings(self):
        """"사회연대경제 청년일경험"처럼 "월 234만원 참여수당 지급" 같은 근로
        참여수당형 정책은 저축액에 비례해 정부가 보태주는 적립형 상품이 아니다.
        estimated_support가 "최대 234만원" 패턴으로 잘못 추출돼 정부기여금인
        것처럼 안내되는 걸 막기 위해 is_stipend_program=True로 표시돼야 한다."""
        request = RoadmapRequest(**{**self.request.__dict__, "age": 28})
        policy = map_youth_policy_row(
            {
                "plcyNo": "P9", "plcyNm": "사회연대경제 청년일경험",
                "plcyExplnCn": "사회연대경제 조직에서 5개월간 일경험",
                "plcySprtCn": "월 234만원 참여수당 지급\n - 월 최대 234만원 지급(세전, 주 40시간 기준)",
                "addAplyQlfcCndCn": "",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        assert policy is not None
        self.assertTrue(policy.is_stipend_program)

    def test_stipend_program_excluded_from_savings_scenarios(self):
        """policy_candidate_scenarios(저축 로드맵 시나리오 빌더)는 참여수당형
        정책을 후보에서 제외해야 한다 — eligible=True인데도 제외되는지 확인한다."""
        request = RoadmapRequest(**{**self.request.__dict__, "monthly_budget": 500_000})
        stipend_policy = map_youth_policy_row(
            {
                "plcyNo": "P9", "plcyNm": "사회연대경제 청년일경험",
                "plcyExplnCn": "사회연대경제 조직에서 5개월간 일경험",
                "plcySprtCn": "월 234만원 참여수당 지급\n - 월 최대 234만원 지급(세전, 주 40시간 기준)",
                "addAplyQlfcCndCn": "",
            },
            request,
            as_of=date(2026, 8, 13),
        )
        assert stipend_policy is not None
        self.assertTrue(stipend_policy.eligible)
        self.assertTrue(stipend_policy.is_stipend_program)

        scenarios = policy_candidate_scenarios(
            request, _FakeRetriever(), _FakePolicyRepository([stipend_policy])
        )
        self.assertEqual(scenarios, [])


if __name__ == "__main__":
    unittest.main()
