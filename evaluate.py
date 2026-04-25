"""
evaluate.py — Automated evaluation suite for the Payment Collection Agent.

Runs test scenarios, checks correctness, and prints a summary report.
Usage: python evaluate.py
"""

import json
import time
from agent import Agent



def run_scenario(name: str, turns: list[tuple[str, list[str]]]) -> dict:
    """
    Run a single test scenario.

    turns: list of (user_input, expected_keywords_in_response)
      - expected_keywords: ALL must appear (case-insensitive) in the agent response
        for that turn to be considered correct.

    Returns a result dict.
    """
    agent = Agent()
    results = []
    passed = 0

    print('\n')
    print(f"  SCENARIO: {name}")
    print('\n')

    for i, (user_input, expected_keywords) in enumerate(turns):
        response = agent.next(user_input)
        msg = response["message"]

        # Check all expected keywords
        missing = [kw for kw in expected_keywords if kw.lower() not in msg.lower()]
        turn_passed = len(missing) == 0

        status = "✓ PASS" if turn_passed else "✗ FAIL"
        if turn_passed:
            passed += 1

        print(f"\n  Turn {i+1}: [{status}]")
        print(f"  User  : {user_input!r}")
        print(f"  Agent : {msg[:200]}{'...' if len(msg) > 200 else ''}")
        if missing:
            print(f"  Missing keywords: {missing}")

        results.append({
            "turn": i + 1,
            "user_input": user_input,
            "agent_response": msg,
            "passed": turn_passed,
            "missing_keywords": missing,
        })

        time.sleep(0.3)  # small delay between API calls

    total = len(turns)
    score = f"{passed}/{total}"
    print(f"\n  Result: {score} turns passed")

    return {
        "scenario": name,
        "passed": passed,
        "total": total,
        "turns": results,
    }



SCENARIOS = [

    # 1. Happy path —-> full successful payment
    ("Happy Path: Full Payment", [
        ("Hi there",
         ["account", "id"]),
        ("My account ID is ACC1001",
         ["name", "identity", "verify"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("My DOB is 1990-05-14",
         ["verified", "1,250.75"]),
        ("I'd like to pay 500",
         ["card"]),
        ("Name: Nithin Jain, card 4532015112830366, CVV 123, expiry 12/2027",
         ["successful", "transaction"]),
    ]),

    # 2. Partial payment --> still a happy path 
    ("Happy Path: Partial Payment", [
        ("ACC1001",
         ["name", "identity", "verify"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("Pincode 400001",
         ["verified", "1,250.75"]),
        ("Pay 200",
         ["card"]),
        ("Nithin Jain, 4532015112830366, 123, 12 2027",
         ["successful", "transaction"]),
    ]),

    # 3. Verification failure —-> exhausts all retries
    ("Verification Failure: Exhausted Retries", [
        ("ACC1001",
         ["name", "identity"]),
        ("Wrong Name",
         ["verify", "attempt"]),  # 1st fail
        ("Also Wrong Name, DOB 1990-05-14",
         ["attempt"]),             # 2nd fail
        ("Bad Name Again, pincode 400001",
         ["closed", "support"]),   # 3rd fail → close
    ]),

    # 4. Account not found --> not a happy path 
    ("Account Not Found", [
        ("ACC9999",
         ["couldn't find", "account"]),
        ("ACC1001",
         ["name", "identity"]),
    ]),

    # 5. Invalid card —-> Luhn check fails --> not a happy path
    ("Payment Failure: Invalid Card Number", [
        ("ACC1001",
         ["name", "identity"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("DOB 1990-05-14",
         ["verified", "balance"]),
        ("500",
         ["card"]),
        ("Nithin Jain, 1234567890123456, 123, 12/2027",
         ["invalid", "card"]),
    ]),

    # 6. Expired card --> also not a happy path 
    ("Payment Failure: Expired Card", [
        ("ACC1001",
         ["name", "identity"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("Aadhaar last 4 is 4321",
         ["verified", "balance"]),
        ("Pay full amount 1250.75",
         ["card"]),
        ("Nithin Jain, 4532015112830366, 123, 01/2020",
         ["expir"]),
    ]),

    # 7. Zero balance account --> can be a edge case 
    ("Edge Case: Zero Balance (ACC1003)", [
        ("ACC1003",
         ["name", "identity"]),
        ("Priya Agarwal",
         ["dob", "aadhaar", "pincode"]),
        ("DOB 1992-08-10",
         ["verified", "0.00", "nothing"]),
    ]),

    # 8. leap year DOB edge case --> 
    ("Edge Case: Leap Year DOB ACC1004 (valid date)", [
        ("ACC1004",
         ["name", "identity"]),
        ("Rahul Mehta",
         ["dob", "aadhaar", "pincode"]),
        ("DOB 1988-02-29",
         ["verified", "3,200.50"]),
    ]),

    # 9. matching the long name --> can be a edge case 
    ("Edge Case: Long Name ACC1002", [
        ("ACC1002",
         ["name", "identity"]),
        ("Rajarajeswari Balasubramaniam",
         ["dob", "aadhaar", "pincode"]),
        ("Pincode 400002",
         ["verified", "540.00"]),
    ]),

    # 10. Out-of-order info — user provides account ID in first message
    ("Out-of-Order: Account ID Given at Greeting", [
        ("Hi, my account ID is ACC1001",
         ["name", "verify"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("DOB 1990-05-14",
         ["verified", "balance"]),
    ]),

    # 11. Insufficient balance --> also not a happy path
    ("Payment Failure: Insufficient Balance", [
        ("ACC1001",
         ["name", "identity"]),
        ("Nithin Jain",
         ["dob", "aadhaar", "pincode"]),
        ("DOB 1990-05-14",
         ["verified", "balance"]),
        ("5000",
         ["invalid", "balance", "amount"]),
    ]),

    # 12. Case-sensitive name mismatch -->  case sensitive name matching must not occur 
    ("Verification Failure: Case-Sensitive Name", [
        ("ACC1001",
         ["name", "identity"]),
        ("nithin jain",  # lowercase — must fail
         ["dob", "aadhaar", "pincode", "attempt"]),
    ]),

    
    ("Ambiguity: Early Amount Before Verification", [
        ("ACC1001",
         ["name", "identity"]),
        ("I want to pay 500 right now",
         ["full name", "verify"]),
        ("Nithin Jain, DOB 1990-05-14",
         ["verified", "1,250.75"]),
    ]),

    # 14. Ambiguity: user provides card details before amount-->
    ("Ambiguity: Card Details Before Amount", [
        ("ACC1001",
         ["name", "identity"]),
        ("Nithin Jain, pincode 400001",
         ["verified", "balance"]),
        ("My card is 4532015112830366, cvv 123, expiry 12/2027, name Nithin Jain",
         ["how much", "pay"]),
    ]),

    # 15. Ambiguity: leap year nearby date should fail strict verification --> e.g Rahul Mehta's DOB is 1988-02-29 , if he enters 1988-02-28 , it must fail to verify the identity 
    ("Ambiguity: Leap Year Nearby DOB Mismatch", [
        ("ACC1004",
         ["name", "identity"]),
        ("Rahul Mehta, DOB 1988-02-28",
         ["attempt"]),
    ]),
]




def main(): # this fn runs the evaluation process 
    print('\n')
    print("  PAYMENT AGENT — EVALUATION SUITE")
    print('\n')

    all_results = []
    total_scenarios = len(SCENARIOS)
    passed_scenarios = 0

    for scenario_name, turns in SCENARIOS:
        result = run_scenario(scenario_name, turns)
        all_results.append(result)
        if result["passed"] == result["total"]:
            passed_scenarios += 1

    # Summary
    print('\n')
    print("  EVALUATION SUMMARY")
    print('\n')
    print(f"  Scenarios passed: {passed_scenarios}/{total_scenarios}")

    total_turns = sum(r["total"] for r in all_results)
    total_passed_turns = sum(r["passed"] for r in all_results)
    turn_rate = total_passed_turns / total_turns * 100 if total_turns else 0
    print(f"  Turns passed    : {total_passed_turns}/{total_turns} ({turn_rate:.1f}%)")

    print("\n  Per-scenario results:")
    for r in all_results:
        status = "✓" if r["passed"] == r["total"] else "✗"
        print(f"    {status} {r['scenario']}: {r['passed']}/{r['total']}")

    # Save JSON report --> to save the results of the evaluation process.
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print("\n  Full report saved to eval_results.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
