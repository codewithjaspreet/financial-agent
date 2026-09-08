"""
The verification layer: checks Gemini's written text before ANY of it reaches
a user, then does the one and only substitution of {{fN}} -> a real number.

This is deliberately separate from tools.py: tools.py controls what numbers
the model is ALLOWED to reference; this file controls what happens to the
text the model actually wrote. Two independent layers -- to get a wrong
number to the user you'd need both to fail at once.
"""
import re

_PLACEHOLDER_RE = re.compile(r"\{\{(f\d+)\}\}")

# Checked on the text with all {{fN}} placeholders already removed.
BAD_PATTERNS = [
    r"\d",                      # any raw digit
    r"[₹$]",                    # currency symbols
    r"\brs\.?\b",
    r"\b(lakh|lakhs|crore|crores|thousand|hundred|million|billion)\b",
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"half|double|triple)\b",
]
_BAD_RE = [re.compile(p, re.IGNORECASE) for p in BAD_PATTERNS]


def check_no_digits(text: str) -> list[str]:
    """Returns problems found OUTSIDE the {{fN}} placeholders. Empty = clean."""
    stripped = _PLACEHOLDER_RE.sub("", text)
    return [f"number-like text found: matches /{p.pattern}/" for p in _BAD_RE if p.search(stripped)]


def check_facts_exist(text: str, facts: dict) -> list[str]:
    """Every {{fN}} the model wrote must be a key tools.py actually created."""
    used = set(_PLACEHOLDER_RE.findall(text))
    return [f"unknown fact reference: {{{{{key}}}}}" for key in used if key not in facts["items"]]


def fill_in_numbers(text: str, facts: dict) -> str:
    """
    The ONLY place a digit gets into the final answer. Called only after
    check_no_digits and check_facts_exist both return no problems.
    """
    def replace(match: re.Match) -> str:
        fact = facts["items"][match.group(1)]
        if fact["is_claim"]:
            return f"reportedly {fact['display']} (unverified)"
        return fact["display"]

    return _PLACEHOLDER_RE.sub(replace, text)
