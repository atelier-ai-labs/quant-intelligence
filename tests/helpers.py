from fastapi.testclient import TestClient


def make_test_client(app):
    """Build a synchronous API client reliable on the supported test runtime."""
    # Python 3.14's default asyncio cross-thread portal can stall in this WSL
    # environment. uvloop is already installed through uvicorn[standard] and
    # keeps Starlette's blocking TestClient portal progressing. This is test
    # harness configuration only; production application behavior is unchanged.
    return TestClient(app, backend_options={"use_uvloop": True})
