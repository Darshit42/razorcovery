# Kickoff prompt — paste into Claude Code

---

## READ FIRST

Before doing anything else, inventory this repo:
- List the current directory structure.
- Read `PRD.md` in full.
- Read `CLAUDE.md` in full.
- Check for any existing code in `/data`, `/decision`, `/voice`, `/audit`,
  `/metrics` — do not assume the repo is empty, confirm it.
- Report back what already exists before proposing anything new.

## TASK

Build the MVP described in `PRD.md` §3 (In scope), starting with the
synthetic data generator and the decision layer + audit trail schema
(PRD §9, Day 1). Do not start on the LiveKit voice agent until the
decision layer and audit trail are working and I've reviewed them.

Specifically for this first pass:
1. Synthetic event generator producing 50+ events across the 3 failure
   types in PRD §3.
2. Audit trail schema (append-only) per PRD §6.
3. Decision layer: one clear, logged function per failure type that picks
   an intervention (voice / SMS / link-only), including the stopping-rule
   checks from PRD §7.
4. Basic tests proving stopping rules actually block a call (max attempts,
   explicit refusal, time window).

## OUT OF SCOPE — do not build these in this pass, or at all without a separate go-ahead

- The LiveKit voice agent itself (next pass, after review).
- Anything from PRD §4 (Out of scope) — no fraud/risk scoring, no real
  payment processing, no multi-tenant auth, no UI polish.
- Postgres migration — stay on SQLite unless I explicitly say otherwise.
- Metrics dashboard — comes after the audit trail is proven correct.
- Any dependency or service not already named in `CLAUDE.md`.

## CONFIRM BEFORE CODING

Do not write any code yet. First give me:
1. Your inventory findings (READ FIRST).
2. A short plan: files you'll create, the schema for the audit trail, and
   the function signatures for the decision layer.
3. Anything in the PRD that's ambiguous or where you'd default to an
   assumption — flag it, don't silently pick one.

Wait for my confirmation before writing any code.
