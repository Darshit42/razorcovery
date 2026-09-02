"""Server-rendered HTML for the metrics view.

Styling via the Tailwind CDN (no build step, no React / component
library). Layout follows the agreed reference: fixed left sidebar, warm
off-white canvas, stat-card row with circular icon badges, white bordered
content panels, empty-state pattern for sections with no data.

The data all comes from metrics.compute — this module only renders it.
"""
from __future__ import annotations

import html
from typing import Any

from metrics import cost
from metrics.compute import EventLifecycle, Summary

# One accent colour, distinct from the reference's amber: teal.
_TAILWIND_CONFIG = """
tailwind.config = {
  theme: {
    extend: {
      colors: {
        brand: {
          50:'#f0fdfa',100:'#ccfbf1',200:'#99f6e4',400:'#2dd4bf',
          500:'#14b8a6',600:'#0d9488',700:'#0f766e',800:'#115e59'
        },
        canvas: '#faf8f4'
      },
      fontFamily: { sans: ['Inter','ui-sans-serif','system-ui','sans-serif'] }
    }
  }
}
"""

_NAV = [
    ("overview", "Overview", "grid"),
    ("intervention", "By intervention", "bars"),
    ("failure-type", "By failure type", "layers"),
    ("effort", "Cost & effort", "rupee"),
    ("stopping-rules", "Stopping rules", "shield"),
    ("exceptions", "Exceptions", "alert"),
    ("all-events", "All events", "list"),
]

_ICONS = {
    "grid": "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
    "bars": "M4 20V10M10 20V4M16 20v-8M22 20H2",
    "layers": "M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
    "rupee": "M6 3h12M6 8h12M9 3c4 0 6 2 6 5s-2 5-6 5H8l6 8",
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    "alert": "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
    "list": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    "check": "M20 6 9 17l-5-5",
    "phone": "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.6A2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.6a2 2 0 0 1-.5 2.1L9.1 11a16 16 0 0 0 6 6l1.6-1.6a2 2 0 0 1 2.1-.5c.8.3 1.7.5 2.6.6A2 2 0 0 1 22 16.9z",
    "back": "M19 12H5M12 19l-7-7 7-7",
    "clock": "M12 6v6l4 2M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20z",
    "spark": "M13 2 3 14h9l-1 8 10-12h-9z",
}

_TINTS = {
    "teal": ("bg-brand-50", "text-brand-600"),
    "emerald": ("bg-emerald-50", "text-emerald-600"),
    "violet": ("bg-violet-50", "text-violet-600"),
    "rose": ("bg-rose-50", "text-rose-600"),
    "amber": ("bg-amber-50", "text-amber-600"),
    "slate": ("bg-slate-100", "text-slate-500"),
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _e(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


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


def _icon(name: str, cls: str = "w-5 h-5") -> str:
    d = _ICONS.get(name, _ICONS["grid"])
    return (
        f"<svg class='{cls}' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
        f"stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        f"<path d='{d}'/></svg>"
    )


def _badge(text: str, tone: str) -> str:
    tones = {
        "recovered": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
        "blocked": "bg-rose-50 text-rose-700 ring-rose-600/20",
        "open": "bg-slate-100 text-slate-600 ring-slate-500/20",
        "real": "bg-brand-50 text-brand-700 ring-brand-600/20",
        "simulated": "bg-amber-50 text-amber-700 ring-amber-600/20",
    }
    c = tones.get(tone, tones["open"])
    return (
        f"<span class='inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 "
        f"text-xs font-medium ring-1 ring-inset {c}'>{_e(text)}</span>"
    )


def _status_badge(lc: EventLifecycle) -> str:
    if lc.recovered:
        return _badge("recovered", "recovered")
    if lc.blocked:
        return _badge("blocked", "blocked")
    return _badge("open", "open")


def _stat_card(icon: str, tint: str, label: str, value: str, sub: str | None = None) -> str:
    bg, fg = _TINTS[tint]
    sub_html = f"<p class='mt-1 text-xs text-slate-400'>{_e(sub)}</p>" if sub else ""
    return (
        f"<div class='rounded-2xl border border-slate-200 bg-white p-5 shadow-sm'>"
        f"<div class='flex h-10 w-10 items-center justify-center rounded-full {bg} {fg}'>"
        f"{_icon(icon)}</div>"
        f"<p class='mt-4 text-xs font-medium uppercase tracking-wide text-slate-400'>{_e(label)}</p>"
        f"<p class='mt-1 text-2xl font-semibold text-slate-900 tabular-nums'>{value}</p>"
        f"{sub_html}</div>"
    )


def _panel(pid: str, title: str, inner: str, caption: str | None = None) -> str:
    cap = f"<p class='mt-0.5 text-sm text-slate-400'>{_e(caption)}</p>" if caption else ""
    return (
        f"<section id='{pid}' class='scroll-mt-6 rounded-2xl border border-slate-200 "
        f"bg-white shadow-sm'>"
        f"<header class='border-b border-slate-100 px-6 py-4'>"
        f"<h2 class='text-base font-semibold text-slate-900'>{_e(title)}</h2>{cap}</header>"
        f"<div class='px-6 py-5'>{inner}</div></section>"
    )


def _empty(icon: str, headline: str, subtext: str) -> str:
    return (
        f"<div class='flex flex-col items-center justify-center py-12 text-center'>"
        f"<div class='flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400'>"
        f"{_icon(icon, 'w-6 h-6')}</div>"
        f"<p class='mt-4 text-sm font-semibold text-slate-700'>{_e(headline)}</p>"
        f"<p class='mt-1 text-sm text-slate-400'>{_e(subtext)}</p></div>"
    )


def _table(headers: list[tuple[str, str]], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = "".join(
        f"<th class='{'text-right' if a == 'r' else 'text-left'} whitespace-nowrap "
        f"px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400'>{_e(h)}</th>"
        for h, a in headers
    )
    body = "".join(
        "<tr class='border-t border-slate-100 hover:bg-slate-50/70'>"
        + "".join(
            f"<td class='{'text-right tabular-nums' if a == 'r' else ''} px-3 py-2.5 "
            f"text-sm text-slate-700'>{c}</td>"
            for c, (_, a) in zip(cells, headers)
        )
        + "</tr>"
        for cells in rows
    )
    return (
        f"<div class='overflow-x-auto'><table class='min-w-full'>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------
def _sidebar(active: str, *, show_nav: bool) -> str:
    if show_nav:
        items = "".join(
            f"<a href='#{pid}' class='flex items-center gap-3 rounded-lg px-3 py-2 text-sm "
            f"font-medium {'bg-brand-50 text-brand-700' if pid == active else 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'}'>"
            f"<span class='{'text-brand-600' if pid == active else 'text-slate-400'}'>{_icon(icon, 'w-4 h-4')}</span>"
            f"{_e(label)}</a>"
            for pid, label, icon in _NAV
        )
        nav = f"<nav class='mt-8 space-y-1'>{items}</nav>"
    else:
        nav = (
            "<a href='/' class='mt-8 flex items-center gap-2 rounded-lg px-3 py-2 text-sm "
            "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700'>"
            f"{_icon('back', 'w-4 h-4')} Back to overview</a>"
        )
    return (
        "<aside class='fixed inset-y-0 left-0 hidden w-72 flex-col border-r border-slate-200 "
        "bg-white px-5 py-6 lg:flex'>"
        "<div class='flex items-center gap-3'>"
        "<div class='flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-white'>"
        f"{_icon('phone', 'w-5 h-5')}</div>"
        "<div><p class='text-sm font-semibold text-slate-900'>razorcovery</p>"
        "<p class='text-xs text-slate-400'>revenue recovery</p></div></div>"
        f"{nav}"
        "<div class='mt-auto pt-6 text-xs text-slate-300'>computed from the audit trail</div>"
        "</aside>"
    )


def _shell(title: str, active: str, body: str, *, show_nav: bool = True) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title>"
        "<script src='https://cdn.tailwindcss.com'></script>"
        f"<script>{_TAILWIND_CONFIG}</script>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap' rel='stylesheet'>"
        "<style>body{font-family:Inter,system-ui,sans-serif}</style></head>"
        "<body class='bg-canvas text-slate-800'>"
        f"{_sidebar(active, show_nav=show_nav)}"
        f"<main class='lg:pl-72'><div class='mx-auto max-w-5xl px-6 py-10'>{body}</div></main>"
        "</body></html>"
    )


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def _bucket_rows(buckets) -> list[list[str]]:
    return [
        [
            f"<span class='font-medium text-slate-900'>{_e(b.key)}</span>",
            str(b.events), str(b.contacted), str(b.recovered),
            _pct(b.recovery_rate),
            _rupees(b.amount_at_risk_inr), _rupees(b.amount_recovered_inr),
            _pct(b.value_recovery_rate),
        ]
        for b in buckets
    ]


_BUCKET_HEADERS = [
    ("", "l"), ("events", "r"), ("contacted", "r"), ("recovered", "r"),
    ("rate", "r"), ("₹ at risk", "r"), ("₹ recovered", "r"), ("₹ rate", "r"),
]


def _filter_bar(filters: dict, total: int, shown: int, active: bool) -> str:
    def _select(name: str, label: str, options: list[str]) -> str:
        cur = filters.get(name, "")
        opts = f"<option value=''>All {_e(label)}</option>" + "".join(
            f"<option value='{_e(o)}'{' selected' if o == cur else ''}>{_e(o)}</option>"
            for o in options
        )
        return (
            f"<label class='flex flex-col gap-1 text-xs font-medium text-slate-500'>"
            f"{_e(label)}"
            f"<select name='{name}' class='rounded-lg border border-slate-200 bg-white "
            f"px-3 py-1.5 text-sm text-slate-700'>{opts}</select></label>"
        )

    def _date(name: str, label: str) -> str:
        return (
            f"<label class='flex flex-col gap-1 text-xs font-medium text-slate-500'>{_e(label)}"
            f"<input type='date' name='{name}' value='{_e(filters.get(name, ''))}' "
            f"class='rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700'></label>"
        )

    from metrics.compute import FAILURE_TYPES, INTERVENTIONS, STATUSES

    clear = (
        "<a href='/' class='rounded-lg px-3 py-1.5 text-sm font-medium text-slate-500 "
        "hover:text-slate-700'>Clear</a>" if active else ""
    )
    count = (
        f"<p class='mt-3 text-xs text-slate-400'>Showing "
        f"<span class='font-semibold text-slate-600'>{shown}</span> of {total} events</p>"
    )
    return (
        "<form method='get' class='mb-8 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm'>"
        "<div class='flex flex-wrap items-end gap-3'>"
        f"{_select('failure_type', 'failure types', list(FAILURE_TYPES))}"
        f"{_select('intervention', 'interventions', list(INTERVENTIONS))}"
        f"{_select('status', 'statuses', list(STATUSES))}"
        f"{_date('since', 'from')}"
        f"{_date('until', 'to')}"
        "<button type='submit' class='rounded-lg bg-brand-600 px-4 py-1.5 text-sm "
        "font-medium text-white hover:bg-brand-700'>Apply</button>"
        f"{clear}"
        "</div>"
        f"{count}</form>"
    )


def index_page(s: Summary, exceptions: list[dict], lifecycles: list[EventLifecycle],
               *, has_simulated: bool, total_events: int | None = None,
               filters: dict | None = None, filters_active: bool = False) -> str:
    eff = s.effort
    total_events = s.total_events if total_events is None else total_events
    filters = filters or {}

    if has_simulated:
        msg = (
            "Some batch call outcomes are <span class='font-semibold'>simulated</span> "
            "(seeded model in <code class='rounded bg-amber-100 px-1'>metrics/seed.py</code>). "
            "Drill-downs tagged <span class='font-semibold'>real</span> are genuine Gemini "
            "conversations. SMS / link interventions have no delivery channel yet and show as "
            "unconfirmed."
        )
    else:
        msg = (
            "Voice outcomes are <span class='font-semibold'>real Gemini conversations</span> "
            "with scripted synthetic customers (no live telephony yet). SMS / link "
            "interventions have no delivery channel and show as unconfirmed. Real customer "
            "calls land in the Day-3 pilot."
        )
    banner = (
        "<div class='mb-8 flex gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4'>"
        f"<span class='mt-0.5 text-amber-500'>{_icon('alert', 'w-5 h-5')}</span>"
        f"<p class='text-sm text-amber-800'>{msg}</p></div>"
    )

    cards = (
        _stat_card("bars", "teal", "recovery rate", _pct(s.recovery_rate),
                   f"{s.recovered} of {s.contacted} contacted")
        + _stat_card("rupee", "emerald", "₹ recovered", _rupees(s.amount_recovered_inr),
                     f"of {_rupees(s.amount_at_risk_inr)} at risk")
        + _stat_card("spark", "violet", "LLM cost / recovery", f"₹{eff.cost_per_recovery_inr:.4f}",
                     f"{eff.tokens_per_recovery:,} tokens each")
        + _stat_card("alert", "rose", "exceptions", str(s.exception_count),
                     "events not recovered")
    )
    overview = (
        f"<section id='overview' class='scroll-mt-6'>"
        f"<div class='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'>{cards}</div></section>"
    )

    by_int = _panel("intervention", "Recovery by intervention",
                    _table(_BUCKET_HEADERS, _bucket_rows(s.by_intervention)),
                    "which channel the decision layer chose, and how it did")
    by_ft = _panel("failure-type", "Recovery by failure type",
                   _table(_BUCKET_HEADERS, _bucket_rows(s.by_failure_type)),
                   "payment retry vs checkout abandonment vs mandate failure")

    effort_rows = [
        ["recovered payments", str(eff.recovered)],
        ["call attempts (total)", str(eff.total_attempts)],
        ["attempts / recovery", str(eff.attempts_per_recovery)],
        ["LLM tokens — prompt / completion",
         f"{eff.prompt_tokens:,} / {eff.completion_tokens:,}"],
        ["tokens / recovery", f"{eff.tokens_per_recovery:,}"],
        ["LLM cost (Gemini list price)", f"₹{eff.llm_cost_inr:.4f}"],
        ["telephony cost", "₹0 — no provider selected"],
        ["SMS sent · links only", f"{eff.sms_sent} · {eff.links_only}"],
        ["total spend", f"₹{eff.total_cost_inr:.4f}"],
        ["cost / recovery", f"₹{eff.cost_per_recovery_inr:.4f}"],
    ]
    effort = _panel(
        "effort", "Cost / effort per recovery",
        _table([("", "l"), ("", "r")], [[f"<span class='text-slate-500'>{_e(a)}</span>", b]
                                        for a, b in effort_rows])
        + f"<p class='mt-3 text-xs text-slate-400'>{_e(cost.RATE_NOTE)}</p>",
    )

    if s.stopping_rule_counts:
        stop_inner = _table(
            [("rule", "l"), ("count", "r")],
            [[f"<span class='font-medium text-slate-900'>{_e(k)}</span>", str(v)]
             for k, v in sorted(s.stopping_rule_counts.items())],
        )
    else:
        stop_inner = _empty("shield", "No stopping rules fired",
                            "No refusals, attempt caps or out-of-window calls in this batch.")
    stops = _panel("stopping-rules", "Stopping rules fired", stop_inner,
                   "every trigger is also logged as its own audit event")

    if exceptions:
        exc_rows = [
            [
                f"<a href='/event/{_e(x['event_id'])}' class='font-medium text-brand-700 hover:underline'>{_e(x['event_id'])}</a>",
                _e(x["failure_type"]), _e(x["intervention"]),
                _rupees(x["amount_inr"]),
                _badge("blocked", "blocked") if x["blocked"] else _badge("not recovered", "open"),
                f"<span class='text-slate-500'>{_e(x['why'])}</span>",
            ]
            for x in exceptions
        ]
        exc_inner = _table(
            [("event", "l"), ("failure", "l"), ("intervention", "l"),
             ("₹", "r"), ("status", "l"), ("why", "l")],
            exc_rows,
        )
    else:
        exc_inner = _empty("check", "Nothing hidden — and nothing to hide",
                           "Every event in this batch recovered.")
    exc = _panel("exceptions", f"Exceptions — {s.exception_count} events not recovered",
                 exc_inner, "shown, not hidden (PRD §5)")

    all_rows = [
        [
            f"<a href='/event/{_e(lc.event_id)}' class='font-medium text-brand-700 hover:underline'>{_e(lc.event_id)}</a>",
            _e(lc.failure_type), _e(lc.decided_intervention),
            _rupees(lc.amount_inr), _status_badge(lc),
            f"<span class='text-slate-500'>{_e(lc.result or '—')}</span>",
        ]
        for lc in sorted(lifecycles, key=lambda l: l.event_id)
    ]
    all_events = _panel(
        "all-events", "All events",
        _table([("event", "l"), ("failure", "l"), ("intervention", "l"),
                ("₹", "r"), ("status", "l"), ("result", "l")], all_rows),
    )

    header = (
        "<div class='mb-8'>"
        "<h1 class='text-2xl font-semibold text-slate-900'>Recovery metrics</h1>"
        f"<p class='mt-1 text-sm text-slate-500'>Computed entirely from the append-only "
        f"audit trail — {s.total_events} of {total_events} events"
        f"{', ' + str(s.total_customers) + ' customers' if s.total_events else ''}.</p></div>"
    )

    filter_bar = _filter_bar(filters, total_events, s.total_events, filters_active)

    if s.total_events == 0:
        body = header + filter_bar + _panel(
            "overview", "No matching events",
            _empty("list", "Nothing matches these filters",
                   "Widen the date range or clear a filter."),
        )
    else:
        body = (
            header + filter_bar + banner + overview
            + "<div class='mt-8 space-y-8'>"
            + by_int + by_ft + effort + stops + exc + all_events
            + "</div>"
        )

    return _shell("razorcovery — recovery metrics", "overview", body)


def detail_page(lc: EventLifecycle | None) -> str:
    if lc is None:
        return _shell(
            "not found", "", show_nav=False,
            body=_empty("alert", "Unknown event", "No audit rows for that id."),
        )

    facts = [
        ("customer", _e(lc.customer_id)),
        ("failure type", _e(lc.failure_type)),
        ("amount at risk", _rupees(lc.amount_inr)),
        ("decided intervention", _e(lc.decided_intervention)),
        ("result", _e(lc.result or "—")),
        ("call time", f"{round(lc.call_seconds, 1)}s"),
        ("retry link", _e(lc.retry_link_url or "—")),
    ]
    facts_grid = (
        "<dl class='grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2'>"
        + "".join(
            f"<div class='flex justify-between gap-4 border-b border-slate-100 pb-2'>"
            f"<dt class='text-sm text-slate-400'>{k}</dt>"
            f"<dd class='text-sm font-medium text-slate-800 text-right'>{v}</dd></div>"
            for k, v in facts
        )
        + "</dl>"
        + f"<p class='mt-4 text-sm text-slate-500'>{_e(lc.decision_reason or '')}</p>"
    )

    if lc.stopping_rules:
        def _stop_li(sr: dict) -> str:
            stage = ""
            if sr.get("stage"):
                stage = f" <span class='text-slate-400'>({_e(sr['stage'])})</span>"
            return (
                f"<li class='flex gap-3'>{_badge(_e(sr['rule']), 'blocked')}"
                f"<span class='text-sm text-slate-600'>{_e(sr['reason'])}{stage}</span></li>"
            )

        stop_inner = "<ul class='space-y-3'>" + "".join(_stop_li(sr) for sr in lc.stopping_rules) + "</ul>"
    else:
        stop_inner = _empty("shield", "No stopping rules triggered",
                            "This event was contacted within all limits.")

    src = lc.transcript_source or "n/a"
    if lc.transcript:
        turns = []
        for t in lc.transcript:
            is_agent = t["role"] in ("assistant", "agent")
            who = "Priya" if is_agent else "Customer"
            avatar_bg = "bg-brand-600 text-white" if is_agent else "bg-slate-200 text-slate-600"
            bubble = "bg-brand-50 text-slate-800" if is_agent else "bg-slate-100 text-slate-700"
            row_dir = "flex-row-reverse" if is_agent else ""
            turns.append(
                f"<div class='flex gap-3 {row_dir}'>"
                f"<div class='flex h-8 w-8 shrink-0 items-center justify-center rounded-full "
                f"text-xs font-semibold {avatar_bg}'>{who[0]}</div>"
                f"<div class='max-w-md rounded-2xl px-4 py-2 text-sm {bubble}'>"
                f"<p class='mb-0.5 text-xs font-medium text-slate-400'>{who}</p>{_e(t['text'])}</div></div>"
            )
        transcript_inner = "<div class='space-y-2'>" + "".join(turns) + "</div>"
        transcript_title = "Call transcript"
        transcript_cap = None
    else:
        transcript_inner = _empty(
            "phone", "No transcript",
            "No call was connected." if src == "n/a" else f"Source: {src}.",
        )
        transcript_title = "Call transcript"
        transcript_cap = None

    def _tl_li(r: dict) -> str:
        reason = f"<p class='text-sm text-slate-500'>{_e(r['reason'])}</p>" if r["reason"] else ""
        return (
            "<li><span class='absolute -left-[7px] mt-1.5 h-3 w-3 rounded-full border-2 "
            "border-white bg-brand-500'></span>"
            f"<p class='text-sm font-medium text-slate-800'>{_e(r['entry_type'])}</p>{reason}</li>"
        )

    timeline_inner = (
        "<ol class='relative space-y-4 border-l border-slate-200 pl-6'>"
        + "".join(_tl_li(r) for r in lc.timeline)
        + "</ol>"
    )

    header = (
        "<div class='mb-8 flex items-center gap-3'>"
        f"<h1 class='text-2xl font-semibold text-slate-900'>{_e(lc.event_id)}</h1>"
        f"{_status_badge(lc)}"
        f"{_badge(src, src) if lc.transcript else ''}</div>"
    )

    body = (
        header
        + "<div class='space-y-8'>"
        + _panel("facts", "Event", facts_grid)
        + _panel("stops", "Stopping rules", stop_inner)
        + _panel("transcript", transcript_title, transcript_inner, transcript_cap)
        + _panel("timeline", "Full audit timeline", timeline_inner)
        + "</div>"
    )
    return _shell(f"{lc.event_id} — recovery detail", "", body, show_nav=False)
