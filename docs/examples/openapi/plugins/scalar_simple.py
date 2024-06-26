from typing import Dict

from litestar import Litestar, get
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin


@get("/", sync_to_thread=False)
def hello_world() -> Dict[str, str]:
    return {"message": "Hello World"}


app = Litestar(
    route_handlers=[hello_world],
    openapi_config=OpenAPIConfig(
        title="Litestar Example",
        description="Example of Litestar with Scalar OpenAPI docs",
        version="0.0.1",
        render_plugins=[ScalarRenderPlugin()],
    ),
)
