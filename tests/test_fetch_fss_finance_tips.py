import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "fetch_fss_finance_tips.py"
SPEC = importlib.util.spec_from_file_location("fetch_fss_finance_tips", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>1</resultCode><resultMsg>SUCCESS</resultMsg></header>
  <body><totalCount>2</totalCount><items>
    <item><seq>101</seq><title>ISA &amp; ETF</title><content><![CDATA[<p>분산투자 안내</p>]]></content><regDate>2026-01-02</regDate></item>
    <item><seq>102</seq><title>보험 안내</title><content>보험 가입 안내</content></item>
  </items></body>
</response>""".encode("utf-8")


class FetchFssFinanceTipsTest(unittest.TestCase):
    def test_load_env_file_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("FSS_OPEN_API_KEY=file-key\nNEW_TEST_KEY=value\n", encoding="utf-8")
            previous_api_key = os.environ.get("FSS_OPEN_API_KEY")
            previous_test_key = os.environ.get("NEW_TEST_KEY")
            os.environ["FSS_OPEN_API_KEY"] = "environment-key"
            os.environ.pop("NEW_TEST_KEY", None)
            try:
                MODULE.load_env_file(path)
                self.assertEqual(os.environ["FSS_OPEN_API_KEY"], "environment-key")
                self.assertEqual(os.environ["NEW_TEST_KEY"], "value")
            finally:
                if previous_api_key is None:
                    os.environ.pop("FSS_OPEN_API_KEY", None)
                else:
                    os.environ["FSS_OPEN_API_KEY"] = previous_api_key
                if previous_test_key is None:
                    os.environ.pop("NEW_TEST_KEY", None)
                else:
                    os.environ["NEW_TEST_KEY"] = previous_test_key

    def test_parse_xml_and_filter_keywords(self):
        page = MODULE.parse_response(SAMPLE_XML, "application/xml")
        self.assertEqual(page.total_count, 2)
        self.assertEqual(len(page.rows), 2)

        selected = MODULE.filter_rows(page.rows, ("ISA", "분산투자"))
        self.assertEqual(len(selected), 1)
        self.assertEqual(MODULE._pick(selected[0], MODULE.FIELD_ALIASES["id"]), "101")

    def test_parse_json(self):
        payload = json.dumps(
            {
                "reponse": {
                    "resultCode": "1",
                    "resultCnt": 1,
                    "result": [
                        {"contentId": "A1", "subject": "적금 안내", "contentsKor": "우대금리"}
                    ],
                }
            },
            ensure_ascii=False,
        ).encode()
        page = MODULE.parse_response(payload, "application/json")
        self.assertEqual(page.total_count, 1)
        self.assertEqual(len(page.rows), 1)

    def test_write_outputs_removes_html_and_adds_warning(self):
        rows = MODULE.parse_response(SAMPLE_XML).rows
        row = next(
            item
            for item in rows
            if MODULE._pick(item, MODULE.FIELD_ALIASES["id"]) == "101"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            json_path, paths = MODULE.write_outputs(
                [row], [row], root / "raw", root / "rag", "20260813"
            )
            self.assertTrue(json_path.exists())
            document = paths[0].read_text(encoding="utf-8")
            self.assertIn("분산투자 안내", document)
            self.assertNotIn("<p>", document)
            self.assertIn("현행 법령", document)

    def test_exact_fss_json_fields(self):
        payload = json.dumps(
            {
                "reponse": {
                    "resultCode": "1",
                    "resultMsg": "조회 성공",
                    "resultCnt": 1,
                    "result": [
                        {
                            "contentId": "114",
                            "subject": "ISA 안내",
                            "originUrl": "https://fine.fss.or.kr/example",
                            "regDate": "2018-12-24 10:52:59",
                            "contentsKor": "<div>ISA 설명</div>",
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ).encode()
        page = MODULE.parse_response(payload, "application/json")
        self.assertEqual(page.total_count, 1)
        self.assertEqual(MODULE._pick(page.rows[0], MODULE.FIELD_ALIASES["id"]), "114")
        self.assertEqual(MODULE._pick(page.rows[0], MODULE.FIELD_ALIASES["title"]), "ISA 안내")

    def test_api_error_is_rejected(self):
        payload = b'<response><resultCode>99</resultCode><resultMsg>KEY ERROR</resultMsg></response>'
        with self.assertRaises(MODULE.FinanceTipsApiError):
            MODULE.parse_response(payload)

    def test_no_data_response_is_an_empty_page(self):
        payload = (
            "<response><resultCode>900</resultCode>"
            "<resultMsg>자료가 없습니다.</resultMsg></response>"
        ).encode("cp949")
        page = MODULE.parse_response(payload, "application/xml; charset=EUC-KR")
        self.assertEqual(page.rows, [])
        self.assertEqual(page.total_count, 0)

    def test_daily_limit_response_pauses_collection(self):
        payload = (
            "<response><resultCode>033</resultCode>"
            "<resultMsg>하루 조회 건수를 초과하였습니다.</resultMsg></response>"
        ).encode("cp949")
        with self.assertRaises(MODULE.FinanceTipsCollectionPaused):
            MODULE.parse_response(payload, "application/xml; charset=EUC-KR")

    def test_fetch_all_reuses_month_cache_without_api_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            (cache_dir / "20260101_20260131.json").write_text(
                json.dumps(
                    {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                        "rows": [{"contentId": "1", "subject": "ISA 안내"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = MODULE.fetch_all(
                "https://example.invalid",
                "unused",
                start_date="2026-01-01",
                end_date="2026-01-31",
                timeout=1,
                extra_params={},
                cache_dir=cache_dir,
                max_new_requests=1,
            )
            self.assertEqual(len(rows), 1)

    def test_cp949_api_error_message_is_readable(self):
        xml = (
            "<response><resultCode>030</resultCode>"
            "<resultMsg>최대 조회기간 초과(최대 : 1년)</resultMsg></response>"
        ).encode("cp949")
        with self.assertRaisesRegex(MODULE.FinanceTipsApiError, "최대 조회기간 초과"):
            MODULE.parse_response(xml, "application/xml; charset=EUC-KR")

    def test_month_windows_split_long_range_without_gaps(self):
        windows = MODULE._month_windows(date(2016, 1, 1), date(2026, 8, 13))
        self.assertEqual(windows[0], (date(2016, 1, 1), date(2016, 1, 31)))
        self.assertEqual(windows[1], (date(2016, 2, 1), date(2016, 2, 29)))
        self.assertEqual(windows[-1][1], date(2026, 8, 13))
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous[1] + MODULE.timedelta(days=1), current[0])

    def test_clean_html_strips_html_entity_escaped_tags(self):
        """실제 API 응답은 CDATA가 아니라 본문 전체가 HTML 엔티티로 이스케이프된
        채로 온다(예: 신용점수 관련 금융꿀팁 문서들) — "&lt;p&gt;내용&lt;/p&gt;"
        처럼. unescape가 태그 스트리핑보다 먼저 실행돼야 실제 태그로 풀린 뒤
        지워지지, 순서가 반대면 이스케이프된 채로 스트리핑을 통과해버린 뒤에야
        풀려서 원본 HTML이 그대로 결과에 남는다."""
        escaped = "&lt;p&gt;신용등급을 올리려면 연체 없이 소액이라도 꾸준히 상환하세요.&lt;/p&gt;"
        cleaned = MODULE.clean_html(escaped)
        self.assertEqual(cleaned, "신용등급을 올리려면 연체 없이 소액이라도 꾸준히 상환하세요.")
        self.assertNotIn("<p>", cleaned)
        self.assertNotIn("&lt;", cleaned)

    def test_clean_html_of_image_only_escaped_body_is_empty(self):
        """본문 전체가 이스케이프된 <img> 태그뿐인 경우(실제 텍스트 없음) —
        unescape 순서가 고쳐지면 이제야 정상적으로 빈 문자열로 판정되고,
        write_outputs의 "본문이 이미지로만 구성되어 있습니다" 안내 문구가
        제대로 걸린다."""
        escaped = (
            "&lt;div class='dbdata'&gt;&lt;img src='https://example.com/a.png' "
            "alt='' style='width: 670px;'/&gt;&lt;/div&gt;"
        )
        self.assertEqual(MODULE.clean_html(escaped), "")


if __name__ == "__main__":
    unittest.main()
