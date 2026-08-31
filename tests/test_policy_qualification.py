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


def test_multiple_distinct_percentages_are_not_arbitrarily_picked():
    """실제 온통청년 데이터: 가구유형/신청유형별로 비율이 다른 정책(예: 인천시 청년월세
    지원사업 '청년독립가구 60% / 원가구 100%')은 어느 쪽이 적용되는지 텍스트만으로
    판별할 수 없으므로, 임의로 하나를 골라 확정 판정하지 않고 확인 필요로 남긴다."""
    text = "소득 : (청년독립가구) 기준 중위소득 60%이하 (원가구) 기준 중위소득 100% 이하"
    assert median_income_limit(text, 2026, 1) is None


def test_repeated_identical_percentage_is_still_used():
    text = "가구 중위소득 60% 이하 (기준 중위소득 60% 이하 유지)"
    assert median_income_limit(text, 2026, 1) == (0.6, 1_538_543)
