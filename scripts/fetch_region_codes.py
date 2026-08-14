from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://www.code.go.kr"
LIST_URL = f"{BASE_URL}/stdcode/regCodeL.do"
DISTRICT_URL = f"{BASE_URL}/stdcode/sggCodeIL.do"
SIDO_RE = re.compile(r'<option value="(\d{2})"[^>]*>([^<]+)</option>')
SIDO_SELECT_RE = re.compile(r'<select[^>]+id="Type1"[^>]*>(.*?)</select>', re.DOTALL)
NAMES_RE = re.compile(r'strSggNm\s*=\s*"([^"]*)"\.split')
CODES_RE = re.compile(r'strSggCd\s*=\s*"([^"]*)"\.split')


def request_text(url: str, data: dict[str, str] | None = None) -> str:
    body = urlencode(data).encode() if data else None
    request = Request(url, data=body, headers={"User-Agent": "SeedUp-Roadmap-Agent/0.1"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_provinces(html: str) -> list[tuple[str, str]]:
    select = SIDO_SELECT_RE.search(html)
    if not select:
        raise RuntimeError("시·도 선택 목록을 찾을 수 없습니다.")
    unique: dict[str, str] = {}
    for code, name in SIDO_RE.findall(select.group(1)):
        unique[code] = name.strip()
    return sorted(unique.items(), key=lambda item: item[1])


def parse_districts(html: str, province_code: str) -> list[dict[str, str]]:
    names_match = NAMES_RE.search(html)
    codes_match = CODES_RE.search(html)
    if not names_match or not codes_match:
        raise RuntimeError(f"시군구 응답을 해석할 수 없습니다: {province_code}")
    names = [value for value in names_match.group(1).split(",") if value]
    codes = [value for value in codes_match.group(1).split(",") if value]
    if len(names) != len(codes):
        raise RuntimeError(f"시군구 이름과 코드 수가 다릅니다: {province_code}")
    return [
        {"code": f"{province_code}{code}", "name": name}
        for code, name in zip(codes, names, strict=True)
    ]


def fetch_regions() -> dict[str, object]:
    provinces = parse_provinces(request_text(LIST_URL))
    regions = []
    for code, name in provinces:
        html = request_text(DISTRICT_URL, {"sidoCd": code, "searchOk": "0"})
        regions.append(
            {"code": code, "name": name, "districts": parse_districts(html, code)}
        )
        print(f"{name}: {len(regions[-1]['districts'])}개")
    return {
        "source_url": LIST_URL,
        "fetched_at": date.today().isoformat(),
        "regions": regions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="행정안전부 현존 시도·시군구 코드 수집")
    parser.add_argument(
        "--output", type=Path, default=Path("data/reference/region_codes.json")
    )
    args = parser.parse_args()
    payload = fetch_regions()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
