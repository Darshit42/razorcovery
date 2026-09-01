"""Server-rendered HTML for the metrics view. Plain f-strings, one
inline stylesheet, no JS, no framework. Legible tables + a readable
transcript view for the pitch demo.
"""
from __future__ import annotations

import html
from typing import Any

from metrics import cost
from metrics.compute import EventLifecycle, Summary

_CSS = """
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
       color: #1a1a1a; background: #fafafa; margin: 0; padding: 2rem 1rem; }
main { max-width: 980px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 .5rem; border-bottom: 2px solid #e2e2e2;
     padding-bottom: .25rem; }
.sub { color: #666; margin: 0 0 1.5rem; }
.banner { background: #fff6e0; border: 1px solid #e8cf8a; border-radius: 6px;
          padding: .6rem .9rem; margin: 1rem 0; font-size: .9rem; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; margin: 1rem 0; }
.card { flex: 1 1 160px; background: #fff; border: 1px solid #e2e2e2;
        border-radius: 8px; padding: .8rem 1rem; }
.card .n { font-size: 1.5rem; font-weight: 600; }
.card .l { color: #666; font-size: .82rem; text-transform: uppercase;
           letter-spacing: .03em; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #e2e2e2; font-size: .92rem; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid #ededed; }
th { background: #f3f3f3; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
a { color: #1256a0; }
.badge { display: inline-block; padding: .05rem .45rem; border-radius: 10px;
         font-size: .8rem; border: 1px solid; }
.b-recovered { background: #e6f4ea; border-color: #a8d5b5; color: #1e6b34; }
.b-blocked   { background: #fde8e8; border-color: #f0b4b4; color: #a12a2a; }
.b-open      { background: #eef1f5; border-color: #c7cfda; color: #45506a; }
.turn { display: grid; grid-template-columns: 90px 1fr; gap: .8rem;
        padding: .5rem .2rem; border-bottom: 1px solid #f0f0f0; }
.turn .role { font-weight: 600; color: #555; text-transform: capitalize; }
.turn.agent { background: #f6f9ff; }
.timeline li { margin: .3rem 0; }
.back { display: inline-block; margin-bottom: 1rem; }
code { background: #f0f0f0; padding: .05rem .3rem; border-radius: 3px; }
"""


def _rupees(n: float | int | None) -> str:
    if not n:
        return "₹0"
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"₹{'-' if n < 0 else ''}{s}"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _e(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _bucket_table(buckets, label: str) -> str:
    rows = "".join(
        f"<tr><td>{_e(b.key)}</td>"
        f"<td class='num'>{b.events}</td>"
        f"<td class='num'>{b.contacted}</td>"
        f"<td class='num'>{b.recovered}</td>"
        f"<td class='num'>{_pct(b.recovery_rate)}</td>"
        f"<td class='num'>{_rupees(b.amount_at_risk_inr)}</td>"
        f"<td class='num'>{_rupees(b.amount_recovered_inr)}</td>"
        f"<td class='num'>{_pct(b.value_recovery_rate)}</td></tr>"
        for b in buckets
    )
    return (
        f"<table><thead><tr><th>{_e(label)}</th>"
        f"<th class='num'>events</th><th class='num'>contacted</th>"
        f"<th class='num'>recovered</th><th class='num'>rate</th>"
        f"<th class='num'>₹ at risk</th><th class='num'>₹ recovered</th>"
        f"<th class='num'>₹ rate</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def index_page(s: Summary, exceptions: list[dict], lifecycles: list[EventLifecycle],
               *, has_simulated: bool) -> str:
    cards = "".join(
        f"<div class='card'><div class='n'>{n}</div><div class='l'>{_e(l)}</div></div>"
        for n, l in [
            (f"{s.total_events}", "events"),
            (f"{s.recovered}", "recovered"),
            (_pct(s.recovery_rate), "recovery rate"),
            (_rupees(s.amount_recovered_inr), "₹ recovered"),
            (_rupees(s.amount_at_risk_inr), "₹ at risk"),
            (_rupees(s.effort.cost_per_recovery_inr), "cost / recovery"),
        ]
    )

    eff = s.effort
    effort_tbl = (
        "<table><tbody>"
        f"<tr><th>recovered payments</th><td class='num'>{eff.recovered}</td></tr>"
        f"<tr><th>call attempts (total)</th><td class='num'>{eff.total_attempts}</td></tr>"
        f"<tr><th>attempts / recovery</th><td class='num'>{eff.attempts_per_recovery}</td></tr>"
        f"<tr><th>call minutes (total)</th><td class='num'>{eff.total_call_minutes}</td></tr>"
        f"<tr><th>call minutes / recovery</th><td class='num'>{eff.call_minutes_per_recovery}</td></tr>"
        f"<tr><th>SMS sent · links only</th><td class='num'>{eff.sms_sent} · {eff.links_only}</td></tr>"
        f"<tr><th>total spend</th><td class='num'>₹{eff.total_cost_inr:.2f}</td></tr>"
        f"<tr><th>cost / recovery</th><td class='num'>₹{eff.cost_per_recovery_inr:.2f}</td></tr>"
        "</tbody></table>"
        f"<p class='sub'>{_e(cost.RATE_NOTE)}</p>"
    )

    stop_tbl = "".join(
        f"<tr><td>{_e(k)}</td><td class='num'>{v}</td></tr>"
        for k, v in sorted(s.stopping_rule_counts.items())
    ) or "<tr><td colspan=2>none</td></tr>"

    def _exc_badge(blocked: bool) -> str:
        cls, txt = ("b-blocked", "blocked") if blocked else ("b-open", "not recovered")
        return f"<span class='badge {cls}'>{txt}</span>"

    exc_rows = "".join(
        f"<tr><td><a href='/event/{_e(x['event_id'])}'>{_e(x['event_id'])}</a></td>"
        f"<td>{_e(x['failure_type'])}</td><td>{_e(x['intervention'])}</td>"
        f"<td class='num'>{_rupees(x['amount_inr'])}</td>"
        f"<td>{_exc_badge(x['blocked'])}</td>"
        f"<td>{_e(x['why'])}</td></tr>"
        for x in exceptions
    )

    all_rows = "".join(
        f"<tr><td><a href='/event/{_e(lc.event_id)}'>{_e(lc.event_id)}</a></td>"
        f"<td>{_e(lc.failure_type)}</td><td>{_e(lc.decided_intervention)}</td>"
        f"<td class='num'>{_rupees(lc.amount_inr)}</td>"
        f"<td>{_status_badge(lc)}</td>"
        f"<td>{_e(lc.result or '—')}</td></tr>"
        for lc in sorted(lifecycles, key=lambda l: l.event_id)
    )

    banner = (
        "<div class='banner'>⚠ Batch call outcomes below are <b>simulated</b> "
        "(seeded model in <code>metrics/seed.py</code>) pending the Day-3 live "
        "pilot. Rows tagged <b>real</b> in the drill-down are genuine Gemini "
        "conversations.</div>" if has_simulated else ""
    )

    return _page(
        "razorcovery — recovery metrics",
        f"<h1>Recovery metrics</h1>"
        f"<p class='sub'>Computed entirely from the append-only audit trail "
        f"({s.total_events} events, {s.total_customers} customers).</p>"
        f"{banner}"
        f"<div class='cards'>{cards}</div>"
        f"<h2>Recovery by intervention</h2>{_bucket_table(s.by_intervention, 'intervention')}"
        f"<h2>Recovery by failure type</h2>{_bucket_table(s.by_failure_type, 'failure type')}"
        f"<h2>Cost / effort per recovery</h2>{effort_tbl}"
        f"<h2>Stopping rules fired</h2><table><thead><tr><th>rule</th>"
        f"<th class='num'>count</th></tr></thead><tbody>{stop_tbl}</tbody></table>"
        f"<h2>Exceptions — {s.exception_count} events not recovered</h2>"
        f"<table><thead><tr><th>event</th><th>failure</th><th>intervention</th>"
        f"<th class='num'>₹</th><th>status</th><th>why</th></tr></thead>"
        f"<tbody>{exc_rows}</tbody></table>"
        f"<h2>All events</h2>"
        f"<table><thead><tr><th>event</th><th>failure</th><th>intervention</th>"
        f"<th class='num'>₹</th><th>status</th><th>result</th></tr></thead>"
        f"<tbody>{all_rows}</tbody></table>",
    )


def _status_badge(lc: EventLifecycle) -> str:
    if lc.recovered:
        return "<span class='badge b-recovered'>recovered</span>"
    if lc.blocked:
        return "<span class='badge b-blocked'>blocked</span>"
    return "<span class='badge b-open'>open</span>"


def detail_page(lc: EventLifecycle) -> str:
    if lc is None:
        return _page("not found", "<a class='back' href='/'>← back</a><p>Unknown event.</p>")

    facts = (
        "<table><tbody>"
        f"<tr><th>event</th><td><code>{_e(lc.event_id)}</code></td></tr>"
        f"<tr><th>customer</th><td>{_e(lc.customer_id)}</td></tr>"
        f"<tr><th>failure type</th><td>{_e(lc.failure_type)}</td></tr>"
        f"<tr><th>amount at risk</th><td>{_rupees(lc.amount_inr)}</td></tr>"
        f"<tr><th>decided intervention</th><td>{_e(lc.decided_intervention)}</td></tr>"
        f"<tr><th>decision reason</th><td>{_e(lc.decision_reason)}</td></tr>"
        f"<tr><th>status</th><td>{_status_badge(lc)}</td></tr>"
        f"<tr><th>result</th><td>{_e(lc.result or '—')}</td></tr>"
        f"<tr><th>call time</th><td>{round(lc.call_seconds,1)}s</td></tr>"
        f"<tr><th>retry link</th><td>{_e(lc.retry_link_url or '—')}</td></tr>"
        "</tbody></table>"
    )

    stops = "".join(
        f"<li><span class='badge b-blocked'>{_e(sr['rule'])}</span> {_e(sr['reason'])}"
        f"{' <em>(' + _e(sr['stage']) + ')</em>' if sr.get('stage') else ''}</li>"
        for sr in lc.stopping_rules
    )
    stops_block = f"<h2>Stopping rules</h2><ul class='timeline'>{stops}</ul>" if stops else ""

    tl = "".join(
        f"<li><code>{_e(r['entry_type'])}</code>"
        f"{' — ' + _e(r['reason']) if r['reason'] else ''}</li>"
        for r in lc.timeline
    )

    src = lc.transcript_source or "n/a"
    if lc.transcript:
        turns = "".join(
            f"<div class='turn {'agent' if t['role'] in ('assistant','agent') else 'user'}'>"
            f"<div class='role'>{_e('Priya' if t['role'] in ('assistant','agent') else 'Customer')}</div>"
            f"<div>{_e(t['text'])}</div></div>"
            for t in lc.transcript
        )
        transcript_block = (
            f"<h2>Call transcript "
            f"<span class='badge {'b-recovered' if src=='real' else 'b-open'}'>{_e(src)}</span></h2>"
            f"<div>{turns}</div>"
        )
    else:
        transcript_block = f"<h2>Call transcript</h2><p class='sub'>No transcript ({_e(src)}).</p>"

    return _page(
        f"{lc.event_id} — recovery detail",
        f"<a class='back' href='/'>← all events</a>"
        f"<h1>{_e(lc.event_id)}</h1>"
        f"{facts}"
        f"{stops_block}"
        f"{transcript_block}"
        f"<h2>Full audit timeline</h2><ol class='timeline'>{tl}</ol>",
    )
