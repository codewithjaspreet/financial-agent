# app/money.py
# Centralizes all money handling so the system never uses float for financial values.

import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


def to_paise(amount: str | int | Decimal) -> int:
    value = Decimal(str(amount))
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(paise: int) -> str:
    rupees = Decimal(paise) / Decimal(100)
    return f"₹{rupees:,.2f}"


def format_short(paise: int) -> str:
    rupees = Decimal(paise) / Decimal(100)

    if rupees >= 10_000_000:
        return f"₹{rupees / Decimal(10_000_000):.2f}Cr"

    if rupees >= 100_000:
        return f"₹{rupees / Decimal(100_000):.2f}L"

    if rupees >= 1_000:
        return f"₹{rupees / Decimal(1_000):.2f}K"

    return format_money(paise)


def split_money(amount_paise: int, parts: int) -> list[int]:
    if parts <= 0:
        raise ValueError("parts must be greater than zero")

    base, remainder = divmod(amount_paise, parts)

    return [
        base + (1 if i < remainder else 0)
        for i in range(parts)
    ]


_UNIT_MULTIPLIERS = {
    "cr": 10_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "l": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "k": 1_000,
    "thousand": 1_000,
}

_MONEY_TEXT_RE = re.compile(
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>crores?|cr|lakhs?|lacs?|thousand|l|k)?\b",
    re.IGNORECASE,
)


def parse_money_text(text: str) -> int | None:
    """
    '8L', '8 lakh', '8,00,000', 'Rs 8.5 lakh' -> paise. None if nothing parses.

    This is our own deterministic parser, not the model's number: an LLM may
    extract "8L" as the amount_text from a WhatsApp claim, but this function,
    not the model, is what turns that into a paise value.
    """
    if not text:
        return None

    match = _MONEY_TEXT_RE.search(text)
    if not match or not match.group("number"):
        return None

    number_str = match.group("number").replace(",", "")
    unit = (match.group("unit") or "").lower()

    try:
        value = Decimal(number_str)
    except InvalidOperation:
        return None

    multiplier = _UNIT_MULTIPLIERS.get(unit, 1)
    rupees = value * multiplier

    return to_paise(rupees)


def split_by_weights(amount_paise: int, weights: list[int]) -> list[int]:
    if not weights or any(weight < 0 for weight in weights):
        raise ValueError("Invalid weights")

    total_weight = sum(weights)

    if total_weight <= 0:
        raise ValueError("Total weight must be greater than zero")

    result = [
        amount_paise * weight // total_weight
        for weight in weights
    ]

    remainder = amount_paise - sum(result)

    for i in range(remainder):
        result[i % len(result)] += 1

    return result
