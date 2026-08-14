import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "fetch_feature2_samples.py"
SPEC = importlib.util.spec_from_file_location("fetch_feature2_samples", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FetchFeature2SamplesTest(unittest.TestCase):
    def test_finlife_options_are_joined_to_product(self):
        base = [
            {"dcls_month": "202607", "fin_co_no": "A", "fin_prdt_cd": "P1"},
            {"dcls_month": "202607", "fin_co_no": "B", "fin_prdt_cd": "P2"},
        ]
        options = [
            {
                "dcls_month": "202607",
                "fin_co_no": "A",
                "fin_prdt_cd": "P1",
                "save_trm": "12",
            }
        ]

        result = MODULE.select_finlife_samples(base, options, 2)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["products"][0]["options"][0]["save_trm"], "12")

    def test_youth_filter_keeps_approved_relevant_policy(self):
        rows = [
            {"plcyNo": "1", "plcyNm": "청년 자산형성", "plcyAprvSttsCd": "0044002"},
            {"plcyNo": "2", "plcyNm": "청년 적금", "plcyAprvSttsCd": "0044003"},
            {"plcyNo": "3", "plcyNm": "문화 행사", "plcyAprvSttsCd": "0044002"},
        ]

        selected = MODULE.select_youth_samples(rows, 3, ("자산형성", "적금"))

        self.assertEqual([row["plcyNo"] for row in selected], ["1"])

    def test_welfare_detail_preserves_repeated_fields(self):
        payload = b"""<response><resultCode>0</resultCode><resultMessage>SUCCESS</resultMessage>
        <wantedDtl><servId>W1</servId><servNm>sample</servNm>
        <lifeArray>007</lifeArray><lifeArray>008</lifeArray></wantedDtl></response>"""

        result = MODULE.parse_welfare_detail(payload)

        self.assertEqual(result["servId"], "W1")
        self.assertEqual(result["lifeArray"], ["007", "008"])

    def test_welfare_list_selection_prioritizes_asset_building(self):
        rows = [
            {"servId": "loan", "servNm": "청년 대출보증", "servDgst": "금융 지원"},
            {"servId": "general", "servNm": "청년 문화지원", "servDgst": "문화생활"},
            {"servId": "saving", "servNm": "청년도약계좌", "servDgst": "자산형성 적금"},
            {"servId": "match", "servNm": "청년내일저축계좌", "servDgst": "저축 지원"},
        ]

        selected = MODULE.select_welfare_samples(rows, 2)

        self.assertEqual([row["servId"] for row in selected], ["saving", "match"])

    def test_parse_welfare_list_returns_total(self):
        payload = b"""<response><resultCode>0</resultCode><totalCount>2</totalCount>
        <wantedList><servList><servId>W1</servId><servNm>one</servNm></servList>
        <servList><servId>W2</servId><servNm>two</servNm></servList></wantedList></response>"""

        rows, total = MODULE.parse_welfare_list(payload)

        self.assertEqual(total, 2)
        self.assertEqual([row["servId"] for row in rows], ["W1", "W2"])


if __name__ == "__main__":
    unittest.main()
