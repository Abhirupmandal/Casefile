"""
`python -m casefile.evaluation` — run the golden suite and write a report.

Exit code 0 when overall status is PASS; 1 otherwise. Offline only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from casefile.evaluation.reports import build_report, report_to_markdown, write_report
from casefile.evaluation.runner import ScenarioRunner
from casefile.evaluation.scenarios import GOLDEN_SCENARIOS


def main(argv: list[str] | None = None) -> int:
    """Run golden scenarios; write JSON (and optional markdown) report."""
    parser = argparse.ArgumentParser(
        prog="python -m casefile.evaluation",
        description="Run the CASEFILE golden evaluation suite (G01–G15).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Path for the JSON report (default: ./evaluation-report.json)",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Optional path for a markdown summary",
    )
    parser.add_argument(
        "--scenario",
        "-s",
        action="append",
        default=None,
        help="Run only these scenario ids (repeatable); default: all golden",
    )
    args = parser.parse_args(argv)

    ids = args.scenario or sorted(GOLDEN_SCENARIOS)
    runner = ScenarioRunner()
    results = []
    failures: list[str] = []
    for scenario_id in ids:
        scenario = GOLDEN_SCENARIOS[scenario_id]
        result = runner.run(scenario)
        results.append(result)
        if result.status != "PASS":
            failures.append(f"{scenario_id}: {result.safe_error or result.status}")

    report = build_report(
        results,
        failures=failures,
        scenario_versions={r.scenario_id: r.scenario_version for r in results},
    )
    output = args.output or Path("evaluation-report.json")
    write_report(report, output)
    if args.markdown is not None:
        args.markdown.write_text(report_to_markdown(report), encoding="utf-8")

    print(report_to_markdown(report))
    print(f"Report written to {output}")
    return 0 if report.overall_status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
