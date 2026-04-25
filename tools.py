
import requests
from typing import Union

BASE_URL = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com"
TIMEOUT = 10 


def lookup_account(account_id: str) -> dict:
    """
    POST /api/lookup-account
    Returns account data dict on success, or error dict on failure.
    
    Success keys: account_id, full_name, dob, aadhaar_last4, pincode, balance
    Error keys: error_code, message
    """
    url = f"{BASE_URL}/api/lookup-account"
    try:
        response = requests.post(
            url,
            json={"account_id": account_id},
            timeout=TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return {"error_code": "account_not_found", "message": "No account found with the provided account_id."}
        else:
            # Unexpected status
            return {
                "error_code": "api_error",
                "message": f"Unexpected server response: {response.status_code}",
            }
            #fault tolerance -->
    except requests.exceptions.Timeout:
        return {"error_code": "network_error", "message": "Request timed out. Please try again."}
    except requests.exceptions.ConnectionError:
        return {"error_code": "network_error", "message": "Could not connect to the server."}
    except Exception as e:
        return {"error_code": "network_error", "message": str(e)}


def process_payment(account_id: str, amount: float, card_data: dict) -> dict:
    """
    POST /api/process-payment
    
    card_data must contain:
        cardholder_name, card_number, cvv, expiry_month (int), expiry_year (int)
    
    Returns:
        Success: {"success": True, "transaction_id": "txn_..."}
        Failure: {"success": False, "error_code": "..."}
    """
    url = f"{BASE_URL}/api/process-payment"
    payload = {
        "account_id": account_id,
        "amount": round(amount, 2),
        "payment_method": {
            "type": "card",
            "card": {
                "cardholder_name": card_data["cardholder_name"],
                "card_number": card_data["card_number"],
                "cvv": card_data["cvv"],
                "expiry_month": int(card_data["expiry_month"]),
                "expiry_year": int(card_data["expiry_year"]),
            },
        },
    }
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 422:
            return response.json()  
        else:
            return {
                "success": False,
                "error_code": "api_error",
                "message": f"Unexpected server response: {response.status_code}",
            }
            #fault tolerance -->
    except requests.exceptions.Timeout:
        return {"success": False, "error_code": "network_error", "message": "Request timed out."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error_code": "network_error", "message": "Could not connect to the server."}
    except Exception as e:
        return {"success": False, "error_code": "network_error", "message": str(e)}
