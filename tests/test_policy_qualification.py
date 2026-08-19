from roadmap_agent.policy_qualification import median_income_limit, median_income_monthly


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
