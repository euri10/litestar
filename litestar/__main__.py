import cappa

from litestar.cli.cappa_cli import LitestarCappa, LL


def run_cli() -> None:
    """Application Entrypoint."""
    cappa.invoke(LL(LitestarCappa))


if __name__ == "__main__":
    run_cli()
