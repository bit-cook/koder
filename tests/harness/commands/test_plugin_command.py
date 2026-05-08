import json

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.plugins.lifecycle import PluginLifecycleService


async def _run_plugin(handler: HarnessInteractiveCommandHandler) -> str:
    return await handler.handle_slash_input("/plugin", scheduler=None)


def test_plugin_command_lists_installed_plugins(tmp_path):
    lifecycle = PluginLifecycleService(tmp_path / "plugins")
    plugin_dir = tmp_path / "demo-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin", "version": "1.0.0"}),
        encoding="utf-8",
    )
    lifecycle.install_from_dir(plugin_dir)
    handler = HarnessInteractiveCommandHandler(plugin_root=lifecycle.root)
    import asyncio

    result = asyncio.run(_run_plugin(handler))
    assert "demo-plugin" in result
