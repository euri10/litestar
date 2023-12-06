import cappa

from litestar.cli.cappa_cli import LitestarCappa


def run_cli() -> None:
    """Application Entrypoint."""
    cappa.invoke(LitestarCappa)


if __name__ == "__main__":
    run_cli()
