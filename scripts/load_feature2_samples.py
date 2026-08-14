#!/usr/bin/env python3
"""기능 2 fixture를 ROADMAP_AGENT_DATA.md 호환 raw 테이블에 UPSERT한다.

PostgreSQL Python 드라이버 없이 Docker Compose의 psql을 사용한다. .env는
Docker Compose만 처리하며 이 스크립트는 비밀정보를 읽거나 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


YOUTH_FIELDS = (
    "plcyNo", "plcyNm", "plcyKywdNm", "plcyExplnCn", "lclsfNm", "mclsfNm",
    "pvsnInstGroupCd", "plcyPvsnMthdCd", "plcySprtCn", "sprvsnInstCd",
    "sprvsnInstCdNm", "operInstCd", "operInstCdNm", "aplyYmd", "aplyPrdSeCd",
    "bizPrdSeCd", "bizPrdBgngYmd", "bizPrdEndYmd", "bizPrdEtcCn",
    "plcyAplyMthdCn", "aplyUrlAddr", "sbmsnDcmntCn", "srngMthdCn", "etcMttrCn",
    "refUrlAddr1", "refUrlAddr2", "sprtTrgtMinAge", "sprtTrgtMaxAge",
    "sprtTrgtAgeLmtYn", "mrgSttsCd", "earnCndSeCd", "earnMinAmt", "earnMaxAmt",
    "earnEtcCn", "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "zipCd", "plcyMajorCd",
    "jobCd", "schoolCd", "sbizCd", "frstRegDt", "lastMdfcnDt", "plcyAprvSttsCd",
)
WELFARE_FIELDS = (
    "servId", "servNm", "servDtlLink", "jurMnofNm", "tgtrDtlCn", "slctCritCn",
    "alwServCn", "wlfareInfoOutlCn", "crtrYr", "rprsCtadr", "sprtCycNm",
    "srvPvsnNm", "applmetList", "inqplCtadrList", "inqplHmpgReldList",
    "basfrmList", "baslawList",
)
JSON_FIELDS = {
    "applmetList", "inqplCtadrList", "inqplHmpgReldList", "basfrmList", "baslawList"
}


class FixtureError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FixtureError(f"fixture가 없습니다: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"JSON 파싱 실패: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FixtureError(f"최상위 JSON은 객체여야 합니다: {path}")
    return value


def _literal(value: Any, *, json_value: bool = False) -> str:
    if value is None or value == "":
        return "NULL"
    if json_value:
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "'" + str(value).replace("'", "''") + "'"


def _json_literal(value: Any) -> str:
    return _literal(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def _upsert(table: str, columns: tuple[str, ...], values: list[str], conflict: str) -> str:
    quoted = ", ".join(f'"{name}"' for name in columns)
    assignments = ", ".join(
        f'"{name}" = EXCLUDED."{name}"' for name in columns if name not in conflict.split(",")
    )
    return (
        f"INSERT INTO {table} ({quoted}) VALUES ({', '.join(values)}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {assignments};"
    )


def build_finlife_sql(document: dict[str, Any]) -> list[str]:
    statements: list[str] = []
    for item in document.get("products", []):
        base = item.get("base", {})
        product_columns = (
            "dcls_month", "fin_co_no", "fin_prdt_cd", "kor_co_nm", "fin_prdt_nm",
            "join_way", "mtrt_int", "spcl_cnd", "join_deny", "join_member",
            "etc_note", "max_limit", "dcls_strt_day", "dcls_end_day", "fin_co_subm_day",
            "raw_data",
        )
        values = [
            _json_literal(base) if field == "raw_data" else _literal(base.get(field))
            for field in product_columns
        ]
        statements.append(
            _upsert(
                "raw.finlife_saving_base", product_columns, values,
                '"dcls_month", "fin_co_no", "fin_prdt_cd"',
            )
        )
        for option in item.get("options", []):
            option_columns = (
                "dcls_month", "fin_co_no", "fin_prdt_cd", "intr_rate_type",
                "intr_rate_type_nm", "rsrv_type", "rsrv_type_nm", "save_trm",
                "intr_rate", "intr_rate2", "raw_data",
            )
            option_values = [
                _json_literal(option) if field == "raw_data" else _literal(option.get(field))
                for field in option_columns
            ]
            statements.append(
                _upsert(
                    "raw.finlife_saving_option", option_columns, option_values,
                    '"dcls_month", "fin_co_no", "fin_prdt_cd", "intr_rate_type", "rsrv_type", "save_trm"',
                )
            )
    return statements


def build_youth_sql(document: dict[str, Any]) -> list[str]:
    statements = []
    columns = YOUTH_FIELDS + ("source_url", "raw_data")
    for policy in document.get("policies", []):
        if policy.get("plcyAprvSttsCd") != "0044002":
            continue
        source_url = policy.get("aplyUrlAddr") or policy.get("refUrlAddr1") or policy.get("refUrlAddr2")
        values = [_literal(policy.get(field)) for field in YOUTH_FIELDS]
        values.extend((_literal(source_url), _json_literal(policy)))
        statements.append(_upsert("raw.youth_policy", columns, values, '"plcyNo"'))
    return statements


def _as_list_lookup(selection: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("servId")): row for row in selection if row.get("servId")}


def build_welfare_sql(document: dict[str, Any]) -> list[str]:
    statements = []
    selection = _as_list_lookup(document.get("selection", []))
    columns = WELFARE_FIELDS + ("source_url", "raw_data")
    for detail in document.get("services", []):
        list_row = selection.get(str(detail.get("servId")), {})
        merged = dict(detail)
        merged["servNm"] = detail.get("servNm") or list_row.get("servNm")
        merged["servDtlLink"] = list_row.get("servDtlLink")
        source_url = merged.get("servDtlLink")
        values = [
            _literal(merged.get(field), json_value=field in JSON_FIELDS)
            + ("::jsonb" if field in JSON_FIELDS and merged.get(field) not in (None, "") else "")
            for field in WELFARE_FIELDS
        ]
        values.extend((_literal(source_url), _json_literal(detail)))
        statements.append(_upsert("raw.welfare_service_detail", columns, values, '"servId"'))
    return statements


def build_sql(fixtures_dir: Path, project_root: Path) -> str:
    statements = [
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
        (project_root / "db/init/002_source_contract.sql").read_text(encoding="utf-8"),
        (project_root / "db/migrations/003_welfare_detail_only.sql").read_text(encoding="utf-8"),
    ]
    statements.extend(build_finlife_sql(_load(fixtures_dir / "finlife_savings_sample.json")))
    statements.extend(build_youth_sql(_load(fixtures_dir / "youth_policy_sample.json")))
    statements.extend(build_welfare_sql(_load(fixtures_dir / "welfare_policy_sample.json")))
    statements.extend(
        (
            "COMMIT;",
            "SELECT 'finlife_base' AS dataset, count(*) FROM raw.finlife_saving_base "
            "UNION ALL SELECT 'finlife_option', count(*) FROM raw.finlife_saving_option "
            "UNION ALL SELECT 'youth_policy', count(*) FROM raw.youth_policy "
            "UNION ALL SELECT 'welfare_detail', count(*) FROM raw.welfare_service_detail "
            "ORDER BY dataset;",
        )
    )
    return "\n".join(statements) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="기능 2 fixture를 로컬 PostgreSQL에 적재합니다.")
    parser.add_argument("--fixtures-dir", type=Path, default=Path("data/fixtures"))
    parser.add_argument("--database", default="roadmap_agent")
    parser.add_argument("--user", default="roadmap_agent")
    parser.add_argument("--print-sql", action="store_true", help="실행하지 않고 SQL을 stdout에 출력")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        sql = build_sql(args.fixtures_dir, root)
    except FixtureError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    if args.print_sql:
        print(sql, end="")
        return 0
    command = [
        "docker", "compose", "exec", "-T", "postgres", "psql",
        "-U", args.user, "-d", args.database,
    ]
    completed = subprocess.run(command, input=sql, text=True, cwd=root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
