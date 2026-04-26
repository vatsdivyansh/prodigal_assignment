# Submission Note — Payment Collection AI Agent
**Date:** 2026-04-26

## 1) Architecture selected:
Deterministic State Machine + Agentic LLM layer (not fully DSM-only and not fully Agent-only).
Pure DSM is too rigid for natural user language; pure agentic flow is too risky for verification and payment gating.  
This project uses a hybrid design:
- Deterministic state machine controls flow, policy, and all security-critical transitions.
- LLM is limited to natural-language extraction and response generation.
- All gating checks are enforced in Python for deterministic, auditable behavior.

### High-level architecture diagram
![High-level architecture](./diagrams/high-level-architecture.png)

## 2) Implementation
The implementation follows a deterministic state-driven orchestration centered on `Agent.next()`.
`ConversationState` and `Phase` enforce strict control flow, while `validators.py` handles rule-based checks and `tools.py` manages external API interactions.
The LLM layer is used only for natural-language extraction/response, with all critical decisions enforced in Python.

### Implementation architecture diagram



![Implementation Architecture](./diagrams/implementation-architecture.png)

## 3) Final Verdict (eval + readiness)
Evaluation results in this repository show full pass:
- 15/15 scenarios
- 53/53 turns
- 100% overall

For assignment scope, this solution is production-ready: deterministic policy-critical flow is enforced in code, conversational flexibility is retained through constrained LLM use, and error/edge-case handling is comprehensively covered by automated evaluation.

This score reflects full pass on the current deterministic keyword-based evaluation suite; broader robustness can be measured with semantic grading and adversarial tests.

## 5) How to Reproduce Evaluation

```powershell
$env:PYTHONUTF8='1'; python evaluate.py
```

Run with UTF-8 on Windows PowerShell to avoid Unicode display issues.

For non-Windows shells:

```bash
python evaluate.py
```

