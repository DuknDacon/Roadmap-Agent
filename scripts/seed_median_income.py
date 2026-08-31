#!/usr/bin/env python3
"""보건복지부 고시 원문에서 확인한 기준 중위소득/생계급여/의료급여 선정기준을
`median_income` 테이블에 채운다 (INSERT OR REPLACE, 몇 번을 돌려도 같은 결과).

출처:
- 2025년: 보건복지부 고시 제2024-162호 (2024-08-01 고시, 2025-01-01 시행)
  data/source_docs/2025년+기준+중위소득+및+생계·의료급여+선정기준과+최저보장수준+고시.pdf
- 2026년: 보건복지부 고시 제2025-135호 (2025-08-01 고시, 2026-01-01 시행)
  data/source_docs/2026년+기준+중위소득+및+생계·의료급여+선정기준과+최저보장수준+고시.pdf

사용법:
  python scripts/seed_median_income.py --db-path /path/to/seedup_data.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path

SOURCE_URL = "https://www.mohw.go.kr/menu.es?mid=a10708010900"

# (household_size, median_monthly_income, livelihood_threshold, medical_threshold)
ROWS_BY_YEAR: dict[int, tuple[str, str, str, list[tuple[int, int, int, int]]]] = {
    2025: (
        "보건복지부 고시 제2024-162호",
        "2024-08-01",
        "2025-01-01",
        [
            (1, 2_392_013, 765_444, 956_805),
            (2, 3_932_658, 1_258_451, 1_573_063),
            (3, 5_025_353, 1_608_113, 2_010_141),
            (4, 6_097_773, 1_951_287, 2_439_109),
            (5, 7_108_192, 2_274_621, 2_843_277),
            (6, 8_064_805, 2_580_738, 3_225_922),
            (7, 8_988_428, 2_876_297, 3_595_371),
        ],
    ),
    2026: (
        "보건복지부 고시 제2025-135호",
        "2025-08-01",
        "2026-01-01",
        [
            (1, 2_564_238, 820_556, 1_025_695),
            (2, 4_199_292, 1_343_773, 1_679_717),
            (3, 5_359_036, 1_714_892, 2_143_614),
            (4, 6_494_738, 2_078_316, 2_597_895),
            (5, 7_556_719, 2_418_150, 3_022_688),
            (6, 8_555_952, 2_737_905, 3_422_381),
            (7, 9_515_150, 3_044_848, 3_806_060),
        ],
    ),
}


def seed(db_path: Path) -> int:
    collected_at = datetime.now().isoformat(timespec="seconds")
    connection = sqlite3.connect(str(db_path))
    inserted = 0
    try:
        for year, (notice_number, announced_on, effective_on, rows) in ROWS_BY_YEAR.items():
            for household_size, median, livelihood, medical in rows:
                connection.execute(
                    """
                    INSERT INTO median_income (
                        effective_year, household_size, median_monthly_income,
                        livelihood_threshold, medical_threshold, notice_number,
                        announced_on, effective_on, source_url, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(effective_year, household_size) DO UPDATE SET
                        median_monthly_income = excluded.median_monthly_income,
                        livelihood_threshold = excluded.livelihood_threshold,
                        medical_threshold = excluded.medical_threshold,
                        notice_number = excluded.notice_number,
                        announced_on = excluded.announced_on,
                        effective_on = excluded.effective_on,
                        source_url = excluded.source_url,
                        collected_at = excluded.collected_at
                    """,
                    (
                        year,
                        household_size,
                        median,
                        livelihood,
                        medical,
                        notice_number,
                        announced_on,
                        effective_on,
                        SOURCE_URL,
                        collected_at,
                    ),
                )
                inserted += 1
        connection.commit()
    finally:
        connection.close()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args()
    if not args.db_path.is_file():
        print(f"오류: DB 파일을 찾을 수 없습니다: {args.db_path}")
        return 1
    count = seed(args.db_path)
    print(f"median_income {count}행 반영 완료 ({date.today().isoformat()} 기준): {args.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
