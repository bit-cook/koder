from pathlib import Path
from types import SimpleNamespace

import pytest

from koder_agent.core.at_mentions import _resolve_mcp_resources, async_process_at_mentions
from koder_agent.mcp import discover_mcp_resources


class _ResourceSession:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def read_resource(self, uri):
        self.calls.append(str(uri))
        return SimpleNamespace(contents=[SimpleNamespace(text=self.label)])


def _server(name: str, label: str):
    return SimpleNamespace(name=name, session=_ResourceSession(label))


@pytest.mark.asyncio
async def test_resource_resolution_rejects_duplicate_exact_server_names():
    first = _server("shared", "first")
    second = _server("shared", "second")

    sections = await _resolve_mcp_resources(
        [("shared", "demo://plugin")],
        [first, second],
    )

    assert sections == ["[MCP server not found: shared]"]
    assert first.session.calls == []
    assert second.session.calls == []


@pytest.mark.asyncio
async def test_resource_resolution_uses_unique_exact_raw_name_only():
    normalized_match = _server("alpha.beta", "wrong")
    exact_match = _server("alpha_beta", "right")

    sections = await _resolve_mcp_resources(
        [("alpha_beta", "demo://resource")],
        [normalized_match, exact_match],
    )

    assert sections == ['<resource server="alpha_beta" uri="demo://resource">\nright\n</resource>']
    assert normalized_match.session.calls == []
    assert exact_match.session.calls == ["demo://resource"]


@pytest.mark.asyncio
async def test_resource_resolution_remains_isolated_between_owners():
    first_owner = [_server("shared", "first-owner")]
    second_owner = [_server("shared", "second-owner")]

    first_sections = await _resolve_mcp_resources(
        [("shared", "demo://resource")],
        first_owner,
    )
    second_sections = await _resolve_mcp_resources(
        [("shared", "demo://resource")],
        second_owner,
    )

    assert "first-owner" in first_sections[0]
    assert "second-owner" in second_sections[0]
    assert first_owner[0].session.calls == ["demo://resource"]
    assert second_owner[0].session.calls == ["demo://resource"]


@pytest.mark.asyncio
async def test_dotted_server_resource_discovery_mention_and_read_round_trip(tmp_path: Path):
    class DiscoverableResourceSession(_ResourceSession):
        async def list_resources(self):
            resource = SimpleNamespace(
                uri="demo://resource",
                description="demo",
                name="demo",
            )
            return SimpleNamespace(resources=[resource])

    server = SimpleNamespace(
        name="alpha.beta",
        session=DiscoverableResourceSession("payload"),
    )

    advertised = await discover_mcp_resources([server])
    mention = f"@{advertised[0][0]}"
    resolved = await async_process_at_mentions(mention, tmp_path, mcp_servers=[server])

    assert advertised == [("alpha.beta:demo://resource", "demo")]
    assert '<resource server="alpha.beta" uri="demo://resource">\npayload\n</resource>' in resolved
    assert server.session.calls == ["demo://resource"]
