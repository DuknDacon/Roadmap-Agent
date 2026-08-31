import sqlite3

from roadmap_agent.policy_qualification import (
    _median_income_table_from_db,
    median_income_limit,
    median_income_monthly,
)


def test_prefers_median_income_table_over_hardcoded_fallback(tmp_path, monkeypatch):
    """DB에 값이 있으면 그걸 쓰고, 하드코딩된 폴백 dict는 무시한다."""
    db_path = tmp_path / "shared.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE median_income (effective_year INTEGER, household_size INTEGER,"
        " median_monthly_income INTEGER)"
    )
    connection.execute("INSERT INTO median_income VALUES (2030, 1, 9999999)")
    connection.commit()
    connection.close()

    monkeypatch.setenv("SHARED_DB_PATH", str(db_path))
    _median_income_table_from_db.cache_clear()
    try:
        assert median_income_monthly(2030, 1) == 9_999_999
        # DB에 없는 연도는 여전히 하드코딩된 폴백으로 떨어진다.
        assert median_income_monthly(2026, 1) == 2_564_238
    finally:
        _median_income_table_from_db.cache_clear()


def test_falls_back_to_hardcoded_values_without_shared_db_path(monkeypatch):
    monkeypatch.delenv("SHARED_DB_PATH", raising=False)
    _median_income_table_from_db.cache_clear()
    assert median_income_monthly(2026, 1) == 2_564_238


def test_returns_2026_median_income_by_household_size():
    assert median_income_monthly(2026, 1) == 2_564_238
    assert median_income_monthly(2026, 4) == 6_494_738


def test_extrapolates_households_larger_than_seven_with_official_rule():
    assert median_income_monthly(2026, 8) == 10_474_348


def test_calculates_policy_percentage_limit():
    assert median_income_limit("가구 중위소득 200% 이하", 2026, 1) == (
        2.0,
        5_128_476,
    )


def test_unknown_year_or_missing_percentage_is_not_guessed():
    assert median_income_monthly(2027, 1) is None
    assert median_income_limit("가구 중위소득 기준", 2026, 1) is None
