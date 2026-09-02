#!/usr/bin/env python3
"""금융감독원 금융꿀팁 200선 Open API를 수집해 JSON과 RAG Markdown으로 저장한다.

공식 API 명세의 기간 조건으로 전체 원문을 받고 관련 문서를 선별한다.
인증키는 명령행 인자로 받지 않아 셸 기록에 남기지 않는다.
"""

from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


KEY_ENV_NAME = "FSS_OPEN_API_KEY"
ENDPOINT = "https://www.fss.or.kr/fss/kr/openApi/api/tip.jsp"
SUCCESS_CODES = {"", "0", "00", "000", "1", "SUCCESS", "OK"}
NO_DATA_CODES = {"900"}
DAILY_LIMIT_CODES = {"033"}
DEFAULT_KEYWORDS = (
    "사회초년생",
    "목돈",
    "저축",
    "예금",
    "적금",
    "우대금리",
    "중도해지",
    "분산투자",
    "장기투자",
    "원금손실",
    "투자위험",
    "펀드",
    "ETF",
    "ISA",
    "개인종합자산관리계좌",
    "연금저축",
    "IRP",
    "퇴직연금",
    "신용점수",
    "신용등급",
    "대출",
    "금융사기",
    "불법사금융",
)
FIELD_ALIASES = {
    "id": ("contentId", "seq", "bbsSeq", "nttId", "boardSeq", "no", "id"),
    "title": ("subject", "title", "sj", "nttSj", "bbsSj", "newsTitle"),
    "body": (
        "contentsKor",
        "content",
        "contents",
        "cn",
        "nttCn",
        "bbsCn",
        "body",
        "articleContent",
    ),
    "date": (
        "regDate",
        "regDt",
        "registerDate",
        "frstRegDt",
        "createDate",
        "date",
    ),
    "url": ("originUrl", "url", "link", "viewUrl", "detailUrl", "sourceUrl"),
}


class FinanceTipsApiError(RuntimeError):
    """API 요청, 응답 파싱 또는 데이터 검증 실패."""


class FinanceTipsCollectionPaused(FinanceTipsApiError):
    """일일 호출 한도에 맞춰 체크포인트 저장 후 수집을 일시 중단."""


@dataclass(frozen=True)
class ParsedPage:
    rows: list[dict[str, Any]]
    total_count: int | None


def load_env_file(path: Path) -> None:
    """외부 패키지 없이 로컬 .env 값을 환경변수의 폴백으로 사용한다."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    result: dict[str, Any] = {}
    for child in children:
        key = _local_name(child.tag)
        value = _xml_value(child)
        if key not in result:
            result[key] = value
        elif isinstance(result[key], list):
            result[key].append(value)
        else:
            result[key] = [result[key], value]
    return result


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _pick(record: dict[str, Any], aliases: tuple[str, ...]) -> str:
    lowered = {str(key).lower(): value for key, value in record.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value is not None and not isinstance(value, (dict, list)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _looks_like_item(record: dict[str, Any]) -> bool:
    return bool(_pick(record, FIELD_ALIASES["title"])) and bool(
        _pick(record, FIELD_ALIASES["id"]) or _pick(record, FIELD_ALIASES["body"])
    )


def _find_total(value: Any) -> int | None:
    total_names = {"resultcnt", "totalcount", "totalcnt", "totcnt", "total"}
    for record in _walk_dicts(value):
        for key, raw in record.items():
            if str(key).lower() in total_names:
                try:
                    return int(str(raw).replace(",", ""))
                except (TypeError, ValueError):
                    pass
    return None


def _check_result(value: Any) -> bool:
    """정상 응답이면 True, 조회 결과 없음이면 False를 반환한다."""
    code_names = {"resultcode", "resultcd", "code"}
    message_names = {"resultmessage", "resultmsg", "message", "msg"}
    for record in _walk_dicts(value):
        lowered = {str(key).lower(): raw for key, raw in record.items()}
        for name in code_names:
            if name not in lowered or isinstance(lowered[name], (dict, list)):
                continue
            code = str(lowered[name]).strip().upper()
            if code in NO_DATA_CODES:
                return False
            if code in DAILY_LIMIT_CODES:
                raise FinanceTipsCollectionPaused(
                    "금융감독원 API 하루 30회 한도에 도달했습니다. "
                    "저장된 월별 캐시는 유지되며 다음 날 같은 명령으로 재개할 수 있습니다."
                )
            if code not in SUCCESS_CODES:
                message = next(
                    (str(lowered[key]) for key in message_names if key in lowered),
                    "메시지 없음",
                )
                raise FinanceTipsApiError(f"API 오류(resultCode={code}): {message}")
            return True
    return True


def _decode_payload(payload: bytes, content_type: str = "") -> str:
    """응답 헤더와 국내 공공 API의 흔한 문자셋을 순서대로 검사한다."""
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(("utf-8-sig", "cp949", "euc-kr"))
    tried: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8-sig", errors="replace")


def parse_response(payload: bytes, content_type: str = "") -> ParsedPage:
    text = _decode_payload(payload, content_type).strip()
    if not text:
        raise FinanceTipsApiError("API 응답이 비어 있습니다.")

    try:
        if "json" in content_type.lower() or text[:1] in "[{":
            root: Any = json.loads(text)
        else:
            root_element = ET.fromstring(text)
            root = {_local_name(root_element.tag): _xml_value(root_element)}
    except (json.JSONDecodeError, ET.ParseError) as exc:
        raise FinanceTipsApiError(f"JSON/XML 파싱 실패: {text[:200]}") from exc

    if not _check_result(root):
        return ParsedPage(rows=[], total_count=0)
    candidates = [record for record in _walk_dicts(root) if _looks_like_item(record)]

    # 상위 컨테이너가 하위 게시물을 중첩한 경우 실제 필드 수가 적은 하위 레코드를 우선한다.
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in sorted(candidates, key=len):
        identity = (
            _pick(record, FIELD_ALIASES["id"]),
            _pick(record, FIELD_ALIASES["title"]),
        )
        if identity not in seen:
            rows.append(record)
            seen.add(identity)

    return ParsedPage(rows=rows, total_count=_find_total(root))


def _request(
    endpoint: str,
    params: dict[str, str | int],
    timeout: int,
) -> tuple[bytes, str]:
    separator = "&" if "?" in endpoint else "?"
    url = endpoint + separator + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Roadmap-Agent-FSS-Finance-Tips-Collector/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt)
    raise FinanceTipsApiError(f"API 요청이 3회 실패했습니다: {last_error}")


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (_pick(row, FIELD_ALIASES["id"]), _pick(row, FIELD_ALIASES["title"]))


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = _row_identity(row)
        if identity not in seen:
            unique.append(row)
            seen.add(identity)
    return unique


def rows_from_cache(cache_dir: Path) -> list[dict[str, Any]]:
    """API를 호출하지 않고, 지금까지 체크포인트로 저장된 월별 캐시만 모은다.

    전체 수집기간이 끝나기 전에도 지금까지 모인 원문으로 RAG 문서를 먼저
    만들어보고 싶을 때 사용한다(`--rag-only`).
    """
    rows: list[dict[str, Any]] = []
    for cache_path in sorted(cache_dir.glob("*.json")):
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        rows.extend(cached.get("rows", []))
    return _dedupe_rows(rows)


def fetch_all(
    endpoint: str,
    api_key: str,
    *,
    start_date: str,
    end_date: str,
    timeout: int,
    extra_params: dict[str, str],
    cache_dir: Path,
    max_new_requests: int,
) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    new_requests = 0
    for window_start, window_end in _month_windows(
        date.fromisoformat(start_date), date.fromisoformat(end_date)
    ):
        cache_path = cache_dir / f"{window_start:%Y%m%d}_{window_end:%Y%m%d}.json"
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            rows = cached.get("rows", [])
            print(f"캐시 사용 {window_start}~{window_end}: {len(rows)}건", file=sys.stderr)
        else:
            if new_requests >= max_new_requests:
                raise FinanceTipsCollectionPaused(
                    f"오늘 신규 조회 {new_requests}회를 저장했습니다. "
                    "다음 날 같은 명령을 실행하면 다음 달부터 이어집니다."
                )
            params: dict[str, str | int] = dict(extra_params)
            params.update(
                {
                    "apiType": "json",
                    "startDate": window_start.isoformat(),
                    "endDate": window_end.isoformat(),
                    "authKey": api_key,
                }
            )
            payload, content_type = _request(endpoint, params, timeout)
            parsed = parse_response(payload, content_type)
            rows = parsed.rows
            if not rows and parsed.total_count != 0:
                raise FinanceTipsApiError("게시물 필드를 찾지 못했습니다. API 응답 예시를 확인하세요.")
            if parsed.total_count is not None and len(rows) != parsed.total_count:
                raise FinanceTipsApiError(
                    f"건수 불일치: resultCnt={parsed.total_count}, 파싱={len(rows)}"
                )
            cache_path.write_text(
                json.dumps(
                    {
                        "start_date": window_start.isoformat(),
                        "end_date": window_end.isoformat(),
                        "rows": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            new_requests += 1
            print(f"조회 기간 {window_start}~{window_end}: {len(rows)}건", file=sys.stderr)
        for row in rows:
            identity = _row_identity(row)
            if identity not in seen:
                unique.append(row)
                seen.add(identity)
    return unique


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """개인 인증키의 최대 조회기간(1개월)을 넘지 않는 연속 구간을 만든다."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1, day=1)
        window_end = min(end, next_month - timedelta(days=1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


# 실제 HTML 태그 이름만 매칭한다 — 그냥 "<[^>]+>"로 아무 꺾쇠나 지우면, 일부
# 게시물 제목이 스타일 표기로 쓰는 "<시리즈 제3편 투자>" 같은 텍스트(HTML 태그가
# 아님)까지 지워져 "신입사원의 금융상품 현명하게 가입하기<시리즈 제3편 투자>"와
# "<시리즈 제4편 신용카드>"가 서로 다른 글인데도 같은 제목으로 뭉개진다.
_HTML_TAG_NAMES = (
    "div|img|p|br|span|a|table|thead|tbody|tfoot|tr|td|th|ul|ol|li|"
    "h[1-6]|strong|em|b|i|u|font|center|blockquote|hr"
)
TAG_RE = re.compile(rf"</?(?:{_HTML_TAG_NAMES})\b[^>]*/?>", re.IGNORECASE)
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_RE = re.compile(r"\n{3,}")


def clean_html(raw: str) -> str:
    # API가 내려주는 본문은 실제 태그가 아니라 HTML 엔티티로 이스케이프된 채로
    # 온다(예: "&lt;div&gt;..."). 엔티티를 먼저 풀어야 <br>/</p> 등이 진짜
    # 태그로 나타나 아래 정리 규칙이 실제로 걸린다 — 순서가 반대면(예전 코드)
    # 스트리핑 시점엔 아직 "&lt;"라 아무것도 못 지우고, 그 뒤에 풀린 태그만
    # 그대로 남아 본문에 raw HTML이 섞여 들어간다.
    raw = html.unescape(raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])>", "\n", raw)
    raw = TAG_RE.sub("", raw)
    raw = raw.replace("\xa0", " ")
    lines = [SPACE_RE.sub(" ", line).strip() for line in raw.splitlines()]
    return BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def filter_rows(rows: list[dict[str, Any]], keywords: tuple[str, ...]) -> list[dict[str, Any]]:
    lowered_keywords = tuple(keyword.casefold() for keyword in keywords)
    selected = []
    for row in rows:
        haystack = " ".join(
            (_pick(row, FIELD_ALIASES["title"]), _pick(row, FIELD_ALIASES["body"]))
        ).casefold()
        if any(keyword in haystack for keyword in lowered_keywords):
            selected.append(row)
    return selected


def _safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned[:100] or fallback).rstrip(" ._")


def write_outputs(
    all_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    raw_dir: Path,
    rag_dir: Path,
    stamp: str,
) -> tuple[Path, list[Path]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    rag_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / f"금융감독원_금융꿀팁200선_{stamp}.json"
    json_path.write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    written: list[Path] = []
    for index, row in enumerate(selected_rows, start=1):
        source_id = _pick(row, FIELD_ALIASES["id"]) or f"row-{index}"
        title = clean_html(_pick(row, FIELD_ALIASES["title"])) or f"금융꿀팁 {source_id}"
        body = clean_html(_pick(row, FIELD_ALIASES["body"]))
        published_at = _pick(row, FIELD_ALIASES["date"])
        source_url = _pick(row, FIELD_ALIASES["url"])
        attachment_names = str(row.get("atchfileNm", "")).split("|") if row.get("atchfileNm") else []
        attachment_urls = str(row.get("atchfileUrl", "")).split("|") if row.get("atchfileUrl") else []
        filename = f"{index:03d}_{_safe_filename(title, source_id)}.md"
        path = rag_dir / filename
        frontmatter = [
            "---",
            "source_type: finance_education",
            f"source_id: fss_finance_tip_{source_id}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"source_url: {source_url}",
            f"published_at: {published_at}",
            f"collected_at: {date.today().isoformat()}",
            "section: 금융꿀팁 200선",
            "status: official_source_unverified_currentness",
            "---",
            "",
            f"# {title}",
            "",
            body or "본문이 이미지로만 구성되어 있습니다. 원문 또는 첨부파일의 텍스트를 별도로 추출해야 합니다.",
            "",
            "## 첨부파일",
            "",
            *(
                [
                    f"- [{attachment_names[i] if i < len(attachment_names) else f'첨부파일 {i + 1}'}]({url})"
                    for i, url in enumerate(attachment_urls)
                    if url
                ]
                or ["- 첨부파일 없음"]
            ),
            "",
            "## 사용 시 주의",
            "",
            "게시 시점 이후 제도가 변경되었을 수 있으므로 금액·기간·자격요건은 현행 법령 또는 최신 공식 안내와 대조한다.",
            "",
        ]
        path.write_text("\n".join(frontmatter), encoding="utf-8")
        written.append(path)
    return json_path, written


def _parse_param(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise FinanceTipsApiError(f"--param은 이름=값 형식이어야 합니다: {value}")
        key, raw = value.split("=", 1)
        if not key.strip():
            raise FinanceTipsApiError(f"--param 이름이 비어 있습니다: {value}")
        result[key.strip()] = raw.strip()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="금융감독원 금융꿀팁 200선 API를 JSON과 RAG Markdown으로 저장합니다."
    )
    parser.add_argument("--endpoint", default=ENDPOINT, help="금융꿀팁 200선 API 요청 URL")
    parser.add_argument("--start-date", default="2016-01-01", help="검색 시작일 YYYY-MM-DD")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="검색 종료일 YYYY-MM-DD")
    parser.add_argument("--timeout", type=int, default=30, help="요청 제한시간(초)")
    parser.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="추가 요청 파라미터"
    )
    parser.add_argument(
        "--keyword", action="append", help="선별 키워드(여러 번 지정 가능)"
    )
    parser.add_argument("--all", action="store_true", help="키워드 선별 없이 전부 문서화")
    parser.add_argument(
        "--rag-only", action="store_true",
        help="API를 호출하지 않고 지금까지 캐시된 월별 원문만으로 RAG 문서를 다시 만든다",
    )
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--rag-dir", type=Path, default=Path("data/rag/finance_tips"))
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/raw/.fss_finance_tips_cache"),
        help="월별 체크포인트 저장 위치",
    )
    parser.add_argument(
        "--max-new-requests",
        type=int,
        default=29,
        help="한 번 실행할 때의 신규 API 요청 수(개인키 일일 제한 30회 보호)",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--date", default=date.today().strftime("%Y%m%d"), help="원문 파일 기준일 YYYYMMDD"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env_file(args.env_file)
    try:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)
    except ValueError:
        print("오류: 날짜는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    if start_date > end_date:
        print("오류: 시작일은 종료일보다 늦을 수 없습니다.", file=sys.stderr)
        return 2
    if args.max_new_requests < 1 or args.max_new_requests > 30:
        print("오류: --max-new-requests는 1~30이어야 합니다.", file=sys.stderr)
        return 2

    if args.rag_only:
        rows = rows_from_cache(args.cache_dir)
        keywords = tuple(args.keyword) if args.keyword else DEFAULT_KEYWORDS
        selected = rows if args.all else filter_rows(rows, keywords)
        json_path, markdown_paths = write_outputs(
            rows, selected, args.raw_dir, args.rag_dir, args.date
        )
        print(f"캐시에서 원문 {len(rows)}건을 모았습니다(수집은 아직 진행 중일 수 있습니다).")
        print(f"RAG 선별: {len(markdown_paths)}건")
        print(f"JSON: {json_path}")
        print(f"RAG:  {args.rag_dir}")
        return 0

    api_key = os.environ.get(KEY_ENV_NAME, "").strip()
    if not api_key and sys.stdin.isatty():
        api_key = getpass.getpass("금융감독원 Open API 인증키: ").strip()
    if not api_key:
        print(f"오류: 인증키를 입력하거나 환경변수 {KEY_ENV_NAME}을 설정하세요.", file=sys.stderr)
        return 2

    try:
        rows = fetch_all(
            args.endpoint.strip(),
            api_key,
            start_date=args.start_date,
            end_date=args.end_date,
            timeout=args.timeout,
            extra_params=_parse_param(args.param),
            cache_dir=args.cache_dir,
            max_new_requests=args.max_new_requests,
        )
        keywords = tuple(args.keyword) if args.keyword else DEFAULT_KEYWORDS
        selected = rows if args.all else filter_rows(rows, keywords)
        json_path, markdown_paths = write_outputs(
            rows, selected, args.raw_dir, args.rag_dir, args.date
        )
    except FinanceTipsCollectionPaused as exc:
        print(f"수집 일시중지: {exc}", file=sys.stderr)
        return 3
    except FinanceTipsApiError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(f"원문 수집: {len(rows)}건")
    print(f"RAG 선별: {len(markdown_paths)}건")
    print(f"JSON: {json_path}")
    print(f"RAG:  {args.rag_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
