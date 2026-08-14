#!/usr/bin/env python3
"""한국사회보장정보원 중앙부처복지서비스 목록을 JSON과 CSV로 저장한다."""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


ENDPOINT = (
    "https://apis.data.go.kr/B554287/"
    "NationalWelfareInformationsV001/NationalWelfarelistV001"
)
KEY_ENV_NAME = "CENTRAL_WELFARE_SERVICE_KEY"
SUCCESS_CODES = {"0", "00"}
PREFERRED_COLUMNS = [
    "servId",
    "servNm",
    "servDgst",
    "servDtlLink",
    "jurMnofNm",
    "jurOrgNm",
    "rprsCtadr",
    "lifeArray",
    "trgterIndvdlArray",
    "intrsThemaArray",
    "sprtCycNm",
    "srvPvsnNm",
    "onapPsbltYn",
    "svcfrstRegTs",
]


class WelfareApiError(RuntimeError):
    """API 응답 또는 데이터 검증 실패."""


@dataclass(frozen=True)
class PageResult:
    rows: list[dict[str, Any]]
    total_count: int
    page_no: int
    num_of_rows: int


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _element_to_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return _text(element)

    result: dict[str, Any] = {}
    for child in children:
        value = _element_to_value(child)
        existing = result.get(child.tag)
        if existing is None:
            result[child.tag] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            result[child.tag] = [existing, value]
    return result


def parse_page(xml_bytes: bytes) -> PageResult:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        preview = xml_bytes[:200].decode("utf-8", errors="replace")
        raise WelfareApiError(f"XML 파싱 실패: {preview}") from exc

    result_code = _text(root.find(".//resultCode"))
    result_message = _text(root.find(".//resultMessage")) or _text(
        root.find(".//resultMsg")
    )
    if result_code not in SUCCESS_CODES:
        raise WelfareApiError(
            f"API 오류(resultCode={result_code or '없음'}): "
            f"{result_message or '메시지 없음'}"
        )

    item_nodes = root.findall(".//servList")
    if not item_nodes:
        item_nodes = [
            node for node in root.findall(".//wantedList") if node.find("servId") is not None
        ]

    rows: list[dict[str, Any]] = []
    for node in item_nodes:
        value = _element_to_value(node)
        if isinstance(value, dict) and value.get("servId"):
            rows.append(value)

    def parse_int(tag: str, default: int) -> int:
        value = _text(root.find(f".//{tag}"))
        try:
            return int(value)
        except ValueError:
            return default

    return PageResult(
        rows=rows,
        total_count=parse_int("totalCount", len(rows)),
        page_no=parse_int("pageNo", 1),
        num_of_rows=parse_int("numOfRows", len(rows)),
    )


def _request_page(service_key: str, page_no: int, page_size: int, timeout: int) -> bytes:
    params = {
        "serviceKey": urllib.parse.unquote(service_key.strip()),
        "callTp": "L",
        "pageNo": page_no,
        "numOfRows": page_size,
        "srchKeyCode": "001",
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Roadmap-Agent-Welfare-Collector/1.0"},
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt)

    raise WelfareApiError(f"API 요청이 3회 실패했습니다: {last_error}")


def fetch_all(service_key: str, page_size: int, timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_no = 1
    total_count: int | None = None

    while total_count is None or len(rows) < total_count:
        page = parse_page(_request_page(service_key, page_no, page_size, timeout))
        if total_count is None:
            total_count = page.total_count
            print(f"API totalCount: {total_count}", file=sys.stderr)

        if not page.rows:
            if len(rows) < total_count:
                raise WelfareApiError(
                    f"{page_no}페이지가 비어 있습니다({len(rows)}/{total_count}건 수집)."
                )
            break

        rows.extend(page.rows)
        print(f"페이지 {page_no}: 누적 {len(rows)}/{total_count}건", file=sys.stderr)
        page_no += 1

    ids = [str(row.get("servId", "")) for row in rows]
    missing_ids = sum(not value for value in ids)
    duplicate_ids = len(ids) - len(set(ids))
    if missing_ids or duplicate_ids:
        raise WelfareApiError(
            f"servId 검증 실패: 누락 {missing_ids}건, 중복 {duplicate_ids}건"
        )
    if total_count is not None and len(rows) != total_count:
        raise WelfareApiError(f"건수 불일치: 응답 {total_count}건, 수집 {len(rows)}건")

    return rows


def _csv_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "" if value is None else str(value)


def write_outputs(rows: list[dict[str, Any]], output_dir: Path, stamp: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"중앙부처복지서비스_목록_{stamp}"
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    all_columns = {key for row in rows for key in row}
    columns = [column for column in PREFERRED_COLUMNS if column in all_columns]
    columns.extend(sorted(all_columns - set(columns)))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})

    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="중앙부처복지서비스 전체 목록을 JSON·CSV로 내려받습니다."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw"),
        help="출력 디렉터리(기본값: data/raw)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="페이지당 건수(1~500, 기본값: 500)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="요청 제한시간(초, 기본값: 30)",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat().replace("-", ""),
        help="출력 파일 기준일 YYYYMMDD(기본값: 오늘)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.page_size <= 500:
        print("오류: --page-size는 1~500이어야 합니다.", file=sys.stderr)
        return 2

    service_key = os.environ.get(KEY_ENV_NAME, "").strip()
    if not service_key and sys.stdin.isatty():
        service_key = getpass.getpass(
            "공공데이터포털 인증키를 입력하세요(화면에 표시되지 않음): "
        ).strip()
    if not service_key:
        print(
            f"오류: 인증키를 입력하거나 환경변수 {KEY_ENV_NAME}을 설정하세요.",
            file=sys.stderr,
        )
        return 2

    try:
        rows = fetch_all(service_key, args.page_size, args.timeout)
        json_path, csv_path = write_outputs(rows, args.output_dir, args.date)
    except WelfareApiError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(f"수집 완료: {len(rows)}건")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
