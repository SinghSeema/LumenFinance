"""Render docs/issues-and-fixes.md as a shareable HTML page (results/issues-and-fixes.html).

    python3 scripts/render_issues_page.py

The Markdown is the source of truth; edit it, then re-run this. Reads only the shapes that file uses:
'## ' date sections, '### ' areas, issue tables (# | Issue | Evidence | Root cause | Fix | Checked by |
Status), other tables, '- ' bullets and plain paragraphs.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "docs/issues-and-fixes.md", ROOT / "results/issues-and-fixes.html"
ISSUE_COLS = ["#", "Issue", "Evidence", "Root cause", "Fix", "Checked by", "Status"]


def inline(text: str) -> str:
    t = html.escape(text.strip())
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def status_class(status: str) -> str:
    s = status.lower()
    if s.startswith("open") or "still open" in s:
        return "open"
    if "not yet" in s or "pending" in s:
        return "check"
    return "done"


def issue_card(row: dict[str, str]) -> str:
    status = row["Status"]
    first = re.split(r"[;.]", status)[0]
    rest = status[len(first):].lstrip(";. ")
    fields = "".join(f"<div><dt>{k}</dt><dd>{inline(row[k])}</dd></div>"
                     for k in ("Evidence", "Root cause", "Fix", "Checked by") if row[k] not in ("", "—"))
    note = f"<p class='note'>{inline(rest)}</p>" if rest else ""
    return (f"<article class='issue'><header><span class='id'>{row['#']}</span>"
            f"<h4>{inline(row['Issue'])}</h4><span class='chip {status_class(status)}'>{inline(first)}</span>"
            f"</header><dl>{fields}</dl>{note}</article>")


def table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{inline(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


def render(md: str) -> tuple[str, str]:
    lines, out, title, i = md.splitlines(), [], "Issues and Fixes", 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            head, rows = cells(block[0]), [cells(b) for b in block[2:]]
            if head == ISSUE_COLS:
                out.append("<div class='issues'>" + "".join(issue_card(dict(zip(head, r))) for r in rows)
                           + "</div>")
            else:
                out.append(table(head, rows))
            continue
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
        i += 1
    return title, "\n".join(out)


STYLE = """
/* Layout: one reading column; each issue is a card with a status chip and four labelled fields. */
:root {
  --bg: #f6f8f8; --surface: #ffffff; --fg: #17201f; --muted: #556563; --rule: #d8e0df;
  --accent: #0d6b62; --done: #1f7a3d; --done-bg: #e3f3e8; --check: #8a5a00; --check-bg: #fbefd6;
  --open: #a23b2a; --open-bg: #f8e1dc; --code-bg: #eaf0ef;
  --display: "Literata", Georgia, serif; --body: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111716; --surface: #182120; --fg: #e3ebea; --muted: #9db0ad; --rule: #2b3836;
  --accent: #5cc3b6; --done: #7ad69a; --done-bg: #1c3325; --check: #f0c26a; --check-bg: #3a2e14;
  --open: #f39a88; --open-bg: #3d211b; --code-bg: #22302e; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #111716; --surface: #182120; --fg: #e3ebea; --muted: #9db0ad; --rule: #2b3836;
  --accent: #5cc3b6; --done: #7ad69a; --done-bg: #1c3325; --check: #f0c26a; --check-bg: #3a2e14;
  --open: #f39a88; --open-bg: #3d211b; --code-bg: #22302e; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.6 var(--body); }
main { max-width: 860px; margin: 0 auto; padding-block: 40px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: flex; flex-direction: column; gap: 14px; }
h1 { font: 600 2.1rem/1.2 var(--display); margin: 0; text-wrap: balance; }
h2 { font: 600 1.35rem/1.3 var(--display); margin: 28px 0 0; padding-top: 18px; border-top: 1px solid var(--rule); }
h3 { font: 600 .78rem/1.4 var(--body); text-transform: uppercase; letter-spacing: .08em; color: var(--accent); margin: 18px 0 0; }
p, li { max-width: 68ch; } p { margin: 0; } ul { margin: 0; padding-left: 1.2em; }
code { font: .86em var(--mono); background: var(--code-bg); padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
.issues { display: flex; flex-direction: column; gap: 10px; }
.issue { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 14px 16px; min-width: 0; }
.issue header { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: baseline; }
.id { font: 600 .8rem var(--mono); color: var(--accent); }
h4 { font: 600 1rem/1.4 var(--body); margin: 0; min-width: 0; }
.chip { font: 600 .72rem var(--body); letter-spacing: .04em; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.chip.done { color: var(--done); background: var(--done-bg); }
.chip.check { color: var(--check); background: var(--check-bg); }
.chip.open { color: var(--open); background: var(--open-bg); }
dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; margin: 12px 0 0; }
dt { font: 600 .7rem var(--body); text-transform: uppercase; letter-spacing: .07em; color: var(--muted); }
dd { margin: 2px 0 0; min-width: 0; overflow-wrap: anywhere; }
.note { margin-top: 10px; font-size: .9rem; color: var(--muted); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font: 600 .72rem var(--body); text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
.lede { color: var(--muted); }
@media (max-width: 560px) { dl { grid-template-columns: 1fr; } .issue header { grid-template-columns: auto 1fr; }
  .chip { grid-column: 2; justify-self: start; } }
"""


def main() -> None:
    title, body = render(SRC.read_text())
    page = (f"<title>{html.escape(title)}</title>\n"
            '<meta name="claude:viewport-fit-sides" content="cover">\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
            '&family=IBM+Plex+Sans:wght@400;600&family=Literata:wght@600&display=swap">\n'
            f"<style>{STYLE}</style>\n<main>\n<h1>{html.escape(title)}</h1>\n"
            "<p class='lede'>Lumen Finance EMI reminder voice agent (take-home). Generated from "
            "<code>docs/issues-and-fixes.md</code>.</p>\n" + body + "\n</main>\n")
    OUT.write_text(page)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
