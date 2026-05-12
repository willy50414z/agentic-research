---
name: trade-strategy-prototype-design
description: Formalize strategy entry, exit, regime, and risk logic before implementation. Use when translating a trading idea into a precise, auditable prototype specification.
---

# Trade Strategy Prototype Design Skill

Formalize trading logic specify before implementation.

## Core Standards

- Save the design spec in a strategy-specific research area defined by the host repository.
- If downstream analysis depends on debug columns or intermediate signals, list them explicitly in the spec.

## Required Work

- Define Entry/Exit conditions precisely.
- Define **Risk Circuit Breaker**: When should the strategy stop trading?
- Define Regime Filters: (e.g., only trade when ADX > 25).
- List all hyper-parameters to be tuned later.

## General Decision Rules

- Do not implement if the entry, exit, and stop conditions are still ambiguous in plain language.
- Do not proceed if the prototype has no explicit rule for stopping or reducing risk during abnormal behavior.
- Do not mark a parameter as tunable unless its trading meaning is understood well enough to justify why it exists.
