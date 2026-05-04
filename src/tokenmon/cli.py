import click

from tokenmon import config, launchd
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
        priced_tokens = 0
        all_tokens = 0
        for model, t in by_model.items():
            cost, has_price = cost_for(
                model,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                cache_read_tokens=t.cache_read_tokens,
                cache_creation_tokens=t.cache_creation_tokens,
            )
            total_cost += cost
            tokens = (
                t.input_tokens + t.output_tokens
                + t.cache_read_tokens + t.cache_creation_tokens
            )
            all_tokens += tokens
            if has_price:
                priced_tokens += tokens
            cost_str = f"${cost:.4f}" if has_price else "(no pricing)"
            click.echo(f"  {model}: {t.input_tokens + t.output_tokens:,} tokens, {cost_str}")
        suffix = ""
        if all_tokens > 0 and priced_tokens < all_tokens:
            coverage = priced_tokens / all_tokens
            suffix = f" ({coverage:.0%} priced)"
        click.echo(f"\nEstimated cost: ${total_cost:.4f}{suffix}")
    click.echo("\nLaunchAgents:")
    for line in launchd.status():
        click.echo(f"  {line}")


@main.command()
@click.option(
    "--providers",
    default=None,
    help="Comma-separated list of providers (e.g. 'anthropic,openrouter'). "
         "Defaults to whatever is in config.json's proxy_providers.",
)
def install(providers: str | None) -> None:
    """Install LaunchAgents so the proxy + menubar start at login."""
    if providers:
        prov_list = [p.strip() for p in providers.split(",") if p.strip()]
        config.set_("proxy_providers", prov_list)
    else:
        prov_list = list(config.get("proxy_providers") or ["anthropic"])
    click.echo(f"Providers: {', '.join(prov_list)}")
    for line in launchd.install(prov_list):
        click.echo(line)


@main.command()
def uninstall() -> None:
    """Remove LaunchAgents."""
    for line in launchd.uninstall():
        click.echo(line)


if __name__ == "__main__":
    main()
