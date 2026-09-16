# dsh-origin-plugin (EN)

> Chinese README: see [README.md](README.md). English version below is a
> condensed overview; the Chinese doc is authoritative.

AI-driven OriginLab Origin plotting over MCP. 62 tools / 30 error codes,
built around a workflow SOP (not a tool dump): environment → inspect → plan →
execute → verify → recover → deliver, with AI image review and a visual
regression baseline.

> **Positioning in one line**: the deliverable is an **editable Origin project file (OPJU) plus a
reproducible FigureSpec YAML** — every line, marker and axis property stays hand-tunable in Origin and
reproducible months later. Text-to-figure / text-to-SVG routes (AutoFigure-like) emit one-off vector
objects; editability granularity and scientific reproducibility are different levels.

## Why

Chat AIs can call Matplotlib, but not Origin. Advisors/journals demand Origin.
This MCP server turns Origin into a deterministic plotting backend with
editable `.opju` as a first-class deliverable.

## Install

| Method | Command |
|---|---|
| A. Register clients | `python install.py` (auto-detect Claude/Cursor/VSCode/Cline/Continue/Kimi/DSH) |
| B. stdio entry | `python origin_mcp_stdio.py --print-config` |
| C. DSH plugin | install `dsh-origin-plugin` from 1024Store |
| D. local build | `pip install .` |

Requirements: Windows, Origin 2021+ (2026 tested), Python 3.10+.

## Quick start

```json
{"columns": {"temperature_C":[20,25,30,35], "pressure_kPa":[95,101,112,118]},
 "plot_type": "line_symbol", "fmt": "png", "style_mode": "journal"}
```

Workflow: `origin_plot_plan` (offline, raises questions) → user confirms →
`origin_execute_plan` → `origin_verify_graph` → `origin_export_delivery`
(PNG/PDF + editable OPJU + CSV + report, all checksum-verified).

## Highlights

- **Deterministic**: plot/writing channels verified by readback; NaN-aware
  (no fake success); exports validated by file magic bytes with a 3-level
  fallback chain.
- **Modal-dialog watchdog**: Origin popups are auto-dismissed (OK/Cancel),
  hard timeout reports the dialog title instead of hanging.
- **LabTalk safety gate**: destructive commands (`delete`, `doc -s`, ...)
  blocked unless `confirm=true`.
- **FigureSpec**: declarative YAML spec (diff-able, replayable, same
  plan_id on re-import) feeding the same confirm-then-execute flow.
- **MCP Resources**: read-only session snapshot (`origin://session`) for
  inspection without side effects.
- **Fit control**: initial params / fixed params / weighted column.
- **Fine-grained edits**: per-plot color/width/marker/visibility, axis,
  legend, page geometry; sort/transpose; remove/swap curves.
- **Bridges**: matplotlib Figure pickle → Origin; PPT assembly (PNG +
  panel letters); OriginLab Graph Gallery search/download.
- **Visual regression baseline**: dHash over 12 reference PNGs catches
  cosmetic regressions that numeric asserts miss.
- **Science boundaries**: no fabricated data, uncertain columns trigger
  questions, stats come with a confidence note.

See [COMPATIBILITY.md](COMPATIBILITY.md) for 15 tested failure scenarios and
[ROADMAP.md](ROADMAP.md) for the roadmap derived from 11 high-star GitHub
Origin projects.

## Test

```
python -m pytest tests/ -v        # offline tests always; live tests skip without Origin
python origin_mcp_server.py --offline-test
python origin_mcp_server.py --selftest   # full chain, needs Origin
```

MIT License. Not affiliated with OriginLab.
