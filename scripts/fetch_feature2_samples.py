#!/usr/bin/env python3
"""기능 2 통합 테스트용 적금·청년정책·복지서비스 샘플을 수집한다.

인증키는 환경변수 또는 터미널 숨김 입력으로만 받으며 파일에 저장하지 않는다.
각 API의 원본 레코드는 가능한 한 그대로 보존한다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any


FINLIFE_ENDPOINT = "https://finlife.fss.or.kr/finlifeapi/savingProductsSearch.json"
YOUTH_ENDPOINT = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
WELFARE_ENDPOINT = (
    "https://apis.data.go.kr/B554287/"
    "NationalWelfareInformationsV001/NationalWelfaredetailedV001"
)
WELFARE_LIST_ENDPOINT = (
    "https://apis.data.go.kr/B554287/"
    "NationalWelfareInformationsV001/NationalWelfarelistV001"
)

KEY_ENV_NAMES = {
    "finlife": "FINLIFE_API_KEY",
    "youth": "YOUTH_POLICY_API_KEY",
    "welfare": "CENTRAL_WELFARE_SERVICE_KEY",
}

DEFAULT_KEYWORDS = (
    "청년",
    "자산형성",
    "목돈",
    "저축",
    "적금",
    "금융",
    "취업",
    "중소기업",
)


class SampleApiError(RuntimeError):
    """API 요청 또는 응답 검증 실패."""


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
        value = value.strip().strip("\"").strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _request_json(url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={"User-Agent": "Roadmap-Agent-Sample-Collector/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt)
    raise SampleApiError(f"JSON API 요청이 3회 실패했습니다: {last_error}")


def _request_bytes(url: str, params: dict[str, Any], timeout: int) -> bytes:
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={"User-Agent": "Roadmap-Agent-Sample-Collector/1.0"},
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
    raise SampleApiError(f"XML API 요청이 3회 실패했습니다: {last_error}")


def _api_message(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("err_msg", "resultMsg", "resultMessage", "message"):
            if value.get(key):
                return str(value[key])
        for child in value.values():
            message = _api_message(child)
            if message:
                return message
    return ""


def parse_finlife(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SampleApiError(f"finlife result가 없습니다: {_api_message(payload) or '응답 확인 필요'}")
    base_list = result.get("baseList", [])
    option_list = result.get("optionList", [])
    if not isinstance(base_list, list) or not isinstance(option_list, list):
        raise SampleApiError("finlife baseList/optionList 형식이 예상과 다릅니다.")
    return base_list, option_list


def select_finlife_samples(
    base_list: list[dict[str, Any]], option_list: list[dict[str, Any]], limit: int
) -> dict[str, Any]:
    def key(row: dict[str, Any]) -> tuple[str, str, str]:
        return tuple(str(row.get(name, "")) for name in ("dcls_month", "fin_co_no", "fin_prdt_cd"))

    options_by_product: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for option in option_list:
        options_by_product.setdefault(key(option), []).append(option)

    # 서로 다른 회사·상품을 우선해 작은 샘플에서도 비교가 가능하게 한다.
    selected: list[dict[str, Any]] = []
    companies: set[str] = set()
    for product in base_list:
        company = str(product.get("fin_co_no", ""))
        if company in companies and len(base_list) > limit:
            continue
        selected.append({"base": product, "options": options_by_product.get(key(product), [])})
        companies.add(company)
        if len(selected) == limit:
            break
    if len(selected) < limit:
        used = {key(item["base"]) for item in selected}
        for product in base_list:
            if key(product) not in used:
                selected.append({"base": product, "options": options_by_product.get(key(product), [])})
            if len(selected) == limit:
                break
    return {"source": "finlife_saving_products", "count": len(selected), "products": selected}


def fetch_finlife(api_key: str, limit: int, timeout: int, top_fin_group: str) -> dict[str, Any]:
    all_base: list[dict[str, Any]] = []
    all_options: list[dict[str, Any]] = []
    page = 1
    while len(all_base) < limit:
        payload = _request_json(
            FINLIFE_ENDPOINT,
            {"auth": api_key, "topFinGrpNo": top_fin_group, "pageNo": page},
            timeout,
        )
        base_list, option_list = parse_finlife(payload)
        all_base.extend(base_list)
        all_options.extend(option_list)
        max_page = int(payload.get("result", {}).get("max_page_no") or page)
        if not base_list or page >= max_page:
            break
        page += 1
    return select_finlife_samples(all_base, all_options, limit)


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


def parse_youth(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("youthPolicyList"), list):
        return result["youthPolicyList"]
    # 응답 래퍼가 변경되더라도 정책번호가 있는 실제 레코드는 찾는다.
    rows = [item for item in _walk_dicts(payload) if item.get("plcyNo") and item.get("plcyNm")]
    if not rows:
        raise SampleApiError(f"온통청년 정책 목록을 찾지 못했습니다: {_api_message(payload) or '응답 확인 필요'}")
    return rows


def select_youth_samples(
    rows: list[dict[str, Any]], limit: int, keywords: tuple[str, ...]
) -> list[dict[str, Any]]:
    approved = [row for row in rows if str(row.get("plcyAprvSttsCd", "0044002")) == "0044002"]
    selected = []
    for row in approved:
        text = " ".join(str(value) for value in row.values()).casefold()
        if any(keyword.casefold() in text for keyword in keywords):
            selected.append(row)
        if len(selected) == limit:
            break
    return selected


def fetch_youth(
    api_key: str, limit: int, timeout: int, keywords: tuple[str, ...]
) -> dict[str, Any]:
    page = 1
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    while len(selected) < limit and page <= 10:
        payload = _request_json(
            YOUTH_ENDPOINT,
            {
                "apiKeyNm": api_key,
                "pageType": 1,
                "rtnType": "json",
                "pageNum": page,
                "pageSize": 100,
            },
            timeout,
        )
        rows = parse_youth(payload)
        if not rows:
            break
        for row in select_youth_samples(rows, limit, keywords):
            policy_id = str(row.get("plcyNo", ""))
            if policy_id and policy_id not in seen:
                selected.append(row)
                seen.add(policy_id)
            if len(selected) == limit:
                break
        page += 1
    return {"source": "youth_policy", "count": len(selected), "policies": selected}


def _xml_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    result: dict[str, Any] = {}
    for child in children:
        value = _xml_value(child)
        if child.tag not in result:
            result[child.tag] = value
        elif isinstance(result[child.tag], list):
            result[child.tag].append(value)
        else:
            result[child.tag] = [result[child.tag], value]
    return result


def parse_welfare_detail(payload: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SampleApiError(f"복지서비스 XML 파싱 실패: {payload[:200]!r}") from exc
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in {"0", "00"}:
        message = root.findtext(".//resultMessage") or root.findtext(".//resultMsg") or "메시지 없음"
        raise SampleApiError(f"복지서비스 API 오류(resultCode={code or '없음'}): {message}")
    candidates = [node for node in root.iter() if node.find("servId") is not None]
    if not candidates:
        raise SampleApiError("복지서비스 상세 레코드를 찾지 못했습니다.")
    value = _xml_value(min(candidates, key=lambda node: len(list(node))))
    if not isinstance(value, dict):
        raise SampleApiError("복지서비스 상세 형식이 예상과 다릅니다.")
    return value


def parse_welfare_list(payload: bytes) -> tuple[list[dict[str, Any]], int]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SampleApiError(f"복지서비스 목록 XML 파싱 실패: {payload[:200]!r}") from exc
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in {"0", "00"}:
        message = root.findtext(".//resultMessage") or root.findtext(".//resultMsg") or "메시지 없음"
        raise SampleApiError(f"복지서비스 목록 API 오류(resultCode={code or '없음'}): {message}")
    rows: list[dict[str, Any]] = []
    for node in root.findall(".//servList"):
        value = _xml_value(node)
        if isinstance(value, dict) and value.get("servId"):
            rows.append(value)
    if not rows:
        for node in root.iter():
            if node.find("servId") is not None and node.find("servNm") is not None:
                value = _xml_value(node)
                if isinstance(value, dict) and value.get("servId"):
                    rows.append(value)
    try:
        total_count = int((root.findtext(".//totalCount") or len(rows)).strip())
    except ValueError:
        total_count = len(rows)
    return rows, total_count


def select_welfare_samples(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    positive = {
        "청년도약계좌": 100,
        "청년내일저축계좌": 100,
        "희망저축": 80,
        "자산형성": 50,
        "적금": 30,
        "저축": 25,
        "목돈": 20,
        "청년": 10,
        "근로장려금": 15,
        "월세": 10,
    }
    negative = {"대출": 30, "융자": 30, "보증": 25, "노후": 20, "아동": 10}

    def score(row: dict[str, Any]) -> tuple[int, str]:
        text = " ".join(
            str(row.get(field, "")) for field in ("servNm", "servDgst", "intrsThemaArray")
        )
        value = sum(weight for word, weight in positive.items() if word in text)
        value -= sum(weight for word, weight in negative.items() if word in text)
        return value, str(row.get("servNm", ""))

    ranked = sorted(rows, key=lambda row: (-score(row)[0], score(row)[1]))
    return [row for row in ranked if score(row)[0] > 0][:limit]


def fetch_welfare_list(api_key: str, limit: int, timeout: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    page = 1
    total_count: int | None = None
    while total_count is None or len(rows) < total_count:
        payload = _request_bytes(
            WELFARE_LIST_ENDPOINT,
            {
                "serviceKey": urllib.parse.unquote(api_key.strip()),
                "callTp": "L",
                "pageNo": page,
                "numOfRows": 500,
                "srchKeyCode": "001",
            },
            timeout,
        )
        page_rows, page_total = parse_welfare_list(payload)
        if total_count is None:
            total_count = page_total
        if not page_rows:
            break
        rows.extend(page_rows)
        page += 1
    unique = {str(row.get("servId")): row for row in rows if row.get("servId")}
    if total_count and len(unique) != total_count:
        raise SampleApiError(
            f"복지서비스 목록 건수 불일치: API {total_count}건, 고유 레코드 {len(unique)}건"
        )
    return select_welfare_samples(list(unique.values()), limit), len(unique)


def fetch_welfare(
    api_key: str, service_ids: list[str], timeout: int, limit: int = 5
) -> dict[str, Any]:
    selected_from_list: list[dict[str, Any]] = []
    list_total_count: int | None = None
    if not service_ids:
        selected_from_list, list_total_count = fetch_welfare_list(api_key, limit, timeout)
        service_ids = [str(row["servId"]) for row in selected_from_list]
    if not service_ids:
        raise SampleApiError("자산형성 관련 복지서비스 후보를 찾지 못했습니다.")
    services = []
    for service_id in service_ids:
        payload = _request_bytes(
            WELFARE_ENDPOINT,
            {
                "serviceKey": urllib.parse.unquote(api_key.strip()),
                "callTp": "D",
                "servId": service_id,
            },
            timeout,
        )
        services.append(parse_welfare_detail(payload))
    return {
        "source": "central_welfare",
        "list_total_count": list_total_count,
        "selection": selected_from_list,
        "count": len(services),
        "services": services,
    }


def _read_key(source: str, requested_sources: list[str]) -> str:
    env_name = KEY_ENV_NAMES[source]
    value = os.environ.get(env_name, "").strip()
    if not value and source in requested_sources and sys.stdin.isatty():
        value = getpass.getpass(f"{source} 인증키({env_name}): ").strip()
    return value


def _write(path: Path, value: dict[str, Any], collected_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"collected_at": collected_at, **value}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="기능 2 테스트용 API 샘플을 수집합니다.")
    parser.add_argument(
        "--source",
        action="append",
        choices=tuple(KEY_ENV_NAMES),
        help="수집할 API. 생략하면 인증키가 있는 API를 모두 수집합니다.",
    )
    parser.add_argument("--finlife-limit", type=int, default=3)
    parser.add_argument("--policy-limit", type=int, default=3)
    parser.add_argument("--welfare-limit", type=int, default=5)
    parser.add_argument("--top-fin-group", default="020000", help="finlife 권역 코드")
    parser.add_argument("--keyword", action="append", help="청년정책 선별 키워드")
    parser.add_argument(
        "--welfare-service-id",
        action="append",
        default=[],
        help="상세 조회할 servId. 생략하면 최신 전체 목록에서 자동 선별합니다.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path("data/fixtures"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--date", default=date.today().isoformat())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env_file(args.env_file)
    if args.finlife_limit < 1 or args.policy_limit < 1 or args.welfare_limit < 1:
        print("오류: 샘플 개수는 1 이상이어야 합니다.", file=sys.stderr)
        return 2

    explicitly_requested = args.source or []
    keys = {source: _read_key(source, explicitly_requested) for source in KEY_ENV_NAMES}
    sources = explicitly_requested or [source for source, key in keys.items() if key]
    if not sources:
        print("오류: --source를 지정하거나 인증키 환경변수를 설정하세요.", file=sys.stderr)
        return 2

    missing = [source for source in sources if not keys[source]]
    if missing:
        names = ", ".join(KEY_ENV_NAMES[source] for source in missing)
        print(f"오류: 인증키가 없습니다: {names}", file=sys.stderr)
        return 2

    keywords = tuple(args.keyword) if args.keyword else DEFAULT_KEYWORDS
    try:
        if "finlife" in sources:
            value = fetch_finlife(
                keys["finlife"], args.finlife_limit, args.timeout, args.top_fin_group
            )
            path = args.output_dir / "finlife_savings_sample.json"
            _write(path, value, args.date)
            print(f"finlife: {value['count']}개 → {path}")
        if "youth" in sources:
            value = fetch_youth(keys["youth"], args.policy_limit, args.timeout, keywords)
            path = args.output_dir / "youth_policy_sample.json"
            _write(path, value, args.date)
            print(f"온통청년: {value['count']}개 → {path}")
        if "welfare" in sources:
            value = fetch_welfare(
                keys["welfare"],
                args.welfare_service_id,
                args.timeout,
                args.welfare_limit,
            )
            path = args.output_dir / "welfare_policy_sample.json"
            _write(path, value, args.date)
            print(f"복지서비스: {value['count']}개 → {path}")
    except SampleApiError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
