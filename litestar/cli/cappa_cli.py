from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass
from typing import Annotated

import cappa
import uvicorn
from cappa import Dep, Subcommands
from rich.tree import Tree

from litestar.cli._utils import (
    UVICORN_INSTALLED,
    LitestarEnv,
    _run_uvicorn_in_subprocess,
    _server_lifespan,
    console,
    show_app_info,
)
from litestar.routes import HTTPRoute, WebSocketRoute
from litestar.utils.helpers import unwrap_partial


def info_command(_cli: LitestarCappa, env: Annotated[LitestarEnv, Dep(env)]) -> None:
    """Show information about the Litestar app."""
    if env.app is None:
        raise cappa.Exit(message="No Litestar app found", code=1)
    show_app_info(env.app)


def version_command(version: Version) -> None:
    """Show the currently installed Litestar version."""
    from litestar import __version__

    console.print(__version__.formatted(short=version.short))


def run_command(
    _cli: LitestarCappa, run: Run, env: Annotated[LitestarEnv, Dep(env)]
) -> None:  # sourcery skip: low-code-quality
    """Run a Litestar app; requires ``uvicorn``.

    The app can be either passed as a module path in the form of <module name>.<submodule>:<app instance or factory>,
    set as an environment variable LITESTAR_APP with the same format or automatically discovered from one of these
    canonical paths: app.py, asgi.py, application.py or app/__init__.py. When auto-discovering application factories,
    functions with the name ``create_app`` are considered, or functions that are annotated as returning a ``Litestar``
    instance.
    """

    if run.debug:
        os.environ["LITESTAR_DEBUG"] = "1"

    if run.pdb:
        os.environ["LITESTAR_PDB"] = "1"

    if not UVICORN_INSTALLED:
        console.print(
            r"uvicorn is not installed. Please install the standard group, litestar\[standard], to use this command."
        )
        sys.exit(1)

    if run.debug:
        env.app.debug = True
    if run.pdb:
        env.app.pdb_on_exception = True

    reload_dirs = env.reload_dirs or run.reload_dir

    console.rule("[yellow]Starting server process", align="left")

    show_app_info(env.app)
    with _server_lifespan(env.app):
        if run.wc == 1 and not run.reload:
            # A guard statement at the beginning of this function prevents uvicorn from being unbound
            # See "reportUnboundVariable in:
            # https://microsoft.github.io/pyright/#/configuration?id=type-check-diagnostics-settings
            uvicorn.run(  # pyright: ignore
                app=_cli.app,
                host=run.host,
                port=run.port,
                fd=run.fd,
                uds=run.uds,
                factory=env.is_app_factory,
                ssl_certfile=run.ssl_certfile,
                ssl_keyfile=run.ssl_keyfile,
            )
        else:
            # invoke uvicorn in a subprocess to be able to use the --reload flag. see
            # https://github.com/litestar-org/litestar/issues/1191 and https://github.com/encode/uvicorn/issues/1045
            if sys.gettrace() is not None:
                console.print(
                    "[yellow]Debugger detected. Breakpoints might not work correctly inside route handlers when running"
                    " with the --reload or --workers options[/]"
                )

            _run_uvicorn_in_subprocess(
                env=env,
                host=run.host,
                port=run.port,
                workers=run.wc,
                reload=run.reload,
                reload_dirs=reload_dirs,
                fd=run.fd,
                uds=run.uds,
                certfile_path=run.ssl_certfile,
                keyfile_path=run.ssl_keyfile,
            )


def routes_command(cli: LitestarCappa, env: Annotated[LitestarEnv, Dep(env)]) -> None:  # pragma: no c
    """Display information about the application's routes."""

    tree = Tree("", hide_root=True)

    for route in sorted(env.app.routes, key=lambda r: r.path):
        if isinstance(route, HTTPRoute):
            branch = tree.add(f"[green]{route.path}[/green] (HTTP)")
            for handler in route.route_handlers:
                handler_info = [
                    f"[blue]{handler.name or handler.handler_name}[/blue]",
                ]

                if inspect.iscoroutinefunction(unwrap_partial(handler.fn)):
                    handler_info.append("[magenta]async[/magenta]")
                else:
                    handler_info.append("[yellow]sync[/yellow]")

                handler_info.append(f'[cyan]{", ".join(sorted(handler.http_methods))}[/cyan]')

                if len(handler.paths) > 1:
                    for path in handler.paths:
                        branch.add(" ".join([f"[green]{path}[green]", *handler_info]))
                else:
                    branch.add(" ".join(handler_info))

        else:
            route_type = "WS" if isinstance(route, WebSocketRoute) else "ASGI"
            branch = tree.add(f"[green]{route.path}[/green] ({route_type})")
            branch.add(f"[blue]{route.route_handler.name or route.route_handler.handler_name}[/blue]")

    console.print(tree)


@cappa.command(invoke=info_command)
@dataclass
class Info:
    ...


@cappa.command(invoke=version_command)
@dataclass
class Version:
    short: Annotated[bool, cappa.Arg(short=True, default=False)]


@cappa.command(invoke=run_command)
@dataclass
class Run:
    reload: Annotated[bool, cappa.Arg(long=True, short=True, default=False)]
    reload_dir: Annotated[None | list[str], cappa.Arg(short="R", long=True, default=None)]
    port: Annotated[int, cappa.Arg(short=True, long=True, default=8000)]
    wc: Annotated[int, cappa.Arg(short=True, long="web-concurrency", default=1)]
    host: Annotated[str, cappa.Arg(short=True, long=True, default="127.0.0.1")]
    fd: Annotated[int | None, cappa.Arg(short=True, long=True, default=None)]
    uds: Annotated[str | None, cappa.Arg(short=True, long=True, default=None)]
    debug: Annotated[bool, cappa.Arg(short=True, long=True, default=False)]
    pdb: Annotated[bool, cappa.Arg(short=True, long=True, default=False)]
    ssl_certfile: Annotated[str | None, cappa.Arg(short=True, long=True, default=None)]
    ssl_keyfile: Annotated[str | None, cappa.Arg(short=True, long=True, default=None)]
    create_self_signed_cert: Annotated[bool, cappa.Arg(short=True, default=False)]


@cappa.command(invoke=routes_command)
@dataclass
class Routes:
    ...


def env(cli: LitestarCappa) -> LitestarEnv | None:
    return LitestarEnv.from_env(app_path=cli.app, app_dir=cli.app_dir)


@dataclass
class LitestarCappa:
    subcommands: Subcommands[Info | Version | Run | Routes]
    app: Annotated[str | None, cappa.Arg(long=True, default=None)]
    app_dir: Annotated[str | None, cappa.Arg(long=True, default=None)]
