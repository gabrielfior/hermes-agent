---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions grill me.
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [Productivity, Planning, Design, Review, Interview]
---

# grill-me

Stress-test a plan or design by interviewing the user relentlessly until you reach a shared understanding. Walk down each branch of the decision tree, resolving dependencies between decisions one-by-one.

## When to Use

- User says grill me or asks to be challenged on a plan
- Stress-testing a design, architecture, or strategy
- Before committing to a complex plan — surface hidden assumptions
- User wants to validate that nothing was missed

## Procedure

1. **Start with the top-level goal.** Confirm the plan or design the user wants grilled.
2. **Walk the decision tree depth-first.** For each branch:
   - Ask one focused question at a time
   - Wait for the user's answer before proceeding
   - If a question can be answered by exploring the codebase, explore the codebase instead of asking
3. **Resolve dependencies.** Before moving to a dependent decision, confirm the prerequisite decision is settled.
4. **Offer your recommendation.** For each question, provide your recommended answer after the user responds.
5. **Continue until shared understanding.** Stop when the user confirms all branches are resolved and no open questions remain.

## Pitfalls

- **Asking multiple questions at once** — breaks the one-at-a-time rule and overwhelms the user
- **Skipping branches** — surface-level grilling misses hidden assumptions
- **Not offering recommendations** — the user benefits from your perspective, not just interrogation
- **Exploring the codebase when unnecessary** — only use codebase exploration when it directly answers the question

## Verification

- User confirms shared understanding
- All branches of the decision tree have been resolved
- No unresolved dependencies remain
- User can summarize the final plan back to you
