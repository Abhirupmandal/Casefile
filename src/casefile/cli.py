"""
CASEFILE Command Line Interface

Provides commands for:
- Running workflows
- Database migrations
- Evaluation suite
- System health checks
"""

import click


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """CASEFILE - AI-Powered Insurance Claim Adjudication System"""
    pass


@main.command()
def version() -> None:
    """Show version information"""
    click.echo("CASEFILE v0.1.0")
    click.echo("Phase 1: Repository Foundation & Core Infrastructure")


@main.command()
@click.option(
    "--env", default="development", help="Environment (development, test, staging, production)"
)
def healthcheck(env: str) -> None:
    """Check system health (SQLite, Redis, Jaeger)"""
    from casefile.config import load_config
    from casefile.health import check_system_health

    click.echo(f"Running CASEFILE health checks for environment: {env}")
    config = load_config(env=env)
    health = check_system_health(config)

    click.echo(f"\nEnvironment: {health.environment}")
    click.echo(f"Overall Status: {health.overall_status.upper()}")
    click.echo("-" * 50)

    for name, svc in health.services.items():
        tag = (
            "[PASS]"
            if svc.status == "healthy"
            else ("[DISABLED]" if svc.status == "disabled" else "[FAIL]")
        )
        latency_str = f" ({svc.latency_ms}ms)" if svc.latency_ms is not None else ""
        click.echo(f"{tag:<10} {name.upper()}: {svc.status.upper()}{latency_str}")
        if svc.details:
            click.echo(f"   Details: {svc.details}")
        if svc.error:
            click.echo(f"   Error: {svc.error}")

    click.echo("-" * 50)
    if health.overall_status == "unhealthy":
        raise click.ClickException("Health check failed - one or more services are unreachable.")


@main.command()
@click.argument("claim_id")
def process_claim(claim_id: str) -> None:
    """Process a claim through the workflow"""
    click.echo(f"Processing claim: {claim_id}")
    click.echo("⚠️  Not implemented - Phase 2 deliverable")


@main.command()
def run_evaluation() -> None:
    """Run the evaluation suite (30+ scenarios)"""
    click.echo("Running evaluation suite...")
    click.echo("⚠️  Not implemented - Phase 5 deliverable")


@main.command()
@click.option(
    "--output",
    "-o",
    default="evaluation-report.json",
    show_default=True,
    help="Path for the JSON evaluation report",
)
@click.option(
    "--markdown",
    default=None,
    help="Optional path for a markdown evaluation summary",
)
@click.option(
    "--scenario",
    "-s",
    "scenarios",
    multiple=True,
    help="Run only these scenario ids (repeatable); default: all 30 dataset cases",
)
@click.option(
    "--dataset",
    is_flag=True,
    help="Run the 30-case Phase 6 evaluation dataset",
)
@click.option(
    "--golden",
    is_flag=True,
    help="Run the 15 golden evaluation scenarios (G01–G15)",
)
def evaluate(
    output: str,
    markdown: str | None,
    scenarios: tuple[str, ...],
    dataset: bool = False,
    golden: bool = False,
) -> None:
    """Run offline evaluation (30-case dataset or G01–G15) and write reports."""
    from pathlib import Path

    from casefile.evaluation.dataset import DATASET_CASES, EVALUATION_DATASET
    from casefile.evaluation.reporter import (
        export_evaluation_run_json,
        format_markdown_report,
        write_markdown_report,
    )
    from casefile.evaluation.reports import (
        build_report,
        report_to_markdown,
        write_report,
    )
    from casefile.evaluation.runner import ScenarioRunner
    from casefile.evaluation.scenarios import GOLDEN_SCENARIOS

    runner = ScenarioRunner()

    run_dataset_mode = dataset or (
        not golden and not scenarios and not output.endswith("eval.json")
    )
    if scenarios and any(s.startswith("CASE-") for s in scenarios):
        run_dataset_mode = True

    if run_dataset_mode and not golden:
        target_cases = (
            [DATASET_CASES[s] for s in scenarios if s in DATASET_CASES]
            if scenarios
            else list(EVALUATION_DATASET)
        )
        if scenarios and not target_cases:
            raise click.ClickException(f"No matching cases found for {scenarios}")

        eval_run = runner.run_dataset(target_cases)
        json_path = export_evaluation_run_json(eval_run, Path(output))
        md_dest = Path(markdown or "docs/evaluation-report.md")
        write_markdown_report(eval_run, md_dest)

        click.echo(format_markdown_report(eval_run))
        click.echo(f"Report written to {json_path}")
        click.echo(f"Markdown report written to {md_dest}")
        if eval_run.failed > 0:
            raise click.ClickException(f"Evaluation failed: {eval_run.failed} scenarios failed")
        return

    ids = list(scenarios) if scenarios else sorted(GOLDEN_SCENARIOS)
    results = []
    failures: list[str] = []
    for scenario_id in ids:
        if scenario_id not in GOLDEN_SCENARIOS:
            raise click.ClickException(f"Unknown scenario ID: {scenario_id}")
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
    path = write_report(report, Path(output))
    if markdown is not None:
        Path(markdown).write_text(report_to_markdown(report), encoding="utf-8")

    click.echo(report_to_markdown(report))
    click.echo(f"Report written to {path}")
    if report.overall_status != "PASS":
        raise click.ClickException(f"Evaluation failed: {report.overall_status}")


@main.command()
@click.option("--host", default="127.0.0.1", help="Host interface to bind")
@click.option("--port", default=8000, type=int, help="Port to bind")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the CASEFILE REST API and Operations Console server"""
    import uvicorn

    click.echo(f"Starting CASEFILE Operations API server on http://{host}:{port}")
    uvicorn.run("casefile.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
