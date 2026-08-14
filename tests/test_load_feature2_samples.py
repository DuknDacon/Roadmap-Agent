import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "load_feature2_samples.py"
SPEC = importlib.util.spec_from_file_location("load_feature2_samples", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LoadFeature2SamplesTest(unittest.TestCase):
    def test_finlife_builds_base_and_option_upserts(self):
        document = {
            "products": [{
                "base": {
                    "dcls_month": "202607", "fin_co_no": "A", "fin_prdt_cd": "P",
                    "kor_co_nm": "은행", "fin_prdt_nm": "적금",
                },
                "options": [{
                    "dcls_month": "202607", "fin_co_no": "A", "fin_prdt_cd": "P",
                    "intr_rate_type": "S", "rsrv_type": "F", "save_trm": "12",
                    "intr_rate": 3.0, "intr_rate2": 4.0,
                }],
            }]
        }

        sql = "\n".join(MODULE.build_finlife_sql(document))

        self.assertIn("raw.finlife_saving_base", sql)
        self.assertIn("raw.finlife_saving_option", sql)
        self.assertIn("ON CONFLICT", sql)

    def test_youth_uses_official_url_fallback_and_skips_unapproved(self):
        approved = {field: "" for field in MODULE.YOUTH_FIELDS}
        approved.update({
            "plcyNo": "Y1", "plcyNm": "정책", "plcyAprvSttsCd": "0044002",
            "refUrlAddr1": "https://official.example",
        })
        rejected = dict(approved, plcyNo="Y2", plcyAprvSttsCd="0044003")

        sql = MODULE.build_youth_sql({"policies": [approved, rejected]})

        self.assertEqual(len(sql), 1)
        self.assertIn("https://official.example", sql[0])
        self.assertNotIn("Y2", sql[0])

    def test_welfare_merges_only_required_list_fields(self):
        document = {
            "selection": [{
                "servId": "W1", "servNm": "목록 이름",
                "servDtlLink": "https://welfare.example/W1", "lifeArray": "청년",
            }],
            "services": [{"servId": "W1", "servNm": "상세 이름", "crtrYr": "2026"}],
        }

        sql = MODULE.build_welfare_sql(document)[0]

        self.assertIn('"servDtlLink"', sql)
        self.assertIn("https://welfare.example/W1", sql)
        self.assertNotIn('"lifeArray"', sql)


if __name__ == "__main__":
    unittest.main()
