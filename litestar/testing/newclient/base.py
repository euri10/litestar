from __future__ import annotations

import sys  # Import the sys module
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Generic, TypeVar, cast

import anyio
from httpx import ASGITransport, AsyncClient, Client  # Import ASGITransport

from litestar import Litestar
from litestar.testing.client.base import _wrap_app_to_add_state
from litestar.types import (
    ASGIApp,
    LifeSpanReceiveMessage,
    LifeSpanScope,
    LifeSpanSendMessage,
)

T_App = TypeVar("T_App", bound=ASGIApp)

if TYPE_CHECKING:
    from types import TracebackType

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


class MyLifespanManager:
    """Manages the ASGI lifespan protocol for a given application using anyio."""

    def __init__(self, app: ASGIApp):
        if not isinstance(app, Litestar):
            app = _wrap_app_to_add_state(app)
        self.app = app
        self._startup_event = anyio.Event()
        self._shutdown_event = anyio.Event()
        self._startup_failed_message: str | None = None
        self._shutdown_failed_message: str | None = None

        self._manager_to_app_send_stream: MemoryObjectSendStream[dict]
        self._manager_to_app_receive_stream: MemoryObjectReceiveStream[dict]
        self._manager_to_app_send_stream, self._manager_to_app_receive_stream = anyio.create_memory_object_stream(
            max_buffer_size=1
        )

        self._app_to_manager_send_stream: MemoryObjectSendStream[dict]
        self._app_to_manager_receive_stream: MemoryObjectReceiveStream[dict]
        # This stream is for messages from app to manager, currently only used for event setting
        self._app_to_manager_send_stream, self._app_to_manager_receive_stream = anyio.create_memory_object_stream(
            max_buffer_size=1
        )

        self._task_group: anyio.abc.TaskGroup | None = None

    async def _asgi_receive(self) -> LifeSpanReceiveMessage:  # Updated signature
        """Called by the ASGI app to receive messages from the lifespan manager."""
        return cast(LifeSpanReceiveMessage, await self._manager_to_app_receive_stream.receive())

    async def _asgi_send(self, message: LifeSpanSendMessage) -> None:  # Updated signature
        """Called by the ASGI app to send messages to the lifespan manager."""
        if message["type"] == "lifespan.startup.complete":
            self._startup_event.set()
        elif message["type"] == "lifespan.startup.failed":
            self._startup_failed_message = message.get("message", "Lifespan startup failed without a message.")
            self._startup_event.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self._shutdown_event.set()
        elif message["type"] == "lifespan.shutdown.failed":
            self._shutdown_failed_message = message.get("message", "Lifespan shutdown failed without a message.")
            self._shutdown_event.set()
        # If app sends other messages, they could be passed via self._app_to_manager_send_stream.send(message)
        # For this example, we only handle lifespan completion/failure events.

    async def _run_app_lifespan(self) -> None:
        """Runs the ASGI application with the lifespan scope."""
        scope: LifeSpanScope = {
            "type": "lifespan",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
        }  # Explicitly typed scope
        try:
            await self.app(scope, self._asgi_receive, self._asgi_send)
        except anyio.get_cancelled_exc_class():
            # If the task is cancelled, ensure events are marked appropriately if not already.
            if not self._startup_event.is_set():
                self._startup_failed_message = self._startup_failed_message or "ASGI app cancelled during startup."
                self._startup_event.set()
            if not self._shutdown_event.is_set():
                self._shutdown_failed_message = self._shutdown_failed_message or "ASGI app cancelled during shutdown."
                self._shutdown_event.set()
            raise
        except RuntimeError as e:  # Changed from Exception
            print(f"LifespanManager: ASGI app instance raised an exception during lifespan: {e}")
            if not self._startup_event.is_set():
                self._startup_failed_message = f"ASGI app crashed: {e}"
                self._startup_event.set()
            if not self._shutdown_event.is_set():  # Check if shutdown already processed
                self._shutdown_failed_message = f"ASGI app crashed: {e}"
                self._shutdown_event.set()  # Ensure shutdown event is set to unblock waiters
        finally:
            if not self._startup_event.is_set():
                self._startup_failed_message = (
                    self._startup_failed_message or "ASGI app exited prematurely during startup."
                )
                self._startup_event.set()
            if not self._shutdown_event.is_set():
                self._shutdown_failed_message = (
                    self._shutdown_failed_message or "ASGI app exited prematurely during shutdown."
                )
                self._shutdown_event.set()
            # Close stream ends that _run_app_lifespan is responsible for (app's perspective)
            await self._manager_to_app_send_stream.aclose()  # Manager stops sending to app
            await self._app_to_manager_send_stream.aclose()  # App stops sending to manager

    async def _perform_startup(self) -> None:
        """Internal startup logic, called by __aenter__."""
        print("LifespanManager: Initiating startup...")
        await self._manager_to_app_send_stream.send({"type": "lifespan.startup"})
        try:
            with anyio.fail_after(10):  # 10-second timeout for startup
                await self._startup_event.wait()
        except TimeoutError:
            self._startup_failed_message = "Lifespan startup timed out."
            # Task group cancellation will handle _run_app_lifespan

        if self._startup_failed_message:
            raise RuntimeError(f"Lifespan startup failed: {self._startup_failed_message}")
        print("LifespanManager: Startup complete.")

    async def _perform_shutdown(self) -> None:
        """Internal shutdown logic, called by __aexit__ or explicitly."""
        if self._startup_failed_message or not self._startup_event.is_set():
            print("LifespanManager: Shutdown skipped (startup failed or not successfully completed).")
            return

        if self._shutdown_event.is_set() and not self._shutdown_failed_message:  # Already successfully shut down
            print("LifespanManager: Shutdown already completed successfully.")
            return

        print("LifespanManager: Initiating shutdown...")
        self._shutdown_event = anyio.Event()  # Reset for this attempt
        self._shutdown_failed_message = None

        await self._manager_to_app_send_stream.send({"type": "lifespan.shutdown"})
        try:
            with anyio.fail_after(10):  # 10-second timeout for shutdown
                await self._shutdown_event.wait()
        except TimeoutError:
            self._shutdown_failed_message = "Lifespan shutdown timed out."

        if self._shutdown_failed_message:
            raise RuntimeError(f"Lifespan shutdown failed: {self._shutdown_failed_message}")
        print("LifespanManager: Shutdown complete.")

    async def __aenter__(self) -> MyLifespanManager:
        print("LifespanManager: Entering async context...")
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        self._task_group.start_soon(self._run_app_lifespan)
        try:
            await self._perform_startup()
        except Exception:  # Keeping this broad for initial startup, could be narrowed if specific errors are expected
            # If startup fails, ensure the task group is exited
            self._task_group.cancel_scope.cancel()
            await self._task_group.__aexit__(*sys.exc_info())  # Use sys.exc_info()
            self._task_group = None  # Mark as exited
            raise
        return self

    async def __aexit__(  # Updated signature for more precise exception types
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        print("LifespanManager: Exiting async context...")
        original_exc_info = (exc_type, exc_val, exc_tb)
        shutdown_exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None] = (
            None,
            None,
            None,
        )  # Updated type hint

        if self._task_group:
            if not self._startup_failed_message and self._startup_event.is_set() and not self._shutdown_event.is_set():
                try:
                    print("LifespanManager: Performing shutdown in __aexit__...")
                    await self._perform_shutdown()
                except RuntimeError as e_shutdown:  # Changed from Exception
                    print(f"LifespanManager: Error during shutdown in __aexit__: {e_shutdown}")
                    shutdown_exc_info = (type(e_shutdown), e_shutdown, e_shutdown.__traceback__)
                    if original_exc_info[0] is None:  # Propagate shutdown error if no other error
                        original_exc_info = shutdown_exc_info

            self._task_group.cancel_scope.cancel()
            # Pass the original exception or shutdown exception if it occurred
            await self._task_group.__aexit__(*original_exc_info)
            self._task_group = None

        # Close receive streams from manager's perspective
        # Send streams are closed by _run_app_lifespan or when task_group cancels it.
        await self._manager_to_app_receive_stream.aclose()
        await self._app_to_manager_receive_stream.aclose()
        print("LifespanManager: Async context exited.")
        if original_exc_info[0] is not None and original_exc_info[0] != exc_type:  # if shutdown added an exception
            # This logic is tricky; __aexit__ should not raise a *new* exception if one is already propagating
            # unless it's a more severe one. For now, we rely on task group's exception handling.
            pass


class MySyncTestClient(Client, Generic[T_App]):
    def __init__(self, app: T_App, base_url: str = "http://testserver.local"):
        self.app = app
        # Let httpx.Client handle ASGI app, portal, and lifespan for sync client
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        super().__init__(transport=transport, base_url=base_url)
        print(f"MySyncTestClient initialized for app: {app} (using httpx lifespan)")

        # The following custom portal and lifespan manager are no longer needed here,
        # as httpx.Client's ASGITransport will manage its own.
        # self._exit_stack = ExitStack()
        # self._portal: anyio.from_thread.BlockingPortal | None = None
        # self._lifespan_manager: MyLifespanManager | None = None

    def __enter__(self) -> MySyncTestClient:
        print("MySyncTestClient: Entering context (delegating to httpx)...")
        # super().__enter__() will correctly initialize the lifespan via ASGITransport
        return super().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        print("MySyncTestClient: Exiting context (delegating to httpx)...")
        # super().__exit__() will correctly shut down the lifespan via ASGITransport
        super().__exit__(exc_type, exc_val, exc_tb)
        print("MySyncTestClient closed.")


class MyAsyncTestClient(AsyncClient, Generic[T_App]):
    def __init__(self, app: T_App, base_url: str = "http://testserver.local"):  # Removed **kwargs
        self.app = app
        # Pass an ASGITransport instance to super() for in-memory testing.
        transport = ASGITransport(app=app)  # type: ignore[arg-type] # Add type ignore for now
        super().__init__(transport=transport, base_url=base_url)  # Removed **kwargs
        print(f"MyAsyncTestClient initialized for app: {app}")

        self._lifespan_manager: MyLifespanManager | None = None
        self._async_exit_stack = AsyncExitStack()

    async def __aenter__(self) -> MyAsyncTestClient:
        print("MyAsyncTestClient: Entering async context...")
        self._lifespan_manager = MyLifespanManager(self.app)
        try:
            await self._async_exit_stack.enter_async_context(self._lifespan_manager)
            # Call super's aenter directly and push its aexit to the stack
            await super().__aenter__()
            self._async_exit_stack.push_async_exit(super().__aexit__)
        except Exception as e:
            print(f"MyAsyncTestClient: Lifespan or client startup failed during __aenter__: {e}")
            await self._async_exit_stack.aclose()
            raise
        print("MyAsyncTestClient: Async context entered.")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        print("MyAsyncTestClient: Exiting async context...")
        # The AsyncExitStack's aclose method itself doesn't take the exception details directly.
        # It propagates them to the registered callbexc_tbacks.
        await self._async_exit_stack.aclose()
        print("MyAsyncTestClient closed.")
