"""Spawn de ferramentas externas sem shell, com os desvios que cada plataforma exige.

No Windows o `az` é `az.cmd`; passar um `.cmd` a um `subprocess` com lista faz o Python pôr o
`cmd.exe` no meio, e um título com `&`, `%` ou aspas é reinterpretado pelo shell. O CLI do Azure
traz o próprio Python: resolvemos o `python.exe` ao lado do `az.cmd` e chamamos
`python.exe -IBm azure.cli`, que recebe o argv intacto. Fora do Windows, e quando o lançador não
existe, o argv volta como veio — `FileNotFoundError`/`AZ_MISSING` segue sendo do chamador.
"""

from __future__ import annotations

import ntpath
import os
import shutil

AZ_MODULE = "azure.cli"


def _windows_az(argv: list[str]) -> list[str]:
    """Resolve o `az.cmd` até o `python.exe` que o lançador usaria, ou devolve o argv intacto."""
    launcher = shutil.which("az")
    if not launcher:
        return list(argv)
    root = ntpath.dirname(ntpath.dirname(launcher))
    python = ntpath.join(root, "python.exe")
    if not os.path.isfile(python):
        return list(argv)
    return [python, "-IBm", AZ_MODULE, *argv[1:]]


def spawn_argv(argv: list[str]) -> list[str]:
    """Devolve o argv que roda a ferramenta sem passar pelo `cmd.exe` no Windows.

    Args:
        argv: O comando e seus argumentos, com o nome da ferramenta em ``argv[0]``.

    Returns:
        No Windows, o argv do `az` trocado pelo Python do próprio CLI do Azure quando ele
        existe; em qualquer outra plataforma, ou quando o lançador falta, uma cópia do argv.
    """
    if os.name != "nt" or not argv or argv[0] != "az":
        return list(argv)
    return _windows_az(argv)
