from dataclasses import dataclass

from cappa import Command, command

from litestar import Litestar
from litestar.plugins import CLIPluginProtocol


class CLIPlugin(CLIPluginProtocol):

    def on_cli_init(self, command: Command) -> None:
        def foo(app: Litestar):
            print(app)

        @dataclass
        @command(invoke=foo)
        class Foo:
            bar: str
            baz: str

app = Litestar(plugins=[CLIPlugin()])