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
    """Check system health (PostgreSQL, Redis, Jaeger)"""
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
@click.argument("checkpoint_id")
def replay(checkpoint_id: str) -> None:
    """Replay workflow from checkpoint"""
    click.echo(f"Replaying from checkpoint: {checkpoint_id}")
    click.echo("⚠️  Not implemented - Phase 4 deliverable")


if __name__ == "__main__":
    main()
