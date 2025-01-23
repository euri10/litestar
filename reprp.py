import asyncio


from litestar import get, Litestar
from litestar.testing import AsyncTestClient


async def test_multiple_clients_event_loop() -> None:
    @get("/")
    async def return_loop_id() -> dict:
        return {"loop_id": id(asyncio.get_event_loop())}

    app = Litestar(route_handlers=[return_loop_id])

    async with AsyncTestClient(app) as client_1, AsyncTestClient(app) as client_2:
        response_1 = await client_1.get("/")
        response_2 = await client_2.get("/")

    assert response_1.json() == response_2.json()