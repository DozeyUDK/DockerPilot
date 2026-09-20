"""TUI command-tree layout regressions."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Tree

from dockerpilot.cli import tui
from dockerpilot.cli.parser import build_cli_parser


class FakePilot:
    def list_containers(self, **kwargs):
        return []

    def list_images(self, **kwargs):
        return []


async def ready(app, pilot):
    for _ in range(30):
        await pilot.pause()
        if app.query_one("#loading-screen").has_class("hidden"):
            return
    pytest.fail("TUI did not finish initialization")


def test_tree_expanders_are_only_shown_for_nodes_with_children():
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            tree = app.query_one("#command-tree", Tree)

            container = next(
                node for node in tree.root.children if node.data.path == ("container",)
            )
            list_command = next(
                node for node in container.children if node.data.path == ("container", "list")
            )

            assert container.allow_expand
            assert not list_command.allow_expand

            for node in tree.root.children:
                assert node.allow_expand == bool(node.data.children)
                for child in node.children:
                    assert child.allow_expand == bool(child.data.children)

    asyncio.run(scenario())
