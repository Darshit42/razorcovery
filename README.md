# razorcovery

Hinglish Voice Recovery Agent — Razorpay AI Buildathon, Track 03.
See `PRD.md` for scope, `CLAUDE.md` for working rules.

## Status

- [x] Synthetic failure-event generator (3 failure types, 50+ events)
- [x] Append-only audit trail (Postgres)
- [x] Decision layer: one logged function per failure type + stopping rules
- [x] Tests: stopping rules provably block calls
- [x] LiveKit Hinglish voice agent — first pass (Gemini Live)
- [x] Metrics view — FastAPI + one HTML page, computed from the audit trail
- [ ] Telephony provider + one live call end-to-end
- [ ] Day-3 live pilot: real calls replace simulated batch outcomes

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env          # fill DATABASE_URL, GOOGLE_API_KEY, LIVEKIT_*
```

Postgres (local alternative to RDS, one-time, as a superuser):

```sql
CREATE ROLE razorcovery WITH LOGIN PASSWORD 'devpass';
CREATE DATABASE razorcovery OWNER razorcovery;
```

## Run

```bash
python -m data.generate_events                       # build data/fixtures/events.json
python -m audit.db                                   # create the audit_log schema
python -m decision.run_batch --now 2026-09-02T12:00:00+05:30   # route + log every event
pytest -q                                            # 58 tests
```

Voice agent (joins a LiveKit room; needs LIVEKIT_* + GOOGLE_API_KEY):

```bash
python -m voice.agent dev                            # local worker
```

Metrics view (needs GOOGLE_API_KEY for `--real`; DATABASE_URL always):

```bash
python -m metrics.seed --reset --real 4              # populate outcomes
uvicorn metrics.app:app --port 8000                  # then open http://localhost:8000
```

## Layout

```
data/       synthetic event generator + pydantic schemas + fixtures
decision/   failure-type -> intervention routing, stopping rules, batch runner
audit/      append-only Postgres audit log (schema, writer, reader, export)
voice/      LiveKit Gemini-Live agent: prompt, flow (logged tools), dispatch
metrics/    FastAPI read-only metrics view over audit_log + one HTML page
tests/
```

## Decision model

| failure type          | rule summary                                                       |
|-----------------------|-------------------------------------------------------------------|
| payment_retry         | amount-tiered: >=1500 voice, >=400 SMS, else retry link            |
| checkout_abandonment  | higher voice bar (>=3000), else SMS / link                         |
| mandate_failure       | `mandate_revoked` -> link only (needs re-auth); else amount-tiered |

Stopping rules (`decision/config.py`, enforced in `decision/stopping_rules.py`,
re-checked at dispatch and again in the agent entrypoint):

- **explicit refusal** -> blocks every channel, logged
- **max 2 voice attempts** per customer -> blocks further calls (downgrades to SMS)
- **call window 09:00-19:00** in the customer's own timezone -> blocks calls outside it

## Audit trail

Every event: `event_ingested -> [stopping_rule_triggered?] -> decision`.
A placed call adds `action -> [stopping_rule_triggered if refused mid-call] -> outcome`,
with the transcript stored in the `outcome` row's payload. `audit_log` is
append-only (DB trigger rejects UPDATE/DELETE); metrics derive from it only.

## Voice pipeline

Clean-room build from the PRD spec. Gemini Live (`gemini-live-2.5-flash-native-audio`)
via `livekit-plugins-google` — one model for STT+LLM+TTS, handles Hindi/English
code-switching. The conversation is a `voice/flow.py` `Agent` whose real actions
are function tools (`send_retry_link`, `mark_do_not_contact`, `wrong_person`,
`offer_declined`, `end_call`) — the model's speech never moves state, only the
logged tools do. The prompt forbids asking for card/CVV/OTP/UPI PIN and requires
honouring a hard "don't contact me again" immediately.

Telephony is not wired: `voice/dispatch.py` exposes a `Dialer` interface and a
default `UnconfiguredDialer` that refuses to place a call until a provider
(Exotel/Twilio/Plivo -> LiveKit SIP participant) is chosen (PRD §10).

## Metrics view

`metrics/` — FastAPI, read-only, no auth, no framework, everything computed from
`audit_log` at request time (`metrics/compute.py`).

- `GET /` — recovery rate + ₹ recovered by intervention and by failure type;
  cost/effort per recovery (attempts, call minutes, ₹ at documented rates in
  `metrics/cost.py`); stopping-rule counts; the full exceptions list (every
  non-recovered event + why, nothing hidden); an all-events table.
- `GET /event/{id}` — drill-down for the pitch demo: event facts -> decision +
  reason -> stopping rules -> action -> outcome -> the call transcript as
  readable dialogue, tagged **real** (genuine Gemini) or **simulated**.
- `GET /api/summary`, `/api/exceptions`, `/api/event/{id}` — same data as JSON.

Batch call outcomes are seeded by `metrics/seed.py` (documented probability
model) because real outcomes come from the Day-3 pilot; `--real N` runs the
actual Gemini agent for N calls so several drill-downs show real transcripts.
The page carries a banner making the simulated/real split explicit.
