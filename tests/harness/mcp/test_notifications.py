import asyncio

from koder_agent.mcp.notifications import MCPNotificationHandler


def test_tools_list_changed_fires_callbacks():
    handler = MCPNotificationHandler()
    results = []

    async def on_refresh(kind, server):
        results.append((kind, server))

    handler.on_refresh(on_refresh)
    asyncio.run(handler.handle_tools_list_changed("test-server"))
    assert results == [("tools", "test-server")]


def test_resources_list_changed():
    handler = MCPNotificationHandler()
    results = []

    async def on_refresh(kind, server):
        results.append((kind, server))

    handler.on_refresh(on_refresh)
    asyncio.run(handler.handle_resources_list_changed("test-server"))
    assert results == [("resources", "test-server")]


def test_prompts_list_changed():
    handler = MCPNotificationHandler()
    results = []

    async def on_refresh(kind, server):
        results.append((kind, server))

    handler.on_refresh(on_refresh)
    asyncio.run(handler.handle_prompts_list_changed("test-server"))
    assert results == [("prompts", "test-server")]


def test_multiple_callbacks():
    handler = MCPNotificationHandler()
    results_a = []
    results_b = []

    async def callback_a(kind, server):
        results_a.append((kind, server))

    async def callback_b(kind, server):
        results_b.append((kind, server))

    handler.on_refresh(callback_a)
    handler.on_refresh(callback_b)
    asyncio.run(handler.handle_tools_list_changed("srv"))
    assert results_a == [("tools", "srv")]
    assert results_b == [("tools", "srv")]


def test_callback_error_does_not_stop_others():
    handler = MCPNotificationHandler()
    results = []

    async def bad_callback(kind, server):
        raise RuntimeError("boom")

    async def good_callback(kind, server):
        results.append((kind, server))

    handler.on_refresh(bad_callback)
    handler.on_refresh(good_callback)
    asyncio.run(handler.handle_tools_list_changed("srv"))
    assert results == [("tools", "srv")]
