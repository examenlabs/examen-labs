# Examen Labs — Evaluation Methodology

## Core Principle: Bijection

A task is bijective when:
- Every requirement in the prompt has exactly one corresponding verifier check
- Every verifier check corresponds to exactly one stated requirement
- Nothing is tested that wasn't promised
- Nothing promised is left untested

This eliminates two failure modes:
1. **Under-verification** — the model passes by doing half the work
2. **Over-verification** — the model fails on requirements it was never told about

## Verifier Design

All verifiers are standalone Python scripts that:
- Take no external configuration
- Return exit code 0 (pass) or non-zero (fail)
- Print a human-readable reason for failure
- Check executable outputs, not just file existence

## Scope-Down Protocol

When the full original environment cannot be reproduced, a `scope.md` must answer:
1. What is the original scope?
2. What is the reduced scope?
3. Why is the reduction necessary?
4. Why does the reduction preserve the capability target?

A reduction is only valid if the core capability being measured is unchanged.

## Task Structure

Every task ships with five documents:
- `prompt.md` — what the model sees
- `problem.md` — human-readable problem statement
- `requirements.md` — explicit hard requirements list
- `scope.md` — reduction justification
- `review.md` — quality assessment of the bundle
