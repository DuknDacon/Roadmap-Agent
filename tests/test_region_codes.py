import json
import tempfile
import unittest
from pathlib import Path

from roadmap_agent.region_codes import filter_regions, load_region_codes


class RegionCodesTest(unittest.TestCase):
    def test_loads_and_filters_names_while_preserving_codes(self):
        payload = {
            "regions": [
                {
                    "code": "11",
                    "name": "서울특별시",
                    "districts": [{"code": "11110", "name": "종로구"}],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "regions.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            regions = load_region_codes(path)
        self.assertEqual(filter_regions(regions, "서울")[0].code, "11")
        self.assertEqual(filter_regions(regions[0].districts, "종로")[0].code, "11110")


if __name__ == "__main__":
    unittest.main()
