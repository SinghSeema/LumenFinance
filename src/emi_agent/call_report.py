"""One HTML page per live call: outcome record, latency, transcript, tools, guard events.

Written by bot.py when a call ends, so the demo (and the interview) can show what happened
without reading logs. Local file only; it holds mock borrower data, never keys.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from emi_agent import latency

CSS = """
:root { --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --line:#dddcd6; --card:#fff;
        --ok:#1f6f5c; --bad:#a23b2a; --agent:#eef4f1; --you:#f3f1ea; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
        --bg:#171716; --fg:#ecece8; --muted:#9a9a94; --line:#33332f; --card:#20201e;
        --ok:#4fb39a; --bad:#e07a68; --agent:#1d2a25; --you:#2a2822; } }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:860px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:1.4rem; margin:0 0 4px; } h2 { font-size:1.05rem; margin:28px 0 8px; }
.meta { color:var(--muted); margin:0 0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }
.kpi b { display:block; font-size:1.3rem; } .kpi span { color:var(--muted); font-size:.85rem; }
.ok { color:var(--ok); } .bad { color:var(--bad); }
pre { margin:0; white-space:pre-wrap; word-break:break-word; font-size:.85rem; }
table { width:100%; border-collapse:collapse; font-size:.85rem; }
th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:500; } td.n { text-align:right; font-variant-numeric:tabular-nums; }
.scroll { overflow-x:auto; }
.turn { padding:8px 12px; border-radius:8px; margin:6px 0; }
.turn.agent { background:var(--agent); } .turn.borrower { background:var(--you); }
.who { font-size:.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
"""


def _kpi(value: str, label: str, cls: str = "") -> str:
    return f'<div class="card kpi"><b class="{cls}">{escape(value)}</b><span>{escape(label)}</span></div>'


def _latency(rows: list[dict]) -> tuple[str, str]:
    """(kpi html, per-turn table html)."""
    complete = [r for r in rows if "gap" in r["ms"] and not r["interrupted"]]
    if complete:
        med, p90 = latency.median_p90([r["ms"]["gap"] for r in complete])
        stop = complete[0]["stop_secs"] * 1000
        kpi = (_kpi(f"{med:.0f} ms", f"median gap (n={len(complete)}); felt ≈ {med + stop:.0f} ms",
                    "ok" if med < 1200 else "bad")
               + _kpi(f"{p90:.0f} ms", f"p90 gap; felt ≈ {p90 + stop:.0f} ms", "ok" if p90 < 1800 else "bad"))
    else:
        kpi = _kpi("n/a", "no complete turns measured")
    keys = ["vad_stop->stt_final", "stt_final->first_sentence", "first_sentence->playback_start", "gap"]
    head = "".join(f"<th>{escape(k.replace('->', ' → '))}</th>" for k in keys)
    body = ""
    for i, r in enumerate(rows, 1):
        notes = ", ".join(n for n, on in (("interrupted", r.get("interrupted")),
                                           ("after barge-in", r.get("after_barge_in")),
                                           ("filler (model slow)" + (f"; answer at {r['ms']['to_answer']} ms"
                                            if "to_answer" in r["ms"] else ""), r.get("filler")),
                                           ("bridge line", r.get("bridge") and not r.get("filler"))) if on)
        cells = "".join(f'<td class="n">{r["ms"].get(k, "")}</td>' for k in keys)
        body += f"<tr><td>{i}</td>{cells}<td>{escape(notes)}</td></tr>"
    table = (f'<div class="scroll"><table><tr><th>#</th>{head}<th>notes</th></tr>{body}</table></div>'
             if rows else '<p class="meta">No latency rows.</p>')
    return kpi, table


def _short(value: object, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def render(brain, rows: list[dict], meta: dict) -> str:
    s = brain.state
    record = s.outcome.model_dump(mode="json") if s.outcome is not None else {}
    early = [d for d in s.disclosure_log if not d.verified]
    lat_kpi, lat_table = _latency(rows)

    kpis = (_kpi(str(record.get("outcome", "none")), "outcome")
            + _kpi("yes" if s.verified else "no", "verified", "ok" if s.verified else "")
            + _kpi("yes" if s.escalate_to_human else "no", "escalate to human", "bad" if s.escalate_to_human else "")
            + _kpi("none" if not early else f"{len(early)} early", "loan detail before verification",
                   "ok" if not early else "bad")
            + lat_kpi)

    transcript = "".join(
        f'<div class="turn {who}"><div class="who">{"Agent" if who == "agent" else "Borrower"}</div>'
        f"{escape(text)}</div>" for who, text in brain.transcript)

    tools = "".join(
        f"<tr><td>{escape(t['name'])}{' (blocked)' if t.get('by') == 'blocked' else ''}</td>"
        f"<td>{escape(t.get('stage', ''))}</td><td>{escape(_short(t.get('args', {})))}</td>"
        f"<td>{escape(_short(t.get('result', '')))}</td></tr>" for t in s.tool_calls)
    tools = (f'<div class="scroll"><table><tr><th>tool</th><th>stage</th><th>args</th><th>result</th></tr>'
             f"{tools}</table></div>" if s.tool_calls else '<p class="meta">No tool calls.</p>')

    events = [(g.get("category", ""), g.get("original", ""), g.get("spoken", "")) for g in s.guard_hits]
    events += [(", ".join(map(str, h.get("hits", []))), h.get("original", ""), h.get("spoken", ""))
               for h in s.speech_hits]
    guard = "".join(f"<tr><td>{escape(c)}</td><td>{escape(o)}</td><td>{escape(sp)}</td></tr>"
                    for c, o, sp in events)
    guard = (f'<div class="scroll"><table><tr><th>check</th><th>model wrote</th><th>spoken instead</th></tr>'
             f"{guard}</table></div>" if events
             else '<p class="meta">No sentence was changed or dropped by the code guards.</p>')

    flags = ", ".join(sorted(s.flags)) or "none"
    info = " · ".join(f"{escape(k)} {escape(str(v))}" for k, v in meta.items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Call {escape(s.borrower_id)}</title><style>{CSS}</style></head>
<body><main>
<h1>Call report: {escape(s.borrower_id)}</h1>
<p class="meta">{info}</p>
<div class="kpis">{kpis}</div>
<h2>Outcome record</h2>
<div class="card"><pre>{escape(json.dumps(record, indent=2, ensure_ascii=False), quote=False)}</pre></div>
<h2>Transcript</h2>
{transcript}
<h2>Latency per turn (ms)</h2>
<p class="meta">Gap = VAD stop to first agent audio. The borrower also waits the VAD stop time
before that, shown as "felt". Interrupted turns are left out of the median and p90. On a turn marked
"filler", the first audio was "one second" while the model was slow, not the answer.</p>
{lat_table}
<h2>Tool calls</h2>
{tools}
<h2>Code guard events</h2>
{guard}
<p class="meta">State flags: {escape(flags)} · tokens {escape(str(brain.usage))}</p>
</main></body></html>
"""


def write(brain, rows: list[dict], meta: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(brain, rows, meta), encoding="utf-8")
    return path
