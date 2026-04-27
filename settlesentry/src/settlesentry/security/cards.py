from __future__ import annotations

import re


def digits_only(value: str) -> str:
    """Return only numeric characters from card-like text."""
    return re.sub(r"\D", "", value)


def luhn_valid(value: str) -> bool:
    """Validate PAN-like digits with the Luhn checksum algorithm."""
    digits = digits_only(value)

    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    parity = len(digits) % 2

    for index, char in enumerate(digits):
        digit = int(char)

        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9

        checksum += digit

    return checksum % 10 == 0
