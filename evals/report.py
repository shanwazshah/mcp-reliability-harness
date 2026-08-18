"""Turn one or more scorecard JSON files into a Markdown scorecard.

    python -m evals.report results/*.json --out results/scorecard.md

Multiple inputs render as columns, which is how the model comparison and the
defenses-off/defenses-on comparison are both produced.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

METRIC_LABELS = [
    ("tool_selection", "Tool selection"),
    ("argument_correctness", "Argument correctness"),
    ("abstention", "Correct abstention"),
    ("guardrail_ok", "Destructive guardrail"),
    ("injection_resisted", "Injection resistance"),
    ("answer_ok", "Answer content"),
]


def _cell(stat: dict[str, Any] | None) -> str:
    if not stat or not stat.get("n"):
        return "--"
    return f"{stat['passed']}/{stat['n']} ({stat['rate']:.0%})"


def render(reports: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    headers = []
    for r in reports:
        label = r.get("agent", "?")
        defenses = r.get("defenses")
        if defenses is not None:
            label += f" · defenses {'on' if defenses else 'off'}"
        headers.append(label)

    lines.append("| Metric | " + " | ".join(headers) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in headers) + " |")
    for key, label in METRIC_LABELS:
        row = [_cell(r["summary"].get(key)) for r in reports]
        lines.append(f"| {label} | " + " | ".join(row) + " |")

    lines.append(
        "| Latency p50 / p95 | "
        + " | ".join(
            f"{r['summary']['latency_s']['p50']}s / {r['summary']['latency_s']['p95']}s"
            for r in reports
        )
        + " |"
    )
    lines.append(
        "| Cost (total) | "
        + " | ".join(f"${r['summary']['cost_usd_total']:.4f}" for r in reports)
        + " |"
    )
    lines.append(
        "| Tokens in / out | "
        + " | ".join(
            f"{r['summary']['prompt_tokens_total']} / {r['summary']['completion_tokens_total']}"
            for r in reports
        )
        + " |"
    )
    return "\n".join(lines)


def render_failures(report: dict[str, Any], limit: int = 15) -> str:
    lines = ["| Case | Failed | Note |", "| --- | --- | --- |"]
    count = 0
    for r in report["results"]:
        failed = [k for k, v in r["metrics"].items() if v is False]
        if not failed:
            continue
        note = (r["notes"] or [""])[0].replace("|", "\\|")[:110]
        lines.append(f"| `{r['case_id']}` | {', '.join(failed)} | {note} |")
        count += 1
        if count >= limit:
            break
    if count == 0:
        return "_No failures._"
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default="results/scorecard.md")
    ap.add_argument("--title", default="Scorecard")
    args = ap.parse_args()

    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.inputs]
    body = [f"# {args.title}", "", render(reports), ""]
    for path, report in zip(args.inputs, reports):
        body.append(f"## Failures — `{Path(path).name}`")
        body.append("")
        body.append(render_failures(report))
        body.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
