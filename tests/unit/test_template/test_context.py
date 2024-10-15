from pathlib import Path
from typing import Any

import pytest

from litestar import MediaType, Request, get
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.contrib.mako import MakoTemplateEngine
from litestar.contrib.minijinja import MiniJinjaTemplateEngine
from litestar.response.template import Template
from litestar.template.config import TemplateConfig
from litestar.testing import create_test_client


@pytest.mark.parametrize(
    "engine, template, expected",
    (
        (JinjaTemplateEngine, 'path: {{ request.scope["path"] }}', "path: /"),
        (MakoTemplateEngine, 'path: ${request.scope["path"]}', "path: /"),
        (MiniJinjaTemplateEngine, 'path: {{ request.scope["path"] }}', "path: &#x2f;"),
    ),
)
def test_request_is_set_in_context(engine: Any, template: str, expected: str, tmp_path: Path) -> None:
    Path(tmp_path / "abc.html").write_text(template)

    @get(path="/", media_type=MediaType.HTML)
    def handler() -> Template:
        return Template(template_name="abc.html", context={"request": {"scope": {"path": "nope"}}})

    with create_test_client(
        route_handlers=[handler],
        template_config=TemplateConfig(
            directory=tmp_path,
            engine=engine,
        ),
    ) as client:
        response = client.get("/")
        assert response.text == expected


def test_context_processor(tmp_path: Path) -> None:
    Path(tmp_path / "processor.html").write_text("""{{ bar_key }}-{{ foo_key }}""")

    @get(path="/")
    def handler() -> Template:
        return Template(template_name="processor.html")

    def foo_processor(request: Request):
        return "bar"

    def bar_processor(request: Request):
        return "foo"

    with create_test_client(
        route_handlers=[handler],
        template_config=TemplateConfig(
            directory=tmp_path,
            engine=JinjaTemplateEngine,
            context_processors={
                "foo_key": foo_processor,
                "bar_key": bar_processor,
            },
        ),
    ) as client:
        response = client.get("/")
        assert response.text == "foo-bar"
