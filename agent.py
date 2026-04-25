import re
import os
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

from groq import Groq
from dotenv import load_dotenv

from tools import lookup_account, process_payment
from validators import (
    validate_luhn,
    validate_cvv,
    validate_expiry,
    validate_card_number,
    validate_amount,
    verify_identity,
    validate_date_string,
)

load_dotenv()



#state machine
class Phase(str, Enum):
    GREETING          = "greeting"
    AWAITING_ACCOUNT  = "awaiting_account"
    VERIFYING         = "verifying"
    PAYMENT_INTRO     = "payment_intro"
    COLLECTING_CARD   = "collecting_card"
    CLOSED            = "closed"


@dataclass
class ConversationState:
    phase: Phase = Phase.GREETING

    # Account
    account_id: Optional[str] = None
    account_data: Optional[dict] = None

    # Verification
    verified: bool = False
    verify_attempts: int = 0
    provided_name: Optional[str] = None  # name provided in current attempt

    # Payment
    payment_amount: Optional[float] = None

    # Card (cleared after payment attempt)
    cardholder_name: Optional[str] = None
    card_number: Optional[str] = None
    cvv: Optional[str] = None
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None


MAX_VERIFY_ATTEMPTS = 3
GROQ_MODEL = "llama-3.3-70b-versatile"

# User-facing error messages for API error codes
PAYMENT_ERROR_MESSAGES = {
    "insufficient_balance": "The amount exceeds your outstanding balance. Please enter a lower amount.",
    "invalid_amount":       "The amount is invalid — it must be positive with at most 2 decimal places.",
    "invalid_card":         "Your card number is invalid. Please re-enter your card number.",
    "invalid_cvv":          "Your CVV is incorrect. Please re-enter your CVV.",
    "invalid_expiry":       "Your card's expiry date is invalid or the card has expired.",
    "network_error":        "A network error occurred. Please try again.",
}

# Which errors are user-fixable (retryable)
RETRYABLE_ERRORS = {"insufficient_balance", "invalid_amount", "invalid_card", "invalid_cvv", "invalid_expiry", "network_error"}


#agent -->
class Agent:
    """
    Conversational payment collection agent.
    Exposes a single public method: next(user_input: str) -> {"message": str}
    """

    def __init__(self):
        self._client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self._state = ConversationState()
        self._history: list[dict] = []  # LLM conversation history

    #public interface 
    def next(self, user_input: str) -> dict:
        """Process one turn of conversation. Returns {"message": str}."""
        self._history.append({"role": "user", "content": user_input})
        response = self._dispatch(user_input)
        self._history.append({"role": "assistant", "content": response})
        return {"message": response}

    
    #dispatcher -->
    def _dispatch(self, user_input: str) -> str:
        phase = self._state.phase
        if phase == Phase.GREETING:
            return self._handle_greeting(user_input)
        elif phase == Phase.AWAITING_ACCOUNT:
            return self._handle_awaiting_account(user_input)
        elif phase == Phase.VERIFYING:
            return self._handle_verifying(user_input)
        elif phase == Phase.PAYMENT_INTRO:
            return self._handle_payment_intro(user_input)
        elif phase == Phase.COLLECTING_CARD:
            return self._handle_collecting_card(user_input)
        elif phase == Phase.CLOSED:
            return "This session is closed. Please start a new session to make another payment."
        return "Something went wrong. Please start over."

    
    #phase handlers
    def _handle_greeting(self, user_input: str) -> str:
        """Greet user; extract account ID if already provided."""
        account_id = self._extract_account_id(user_input)
        if account_id:
            self._state.account_id = account_id
            self._state.phase = Phase.AWAITING_ACCOUNT
            return self._do_account_lookup()
        self._state.phase = Phase.AWAITING_ACCOUNT
        return self._llm_reply(
            "You are a professional, friendly payment collection assistant for a financial company. "
            "Greet the user warmly and ask for their Account ID to get started. Be brief and clear."
        )

    def _handle_awaiting_account(self, user_input: str) -> str:
        """Extract account ID and call lookup API."""
        account_id = self._extract_account_id(user_input)
        if not account_id:
            return (
                "I couldn't find an account ID in your message. "
                "Could you please share your Account ID? It looks like ACC1001."
            )
        self._state.account_id = account_id
        return self._do_account_lookup()

    def _do_account_lookup(self) -> str:
        """Call /api/lookup-account and transition state."""
        requested_account_id = self._state.account_id
        result = lookup_account(self._state.account_id)

        if result.get("error_code") == "account_not_found":
            self._state.account_id = None
            return (
                f"I couldn't find an account with ID '{requested_account_id or ''}'. "
                "Please double-check your Account ID and try again."
            )
        if result.get("error_code") == "network_error":
            self._state.account_id = None
            return (
                "I'm having trouble reaching our servers right now. "
                "Please try again in a moment."
            )

        
        self._state.account_data = result
        self._state.phase = Phase.VERIFYING

        return (
            "I've located your account. For your security, I need to verify your identity first.\n\n"
            "Please provide your **full name** as registered with us."
        )

    def _handle_verifying(self, user_input: str) -> str:
        """Collect name + secondary factor and verify identity."""
        extracted = self._extract_verification_fields(user_input)
        if any(v is None for v in extracted.values()):
            llm_extracted = self._extract_json(user_input, {
                "full_name": "Person's full name (string or null)",
                "dob": "Date of birth in YYYY-MM-DD format (string or null)",
                "aadhaar_last4": "Last 4 digits of Aadhaar as a string (or null)",
                "pincode": "6-digit pincode/postal code as a string (or null)",
            })
            for key, value in llm_extracted.items():
                if extracted.get(key) is None and value is not None:
                    extracted[key] = value

        # Accept name if not yet stored for this attempt
        if extracted.get("full_name") and not self._state.provided_name:
            self._state.provided_name = extracted["full_name"].strip()

        if not self._state.provided_name:
            return (
                "To verify your identity, I'll need your full name as registered with us. "
                "Could you please provide it?"
            )

        # Strict name check first; wrong name counts as a failed attempt.
        if self._state.provided_name != self._state.account_data.get("full_name", ""):
            return self._register_verification_failure()

        # Look for a secondary factor
        secondary_type, secondary_value = None, None
        if extracted.get("dob"):
            raw_dob = extracted["dob"].strip()
            ok, err = validate_date_string(raw_dob)
            if not ok:
                return (
                    f"The date '{raw_dob}' doesn't appear to be valid ({err}). "
                    "Please use the format YYYY-MM-DD."
                )
            secondary_type, secondary_value = "dob", raw_dob
        elif extracted.get("aadhaar_last4"):
            secondary_type = "aadhaar_last4"
            secondary_value = str(extracted["aadhaar_last4"]).strip()
        elif extracted.get("pincode"):
            secondary_type = "pincode"
            secondary_value = str(extracted["pincode"]).strip()

        if not secondary_type:
            return (
                f"Thank you, {self._state.provided_name}. "
                "To complete verification, please provide **one** of:\n"
                "• DOB (YYYY-MM-DD)\n"
                "• Last 4 digits of your Aadhaar\n"
                "• Your registered pincode"
            )

        return self._attempt_verification(secondary_type, secondary_value)

    def _attempt_verification(self, secondary_type: str, secondary_value: str) -> str:
        """Run the strict verification check and handle retries."""
        passed = verify_identity(
            provided_name=self._state.provided_name,
            secondary_type=secondary_type,
            secondary_value=secondary_value,
            account_data=self._state.account_data,
        )

        if passed:
            self._state.verified = True
            balance = self._state.account_data["balance"]

            if balance == 0.0:
                self._state.phase = Phase.CLOSED
                return (
                    "✓ Identity verified.\n\n"
                    "Your account currently has an outstanding balance of **₹0.00** — "
                    "there is nothing to pay at this time. "
                    "Thank you for calling. Have a great day!"
                )

            self._state.phase = Phase.PAYMENT_INTRO
            return (
                f"✓ Identity verified successfully!\n\n"
                f"Your outstanding balance is **₹{balance:,.2f}**.\n"
                "How much would you like to pay today? "
                "You may pay the full amount or any partial amount."
            )

        return self._register_verification_failure()

    def _handle_payment_intro(self, user_input: str) -> str:
        """Collect and validate payment amount."""
        amount = self._extract_amount(user_input)
        if amount is None:
            extracted = self._extract_json(user_input, {
                "amount": "Payment amount as a number (e.g. 500 or 1250.75), or null if not mentioned",
            })
            raw_amount = extracted.get("amount")
            try:
                amount = float(raw_amount) if raw_amount is not None else None
            except (TypeError, ValueError):
                amount = None

        balance = self._state.account_data["balance"]
        if amount is None:
            return (
                f"Your outstanding balance is **₹{balance:,.2f}**. "
                "How much would you like to pay today?"
            )

        ok, err = validate_amount(amount, balance)
        if not ok:
            return f"Invalid amount — {err}\nPlease enter a valid payment amount."

        self._state.payment_amount = amount
        self._state.phase = Phase.COLLECTING_CARD

        return (
            f"Got it — **₹{amount:,.2f}** will be charged.\n\n"
            "Please provide your card details:\n"
            "• **Cardholder name** (as printed on card)\n"
            "• **Card number**\n"
            "• **CVV**\n"
            "• **Expiry date** (month and year)\n\n"
            "You can share all details in one message."
        )

    def _handle_collecting_card(self, user_input: str) -> str:
        """Collect card fields incrementally, validate, and process payment."""
        extracted = self._extract_card_fields(user_input)
        if any(v is None for v in extracted.values()):
            llm_extracted = self._extract_json(user_input, {
                "cardholder_name": "Name on the card (string or null)",
                "card_number":     "Card number digits only (string or null)",
                "cvv":             "CVV security code (string or null)",
                "expiry_month":    "Expiry month as integer 1-12 (or null)",
                "expiry_year":     "Expiry year as 4-digit integer e.g. 2027 (or null)",
            })
            for key, value in llm_extracted.items():
                if extracted.get(key) is None and value is not None:
                    extracted[key] = value

        s = self._state

        if extracted.get("cardholder_name"):
            s.cardholder_name = extracted["cardholder_name"].strip()
        if extracted.get("card_number"):
            s.card_number = re.sub(r"[\s\-]", "", str(extracted["card_number"]))
        if extracted.get("cvv"):
            s.cvv = str(extracted["cvv"]).strip()
        if extracted.get("expiry_month") is not None:
            try:
                s.expiry_month = int(extracted["expiry_month"])
            except (TypeError, ValueError):
                pass
        if extracted.get("expiry_year") is not None:
            try:
                s.expiry_year = int(extracted["expiry_year"])
            except (TypeError, ValueError):
                pass

        # Identify missing fields
        missing = []
        if not s.cardholder_name:  missing.append("cardholder name")
        if not s.card_number:      missing.append("card number")
        if not s.cvv:              missing.append("CVV")
        if not s.expiry_month:     missing.append("expiry month")
        if not s.expiry_year:      missing.append("expiry year")

        if missing:
            items = ", ".join(missing)
            return f"Still needed: **{items}**. Please provide these to complete your payment."

        # Validating all card fields before API call
        errors = []

        ok, err = validate_card_number(s.card_number)
        if not ok:
            errors.append(f"Card number: {err}")
            s.card_number = None

        if s.card_number:  # only validating  CVV if card number is valid (need it for Amex check)
            ok, err = validate_cvv(s.cvv, s.card_number)
            if not ok:
                errors.append(f"CVV: {err}")
                s.cvv = None

        ok, err = validate_expiry(s.expiry_month, s.expiry_year)
        if not ok:
            errors.append(f"Expiry: {err}")
            s.expiry_month = None
            s.expiry_year = None

        if errors:
            bullet_errors = "\n".join(f"• {e}" for e in errors)
            return (
                f"There are issues with your card details:\n{bullet_errors}\n\n"
                "Please provide the corrected information."
            )

        # Everything valid — call payment API
        return self._do_process_payment()

    def _do_process_payment(self) -> str:
        """Call /api/process-payment and handle the response."""
        s = self._state
        card_data = {
            "cardholder_name": s.cardholder_name,
            "card_number":     s.card_number,
            "cvv":             s.cvv,
            "expiry_month":    s.expiry_month,
            "expiry_year":     s.expiry_year,
        }

        result = process_payment(s.account_id, s.payment_amount, card_data)

        # Clear sensitive card data immediately
        s.card_number = None
        s.cvv = None

        if result.get("success"):
            txn_id = result.get("transaction_id", "N/A")
            s.phase = Phase.CLOSED
            return (
                f"✓ **Payment successful!**\n\n"
                f"• Amount paid: ₹{s.payment_amount:,.2f}\n"
                f"• Transaction ID: `{txn_id}`\n\n"
                "Please save your transaction ID for your records. "
                "Thank you for your payment! Have a great day."
            )

        error_code = result.get("error_code", "unknown")
        user_msg = PAYMENT_ERROR_MESSAGES.get(error_code, "An unexpected error occurred.")

        if error_code in RETRYABLE_ERRORS:
            # Reset the relevant field and stay in appropriate phase
            if error_code == "insufficient_balance":
                s.payment_amount = None
                s.phase = Phase.PAYMENT_INTRO
            elif error_code == "invalid_amount":
                s.payment_amount = None
                s.phase = Phase.PAYMENT_INTRO
            elif error_code == "invalid_card":
                s.card_number = None
            elif error_code == "invalid_cvv":
                s.cvv = None
            elif error_code == "invalid_expiry":
                s.expiry_month = None
                s.expiry_year = None

            return f"⚠ Payment failed: {user_msg}"
        else:
            s.phase = Phase.CLOSED
            return (
                f"⚠ Payment could not be processed: {user_msg}\n"
                "This session has been closed. "
                "Please contact our support team for further assistance."
            )

    def _register_verification_failure(self) -> str:
        """Increment verification attempts and return the next failure message."""
        self._state.verify_attempts += 1
        remaining = MAX_VERIFY_ATTEMPTS - self._state.verify_attempts
        self._state.provided_name = None

        if remaining <= 0:
            self._state.phase = Phase.CLOSED
            return (
                "I'm sorry — I was unable to verify your identity after "
                f"{MAX_VERIFY_ATTEMPTS} attempts. "
                "For your security, this session has been closed. "
                "Please contact our support team for assistance."
            )

        attempt_word = "attempt" if remaining == 1 else "attempts"
        return (
            "I wasn't able to verify your identity with the information provided.\n"
            f"You have **{remaining} {attempt_word}** remaining.\n\n"
            "Please provide your full name and one of:\n"
            "• DOB (YYYY-MM-DD)\n"
            "• Last 4 digits of your Aadhaar\n"
            "• Your registered pincode"
        )

   
    def _extract_json(self, user_input: str, fields: dict) -> dict:
        """
        Use LLM to extract structured fields from a natural-language user message.
        Returns a dict with keys from `fields`; missing values are None.
        """
        schema = json.dumps(fields, ensure_ascii=False)
        prompt = (
            f"Extract the following fields from the user message. "
            f"Return ONLY a valid JSON object with exactly these keys. "
            f"Use null for any field not present in the message.\n\n"
            f"Fields:\n{schema}\n\n"
            f'User message: "{user_input}"\n\n'
            "Return ONLY the JSON object."
        )
        try:
            resp = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=300,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception:
            return {k: None for k in fields}

    def _extract_verification_fields(self, text: str) -> dict:
        """Deterministically extract verification fields from user text."""
        out = {"full_name": None, "dob": None, "aadhaar_last4": None, "pincode": None}
        cleaned = text.strip()

        dob_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
        if dob_match:
            out["dob"] = dob_match.group(1)

        aadhaar_match = re.search(r"(?:aadhaar|aadhar)[^\d]*(\d{4})\b", cleaned, re.IGNORECASE)
        if aadhaar_match:
            out["aadhaar_last4"] = aadhaar_match.group(1)

        pincode_match = re.search(r"(?:pincode|pin|postal(?:\s*code)?)[^\d]*(\d{6})\b", cleaned, re.IGNORECASE)
        if pincode_match:
            out["pincode"] = pincode_match.group(1)

        # If user gives a pure name turn (letters/spaces only), treat it as full name.
        if re.fullmatch(r"[A-Za-z][A-Za-z\s'.-]{1,80}", cleaned):
            out["full_name"] = re.sub(r"\s+", " ", cleaned).strip()
        else:
            name_match = re.search(r"(?:name\s*(?:is|:)\s*)([A-Za-z][A-Za-z\s'.-]{1,80})", cleaned, re.IGNORECASE)
            if name_match:
                out["full_name"] = re.sub(r"\s+", " ", name_match.group(1)).strip()
            else:
                leading_segment = cleaned.split(",", 1)[0].strip()
                if (
                    re.fullmatch(r"[A-Za-z][A-Za-z\s'.-]{1,80}", leading_segment)
                    and not re.search(r"\b(dob|aadhaar|aadhar|pincode|pin|postal)\b", leading_segment, re.IGNORECASE)
                ):
                    out["full_name"] = re.sub(r"\s+", " ", leading_segment).strip()

        return out

    def _extract_amount(self, text: str) -> Optional[float]:
        """Deterministically extract the first plausible monetary amount."""
        cleaned = text.strip()
        normalized = cleaned.replace(",", "")

        # Accept plain numeric turns like "500" or "1250.75".
        if re.fullmatch(r"\d+(?:\.\d{1,3})?", normalized):
            try:
                return float(normalized)
            except ValueError:
                return None

        # Otherwise only parse numbers when user is clearly talking about amount.
        if not re.search(r"\b(pay|payment|amount)\b|₹|\brs\.?\b", normalized, re.IGNORECASE):
            return None

        m = re.search(r"(?:₹|rs\.?\s*)?(\d+(?:\.\d{1,3})?)", normalized, re.IGNORECASE)
        if not m:
            return None

        candidate = m.group(1)
        if len(candidate.split(".")[0]) > 7:
            return None
        try:
            return float(candidate)
        except ValueError:
            return None

    def _extract_card_fields(self, text: str) -> dict:
        """Deterministically extract card fields from free-form text."""
        out = {
            "cardholder_name": None,
            "card_number": None,
            "cvv": None,
            "expiry_month": None,
            "expiry_year": None,
        }

        card_match = re.search(r"\b(?:\d[\s-]*){13,19}\b", text)
        if card_match:
            out["card_number"] = re.sub(r"[\s-]", "", card_match.group(0))

        cvv_match = re.search(r"\bcvv[^\d]*(\d{3,4})\b", text, re.IGNORECASE)
        if cvv_match:
            out["cvv"] = cvv_match.group(1)

        expiry_match = re.search(r"\b(0?[1-9]|1[0-2])\s*[/\-\s]\s*(\d{2,4})\b", text)
        if expiry_match:
            month = int(expiry_match.group(1))
            year = int(expiry_match.group(2))
            if year < 100:
                year += 2000
            out["expiry_month"] = month
            out["expiry_year"] = year

        name_match = re.search(r"(?:name\s*(?:is|:)\s*)([A-Za-z][A-Za-z\s'.-]{1,80})", text, re.IGNORECASE)
        if name_match:
            out["cardholder_name"] = re.sub(r"\s+", " ", name_match.group(1)).strip()

        return out

    def _llm_reply(self, system_prompt: str) -> str:
        """Generate a friendly natural language response using full conversation history."""
        messages = [{"role": "system", "content": system_prompt}] + self._history
        try:
            resp = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0,
                max_tokens=300,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"I'm sorry, I encountered a technical issue. Please try again. ({e})"

    def _extract_account_id(self, text: str) -> Optional[str]:
        """
        Extract account ID using regex first (fast & reliable).
        Falls back to LLM if regex finds nothing.
        """
        match = re.search(r"\bACC\d{3,6}\b", text, re.IGNORECASE)
        if match:
            return match.group(0).upper()

        # LLM fallback for natural phrasing
        extracted = self._extract_json(text, {
            "account_id": "Account ID like ACC1001 (string or null)"
        })
        val = extracted.get("account_id")
        if val and re.match(r"^ACC\d{3,6}$", str(val).strip(), re.IGNORECASE):
            return str(val).strip().upper()
        return None
