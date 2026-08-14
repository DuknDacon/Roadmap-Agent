import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "fetch_central_welfare_list.py"
SPEC = importlib.util.spec_from_file_location("fetch_central_welfare_list", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>0</resultCode><resultMessage>SUCCESS</resultMessage></header>
  <body>
    <pageNo>1</pageNo><numOfRows>500</numOfRows><totalCount>2</totalCount>
    <wantedList>
      <servList>
        <servId>WLF00000060</servId><servNm>sample-one</servNm>
        <lifeArray>007</lifeArray><lifeArray>008</lifeArray>
      </servList>
      <servList><servId>WLF00005360</servId><servNm>sample-two</servNm></servList>
    </wantedList>
  </body>
</response>
"""


class FetchCentralWelfareListTest(unittest.TestCase):
    def test_parse_page_preserves_repeated_values(self):
        page = MODULE.parse_page(SAMPLE_XML)

        self.assertEqual(page.total_count, 2)
        self.assertEqual(page.page_no, 1)
        self.assertEqual([row["servId"] for row in page.rows], ["WLF00000060", "WLF00005360"])
        self.assertEqual(page.rows[0]["lifeArray"], ["007", "008"])

    def test_write_outputs_creates_utf8_json_and_csv(self):
        rows = MODULE.parse_page(SAMPLE_XML).rows
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, csv_path = MODULE.write_outputs(rows, Path(temp_dir), "20260813")

            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertIn("WLF00000060", json_path.read_text(encoding="utf-8"))
            self.assertIn("servId", csv_path.read_text(encoding="utf-8-sig"))

    def test_api_error_is_rejected(self):
        error_xml = b"<response><resultCode>30</resultCode><resultMessage>KEY ERROR</resultMessage></response>"
        with self.assertRaises(MODULE.WelfareApiError):
            MODULE.parse_page(error_xml)


if __name__ == "__main__":
    unittest.main()
