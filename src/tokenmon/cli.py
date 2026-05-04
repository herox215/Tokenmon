import click

from tokenmon import launchd
from tokenmon.pricing import cost_for
from tokenmon.storage import init_db, query_today, query_today_by_model


@click.group()
def main() -> None:
    """Tokenmon — local Anthropic API token tracker."""


@main.command()
def status() -> None:
    """Show today's usage and LaunchAgent state."""
    init_db()
    totals = query_today()
    active = totals.input_tokens + totals.output_tokens
    click.echo(f"Today: {active:,} tokens across {totals.request_count} requests")
    click.echo(f"  input:  {totals.input_tokens:,}")
    click.echo(f"  output: {totals.output_tokens:,}")
    by_model = query_today_by_model()
    if by_model:
        click.echo("\nBy model:")
        total_cost = 0.0
        for model, t in by_model.items():
            cost = cost_for(
                model,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                cache_read_tokens=t.cache_read_tokens,
                cache_creation_tokens=t.cache_creation_tokens,
            )
            total_cost += cost
            click.echo(f"  {model}: {t.input_tokens + t.output_tokens:,} tokens, ${cost:.4f}")
        click.echo(f"\nEstimated cost: ${total_cost:.4f}")
    click.echo("\nLaunchAgents:")
    for line in launchd.status():
        click.echo(f"  {line}")


@main.command()
def install() -> None:
    """Install LaunchAgents so proxy + menubar start at login."""
    for line in launchd.install():
        click.echo(line)


@main.command()
def uninstall() -> None:
    """Remove LaunchAgents."""
    for line in launchd.uninstall():
        click.echo(line)


if __name__ == "__main__":
    main()
