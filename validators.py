

import re
from datetime import datetime
from typing import Tuple, Optional



#card validation -->
def validate_luhn(card_number: str) -> bool:
    """Validate card number using the Luhn algorithm."""
    digits = re.sub(r"\D", "", card_number)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def is_amex(card_number: str) -> bool:
    """American Express cards start with 34 or 37."""
    digits = re.sub(r"\D", "", card_number)
    return digits[:2] in ("34", "37")


def validate_cvv(cvv: str, card_number: str) -> Tuple[bool, Optional[str]]:
    """
    Validate CVV length.
    - Amex: 4 digits
    - All others: 3 digits
    """
    cvv = cvv.strip()
    if not cvv.isdigit():
        return False, "CVV must contain only digits."
    expected_len = 4 if is_amex(card_number) else 3
    if len(cvv) != expected_len:
        card_type = "Amex" if expected_len == 4 else "standard"
        return False, f"CVV must be {expected_len} digits for a {card_type} card."
    return True, None


def validate_expiry(month: int, year: int) -> Tuple[bool, Optional[str]]:
    """Validate card expiry — must be a present or future month."""
    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError):
        return False, "Expiry month and year must be integers."

    if not (1 <= month <= 12):
        return False, "Expiry month must be between 1 and 12."

    now = datetime.now()
    # Card is valid through the last day of the expiry month
    if year < now.year or (year == now.year and month < now.month):
        return False, "This card has expired."
    return True, None


def validate_card_number(card_number: str) -> Tuple[bool, Optional[str]]:
    """Validate card number format and Luhn check."""
    digits = re.sub(r"\D", "", card_number)
    if len(digits) < 13 or len(digits) > 19:
        return False, "Card number must be between 13 and 19 digits."
    if not validate_luhn(digits):
        return False, "Card number is invalid (failed Luhn check)."
    return True, None




#amount validator -->
def validate_amount(amount: float, balance: float) -> Tuple[bool, Optional[str]]:
    """
    Validate payment amount:
    - Must be positive
    - Must not exceed 2 decimal places
    - Must not exceed outstanding balance
    """
    if amount <= 0:
        return False, "Amount must be greater than zero."
    # Check decimal precision
    if round(amount, 2) != amount or len(str(amount).split(".")[-1]) > 2:
        # round trip check
        if abs(amount - round(amount, 2)) > 1e-9:
            return False, "Amount must have at most 2 decimal places."
    if amount > balance:
        return False, f"Amount (₹{amount:,.2f}) exceeds your outstanding balance of ₹{balance:,.2f}."
    return True, None



#identity verification -->
def verify_identity(
    provided_name: str,
    secondary_type: str,
    secondary_value: str,
    account_data: dict,
) -> bool:
    """
    Verify user identity using strict matching.
    
    Rule:
      full_name must match EXACTLY (case-sensitive, no trimming beyond outer whitespace)
      AND at least one secondary factor must match:
        - dob (YYYY-MM-DD)
        - aadhaar_last4 (4 digits)
        - pincode (6 digits)

    Returns True only if BOTH conditions are satisfied.
    NEVER exposes account data externally.
    """
    # Strict name check — case-sensitive, exact match after stripping outer whitespace
    if provided_name.strip() != account_data.get("full_name", "").strip():
        return False

    # Secondary factor check
    if secondary_type == "dob":
        return secondary_value.strip() == account_data.get("dob", "")
    elif secondary_type == "aadhaar_last4":
        return secondary_value.strip() == str(account_data.get("aadhaar_last4", ""))
    elif secondary_type == "pincode":
        return secondary_value.strip() == str(account_data.get("pincode", ""))

    return False



#data validation -->
def validate_date_string(date_str: str) -> Tuple[bool, Optional[str]]:
    """
    Strictly parse a date string in YYYY-MM-DD format.
    Handles leap year edge cases (e.g. 1988-02-29 is valid).
    """
    try:
        datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return True, None
    except ValueError:
        return False, f"'{date_str}' is not a valid date in YYYY-MM-DD format."
