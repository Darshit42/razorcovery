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
- [x] Merchant intake — upload a contact sheet, run the recovery batch,
      watch it live, download the results
- [x] Real outbound-SIP dialer (activates on LIVEKIT_* + SIP trunk creds)
- [ ] Telephony provider account + one live PSTN call end-to-end
- [ ] Day-3 live pilot with a real merchant

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
python -m metrics.seed --reset --simulate            # fast, seeded estimates, no API calls
# real Gemini agent for every voice event (needs Gemini quota headroom;
# an AI Studio free key will rate-limit under a full batch):
# python -m metrics.seed --reset --real-batch
# top up real transcripts a few at a time:
# python -m metrics.seed --append-real 5
uvicorn metrics.app:app --port 8000                  # then open http://localhost:8000
```

Or drive it from a real sheet: open `http://localhost:8000/upload`, upload a
CSV/XLSX of contacts, tick the consent attestation, and run the batch — watch
it live at `/batch/{id}` and download the results.

Every `--reset` regenerates the synthetic batch with a random seed and a
random count (45-75) — different customers, amounts and failure mix each
run. `--keep-events` reuses the current fixture; `--gen-seed N` / `--count N`
pin it. `data/fixtures/events.json` is a generated artifact (gitignored);
`python -m data.generate_events` writes a deterministic seed-42 batch.

The dashboard filters by failure type, intervention, status and date range
(`/?failure_type=payment_retry&status=recovered`); same params on the JSON
endpoints. LLM cost is real token usage (LiveKit UsageCollector) priced at
Gemini 2.5 Flash list price; telephony is ₹0 until a provider is chosen.

## Layout

```
data/       synthetic event generator + pydantic schemas + fixtures
decision/   failure-type -> intervention routing, stopping rules, batch runner
audit/      append-only Postgres audit log (schema, writer, reader, export)
voice/      Gemini agent: prompt, flow (logged tools), headless conversation,
            LiveKit outbound-SIP dialer
intake/     merchant sheet -> parse (CSV/XLSX) -> batch/batch_row -> async runner
metrics/    FastAPI: dashboard + intake routes, computed from audit_log
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

Telephony: `voice/dialer.py` has a real `LiveKitSipDialer` and `voice/agent.py`
rings the callee in with `create_sip_participant`. It activates when
`LIVEKIT_*` + `SIP_OUTBOUND_TRUNK_ID` are set. Until then `UnconfiguredDialer`
runs and each voice row uses `voice/headless.converse` — a real Gemini
conversation with a scripted synthetic customer, no PSTN.

Wiring it up (one-time):

```bash
# 1. register your SIP provider as a LiveKit outbound trunk
python -m voice.setup_trunk --address <sip-host> --number +91XXXXXXXXXX \
    --user <sip-user> --password <sip-pass>          # prints SIP_OUTBOUND_TRUNK_ID
# 2. paste that + LIVEKIT_* into .env, then run the worker
python -m voice.agent start
# 3. test one call to a number you control
python -m voice.testcall --to +9198XXXXXXXX --amount 2499
```

Once the worker is up, the intake batch runner dispatches real calls to it
automatically for every voice-routed row.

## Merchant intake workflow

`intake/` — a merchant uploads a contact sheet and the recovery batch runs
against it. No hardcoded values; every number on the batch and dashboard is
computed from `audit_log`.

- `GET /upload` — CSV or XLSX. Columns are auto-detected (`intake/parse.py`:
  fuzzy header match, `+91` phone normalisation, dedup). A required consent
  attestation gates the run (PRD §1a: merchant's own customers only).
- Preview shows valid rows + every rejected row *with the reason* — nothing
  is silently dropped.
- `POST /upload` (attested) creates a `batch` + one `batch_row` per contact.
- `POST /batch/{id}/run` spawns `python -m intake.run_batch <id>` (subprocess:
  LiveKit plugins need the process main thread). Per row: build a real
  `FailureEvent` → `decision.route` → dial (if telephony configured) or
  headless Gemini conversation → audit rows tagged `payload.batch_id`.
- `GET /batch/{id}` — live progress bar + records table (polls
  `/batch/{id}/progress`). `GET /batch/{id}/stream` is the SSE feed.
- `GET /batch/{id}/export.csv` / `.json` — per-row decision + outcome +
  transcript. `GET /?batch=<id>` slices the whole dashboard to that batch.
- `GET /batches` — all batches.

## Metrics view

`metrics/` — FastAPI, read-only, no auth. Server-rendered HTML styled with the
Tailwind CDN (no build step, no React / component library); fixed sidebar,
stat-card row, white content panels, empty states. Everything is computed from
`audit_log` at request time (`metrics/compute.py`).

- `GET /` — recovery rate + ₹ recovered by intervention and by failure type;
  cost/effort per recovery (attempts, call minutes, ₹ at documented rates in
  `metrics/cost.py`); stopping-rule counts; the full exceptions list (every
  non-recovered event + why, nothing hidden); an all-events table.
- `GET /event/{id}` — drill-down for the pitch demo: event facts -> decision +
  reason -> stopping rules -> action -> outcome -> the call transcript as
  readable dialogue, tagged **real** (genuine Gemini) or **simulated**.
- `GET /api/summary`, `/api/exceptions`, `/api/event/{id}` — same data as JSON.

`metrics/seed.py --real-batch` runs the actual Gemini agent (scripted synthetic
customers, 8 personas, no live telephony) for every non-blocked voice event and
logs real transcripts + tool-driven outcomes; Gemini failures are logged
honestly as `result="failed"`, never faked. SMS / link interventions have no
delivery channel yet, so they get no outcome and show as unconfirmed. `--simulate`
keeps the old seeded probability model for a fast demo. The page banner states
which mode produced the data.
