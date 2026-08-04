"""Hermes plugin registration for Hermes Todo."""

from __future__ import annotations

from .cli import register_cli, todo_command


def register(ctx) -> None:
    ctx.register_cli_command(
        name="todo",
        help="Read and update the shared Hermes Todo board",
        setup_fn=register_cli,
        handler_fn=todo_command,
        description="Agent-writable CLI for the shared Hermes Todo task board.",
    )
