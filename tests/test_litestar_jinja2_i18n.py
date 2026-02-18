import gettext
from contextvars import ContextVar
from pathlib import Path
import subprocess
import pytest
from jinja2 import Environment, FileSystemLoader
from litestar import Litestar, Request, get
from litestar.response import Template
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from litestar.testing import AsyncTestClient


async def test_litestar_jinja2_i18n_accept_language(tmp_path: Path):

    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template_path = template_dir / "hello.html"
    with open(template_path, "w") as f:
        f.write('{% trans %}Hello{% endtrans %}')

    locale_dir = tmp_path / "locale"
    langs = {"en": "Hello", "pl": "Cześć", "zh": "你好"}
    domain = "messages"

    for lang, translation in langs.items():
        lang_dir = locale_dir / lang / "LC_MESSAGES"
        lang_dir.mkdir(parents=True)
        po_path = lang_dir / f"{domain}.po"
        mo_path = lang_dir / f"{domain}.mo"
        with open(po_path, "w") as f:
            f.write(
                f'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=utf-8\\n"
"Language: {lang}\\n"

#: {template_dir}/hello.html:1
msgid "Hello"
msgstr "{translation}"
'''
            )
        try:
            subprocess.run(["msgfmt", str(po_path), "-o", str(mo_path)], check=True)
        except Exception:
            pytest.skip("msgfmt not available to compile .po files")

    # Build a per-language translation object
    translations = {
        lang: gettext.translation(domain, localedir=str(locale_dir), languages=[lang])
        for lang in langs
    }

    # ContextVar holds the active translation for the current request context
    current_translation: ContextVar[gettext.NullTranslations] = ContextVar(
        "current_translation", default=translations["en"]
    )

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        extensions=["jinja2.ext.i18n"],
        autoescape=True,
    )
    # Delegate gettext/ngettext to whatever the ContextVar holds at render time
    env.install_gettext_callables(  # type: ignore[attr-defined]
        lambda s: current_translation.get().gettext(s),
        lambda s, p, n: current_translation.get().ngettext(s, p, n),
        newstyle=True,
    )

    template_engine = JinjaTemplateEngine.from_environment(env)

    @get("/")
    async def handler(request: Request) -> Template:
        accept_lang = request.headers.get("accept-language", "en")
        lang = accept_lang.split("-")[0].split(";")[0].strip()
        current_translation.set(translations.get(lang, translations["en"]))
        return Template(template_name="hello.html")

    app = Litestar(route_handlers=[handler], template_config=TemplateConfig(engine=template_engine))

    async with AsyncTestClient(app) as client_en, AsyncTestClient(app) as client_pl, AsyncTestClient(app) as client_zh, AsyncTestClient(app) as client_default:
        resp_en = await client_en.get("/", headers={"accept-language": "en"})
        print(resp_en.text)
        assert resp_en.status_code == 200
        assert "Hello" in resp_en.text

        resp_pl = await client_pl.get("/", headers={"accept-language": "pl"})
        print(resp_pl.text)
        assert resp_pl.status_code == 200
        assert "Cześć" in resp_pl.text

        resp_default = await client_default.get("/")
        print(resp_default.text)
        assert resp_default.status_code == 200
        assert "Hello" in resp_default.text

        resp_zh = await client_zh.get("/", headers={"accept-language": "zh"})
        print(resp_zh.text)
        assert resp_zh.status_code == 200
        assert "你好" in resp_zh.text

