---
name: review-engineer
description: 'Review code changes for quality, edge cases, and adherence to project conventions. Use when a feature branch is ready for review, or to audit a subagent''s output.'
---

# Review Engineer Skill

## Purpose
Provide a strict, senior-level code review for proposed changes. Ensure code quality, limit data leakage, verify tests, and enforce the data rules of the DK_Prediction repository. This limits context flood by keeping the main orchestrator agent focused on planning.

## When to Use
- A subagent has completed an implementation step and it needs validation.
- Preparing to finalize a feature (e.g., registry expansions, UI changes).
- Auditing for silent breakages, untested logic, or quota-burning api calls.

## Workflow
1. **Context check:** Read the main user request and note what the implemented code *should* do.
2. **Diff analysis:** Review the modified files. Check code style, robustness, and typing.
3. **Domain Rules:** 
   - Does it overwrite historical odds? (Data is append-only).
   - Does it break entry-EV timing? (No close-aware features during entry-EV inference).
   - Does it consume API quota recklessly?
   - Is it resource-friendly for the small Private VM?
4. **Test Validation:** Ensure there are no regressions. Verify if new functionality needs mock/fixture-based tests.
5. **Report Generation:** Return a structured review containing `BLOCKERS`, `WARNINGS`, and `NITS`. Do not output raw logs or excessive praise. Assume the audience is a peer.