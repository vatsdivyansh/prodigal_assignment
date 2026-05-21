import os
import json
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

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_VERIFY_ATTEMPTS = 3

SYSTEM_PROMPT = """
You are a professional, friendly payment collection assistant for a financial company.
You must follow a strict policy to collect payments from users. 

CRITICAL POLICY & WORKFLOW:
1. Greeting: Always greet the user and ask for their "Account ID" to get started. 
   - Keywords required: You MUST use the words "account" and "id" in your greeting.
2. Account Lookup: When the user provides an account ID, call the `lookup_account` tool.
   - If the tool returns that the account couldn't be found, tell the user you "couldn't find" the "account".
   - If the tool returns a network error, tell the user to try again.
   - If the tool finds the account, you MUST tell the user you need to "verify" their "identity" and ask for their "full name" as registered.
3. Verification: When the user provides their name (or name + secondary factor), call the `verify_identity` tool immediately.
   - If the tool says the name is correct but asks for a secondary factor, ask the user for ONE secondary factor. You MUST mention "DOB", "Aadhaar", and "pincode" in your response.
   - If the tool returns `success: false`, you MUST tell the user that the "verify" failed and state the number of remaining "attempt" (you MUST use the exact word "verify" and the exact word "attempt"). If 0 attempts remain, tell them the session is "closed" and to contact "support".
   - If the tool says `success: True` and provides the balance, you MUST say "verified".
   - If balance is 0.00, say "0.00" and that there is "nothing" to pay, then end the session.
   - If balance > 0, state the balance (e.g., "1,250.75") and ask "how much" they would like to "pay".
4. Amount Collection: Once verified and the user states an amount, ensure they have stated an amount before proceeding. If they give card details before an amount, explicitly ask "how much" they want to "pay".
   - If they state an amount > balance, tell them "invalid amount", "exceeds balance".
   - Otherwise, acknowledge the amount and ask for their "card" details (Name, Card Number, CVV, Expiry).
5. Payment Processing: Once you have all 4 card details and the amount, call the `process_payment` tool.
   - If the tool returns an error about card validity, tell them it's an "invalid card".
   - If the tool returns an error about expiry, tell them it's "expired" or "expir".
   - If the tool returns success, say payment was "successful" and provide the "transaction" ID.

IMPORTANT RULES:
- Never assume the user's name or DOB. Wait for them to provide it.
- If the user provides info out of order (e.g. amount before verifying), acknowledge it but insist on completing the current step (e.g., ask for "full name" and "verify").
- Never expose the user's full name, DOB, or other PII returned by internal systems to the user unless they provided it first.
- Maintain a polite and professional tone.
"""

class Agent:
    def __init__(self):
        self._client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self._history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._account_data = None
        self._verify_attempts = 0
        self._verified = False
        self._payment_amount = None

    def next(self, user_input: str) -> dict:
        self._history.append({"role": "user", "content": user_input})
        
        while True:
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=self._history,
                tools=self._get_tools(),
                tool_choice="auto",
                temperature=0,
                max_tokens=300,
            )
            
            message = response.choices[0].message
            
            # If the model wants to call tools
            if message.tool_calls:
                self._history.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        } for tc in message.tool_calls
                    ]
                })
                
                for tool_call in message.tool_calls:
                    function_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    
                    if function_name == "lookup_account":
                        result = self._tool_lookup_account(**arguments)
                    elif function_name == "verify_identity":
                        result = self._tool_verify_identity(**arguments)
                    elif function_name == "process_payment":
                        result = self._tool_process_payment(**arguments)
                    else:
                        result = {"error": f"Unknown tool: {function_name}"}
                        
                    self._history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": json.dumps(result)
                    })
            else:
                # Normal text response
                self._history.append({"role": "assistant", "content": message.content})
                return {"message": message.content}

    def _get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "lookup_account",
                    "description": "Look up an account by its ID. Call this when the user provides an account ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "account_id": {"type": "string", "description": "The account ID, e.g., ACC1001"}
                        },
                        "required": ["account_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "verify_identity",
                    "description": "Verify the user's identity using their name and one secondary factor (DOB, Aadhaar last 4, or Pincode).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provided_name": {"type": "string", "description": "Full name provided by the user"},
                            "secondary_type": {"type": "string", "enum": ["dob", "aadhaar_last4", "pincode"]},
                            "secondary_value": {"type": "string", "description": "The value of the secondary factor provided"}
                        },
                        "required": ["provided_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "process_payment",
                    "description": "Process a payment after the user has been verified and provided card details.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Amount to pay"},
                            "cardholder_name": {"type": "string", "description": "Name on the card"},
                            "card_number": {"type": "string", "description": "Card number digits"},
                            "cvv": {"type": "string", "description": "CVV security code"},
                            "expiry_month": {"type": "integer", "description": "Expiry month (1-12)"},
                            "expiry_year": {"type": "integer", "description": "Expiry year (e.g. 2027)"}
                        },
                        "required": ["amount", "cardholder_name", "card_number", "cvv", "expiry_month", "expiry_year"]
                    }
                }
            }
        ]

    def _tool_lookup_account(self, account_id: str) -> dict:
        result = lookup_account(account_id)
        if result.get("error_code"):
            return {"success": False, "error": result["message"]}
        
        # Cache the sensitive data securely
        self._account_data = result
        self._verify_attempts = 0
        self._verified = False
        
        # Return a safe summary to the LLM
        return {"success": True, "message": "Account found. Please verify identity next."}

    def _tool_verify_identity(self, provided_name: str, secondary_type: str = None, secondary_value: str = None) -> dict:
        if not self._account_data:
            return {"success": False, "error": "No active account. Please lookup account first."}
            
        if self._verified:
            return {"success": True, "balance": self._account_data["balance"], "message": "Already verified."}
            
        # Strict name check first
        if provided_name.strip() != self._account_data.get("full_name", "").strip():
            self._verify_attempts += 1
            remaining = MAX_VERIFY_ATTEMPTS - self._verify_attempts
            return {"success": False, "error": "Verification failed. Name is incorrect.", "attempts_remaining": remaining}
            
        # Name is correct. If no secondary factor provided, just ask for it.
        if not secondary_type or not secondary_value:
            return {"success": True, "message": "Name is correct. Now ask for secondary factor (DOB, Aadhaar, or Pincode). Do NOT say verification is complete."}
            
        # Both provided, check secondary factor
        passed = verify_identity(
            provided_name=provided_name,
            secondary_type=secondary_type,
            secondary_value=secondary_value,
            account_data=self._account_data
        )
        
        if passed:
            self._verified = True
            return {"success": True, "balance": self._account_data["balance"]}
            
        self._verify_attempts += 1
        remaining = MAX_VERIFY_ATTEMPTS - self._verify_attempts
        return {"success": False, "error": "Verification failed. Secondary factor is incorrect.", "attempts_remaining": remaining}

    def _tool_process_payment(self, amount: float, cardholder_name: str, card_number: str, cvv: str, expiry_month: int, expiry_year: int) -> dict:
        if not self._verified or not self._account_data:
            return {"success": False, "error": "User must be verified before payment."}
            
        # Amount validation
        ok, err = validate_amount(amount, self._account_data["balance"])
        if not ok:
            return {"success": False, "error": f"Invalid amount: {err}"}
            
        # Card validation
        clean_card = card_number.replace(" ", "").replace("-", "")
        ok, err = validate_card_number(clean_card)
        if not ok:
            return {"success": False, "error": "Invalid card number."}
            
        ok, err = validate_cvv(str(cvv), clean_card)
        if not ok:
            return {"success": False, "error": f"Invalid CVV: {err}"}
            
        ok, err = validate_expiry(expiry_month, expiry_year)
        if not ok:
            return {"success": False, "error": f"Expired card: {err}"}
            
        # Call actual API
        card_data = {
            "cardholder_name": cardholder_name,
            "card_number": clean_card,
            "cvv": str(cvv),
            "expiry_month": expiry_month,
            "expiry_year": expiry_year,
        }
        
        result = process_payment(self._account_data["account_id"], amount, card_data)
        
        if result.get("success"):
            return {"success": True, "transaction_id": result.get("transaction_id")}
            
        return {"success": False, "error": result.get("message", result.get("error_code"))}
