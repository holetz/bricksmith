"""The ``features`` command group: ``install-kernel`` puts the JVM kernel in a volume and creates its UC function.

:func:`install_kernel` is the library twin, callable from a bundle task (``install_kernel(Clients(ctx.execution),
...)``) or from a notebook. Both are idempotent: the JAR is uploaded once per version and content hash, and the
function is created or replaced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK

if TYPE_CHECKING:
    import argparse

    from bricksmith.clients import Clients


def install_kernel(clients: Clients, volume: str, function: str) -> dict[str, str]:
    """Upload the packaged kernel JAR to ``volume`` and create or replace the Unity Catalog ``function``.

    Return the function name and the JAR's volume path.
    """
    from bricksmith.features.kernel import install_uc_function  # noqa: PLC0415 - only the installer needs it
    from bricksmith.io.volumes import files_api_client  # noqa: PLC0415

    jar = install_uc_function(clients.spark(), files_api_client(clients), volume, function)
    return {"function": function, "jar": jar}


def _install(args: argparse.Namespace) -> int:
    from bricksmith.clients import Clients  # noqa: PLC0415 - heavy clients load only for this command

    installed: dict[str, object] = {**install_kernel(Clients(context_from_args(args)), args.volume, args.function)}
    emit(args, installed, f"kernel function {installed['function']} uses {installed['jar']}")
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``features install-kernel``."""
    group = subparsers.add_parser("features", help="windowed feature engines", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    install = commands.add_parser(
        "install-kernel",
        help="upload the JVM kernel JAR to a volume and create or replace its Unity Catalog function",
        parents=[global_parent()],
    )
    install.add_argument("--volume", required=True, help="catalog.schema.volume that holds the JAR")
    install.add_argument("--function", required=True, help="catalog.schema.name of the kernel function")
    install.set_defaults(handler=_install)
