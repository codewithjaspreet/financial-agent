from app.utils.money import format_money, parse_money_text, split_by_weights, split_money, to_paise


def test_split_money_sums_back_exactly():
    parts = split_money(10_000_000, 3)
    assert parts == [3333334, 3333333, 3333333]
    assert sum(parts) == 10_000_000


def test_split_money_rounding_case_1_lakh_three_ways():
    """Edge case #15: Rs 1,00,000 split three ways -- must sum back exactly."""
    parts = split_money(1_00_000_00, 3)
    assert sum(parts) == 1_00_000_00
    assert max(parts) - min(parts) <= 1  # leftover paise goes to the first entries only


def test_split_by_weights_uneven_split_sums_back_exactly():
    parts = split_by_weights(100, [1, 1, 1])
    assert sum(parts) == 100


def test_to_paise_from_rupee_string():
    assert to_paise("420000") == 420000_00


def test_format_money_uses_indian_lakh_crore_grouping_not_western():
    assert format_money(420000000) == "₹42,00,000.00"
    assert format_money(100) == "₹1.00"
    assert format_money(-420000000) == "-₹42,00,000.00"


def test_parse_money_text_handles_lakh_shorthand():
    assert parse_money_text("8L") == 8_00_000_00
    assert parse_money_text("8 lakh") == 8_00_000_00
    assert parse_money_text("8,00,000") == 8_00_000_00
    assert parse_money_text("nothing here") is None
