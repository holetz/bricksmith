"""Install the built wheel and sdist into clean environments and import every bricksmith module.

Usage: ``python scripts/verify_dist.py [--dist DIR]``. Without ``--dist`` the distributions are built
into a temporary directory. Each artifact is installed alone, so heavy integrations (Databricks SDK,
Connect, bundles, MLflow, scikit-learn) are absent; every module must still import.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAVY = ("databricks", "pyspark", "mlflow", "sklearn")
PROBE = """
import importlib, importlib.util, pkgutil, sys
import bricksmith
present = [m for m in {heavy!r} if importlib.util.find_spec(m) is not None]
if present:
    sys.exit("heavy integrations unexpectedly installed: " + ", ".join(present))
names = ["bricksmith"] + [m.name for m in pkgutil.walk_packages(bricksmith.__path__, "bricksmith.")]
for name in names:
    importlib.import_module(name)
loaded = [m for m in {heavy!r} if m in sys.modules]
if loaded:
    sys.exit("importing bricksmith loaded heavy modules: " + ", ".join(loaded))
for capability in ("dab", "registry", "mlops", "monitor"):
    importlib.import_module("bricksmith." + capability)
print(len(names), "modules imported")
"""


def run(*command: str | Path) -> None:
    """Run a command and fail loudly with its output."""
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit(f"command failed: {' '.join(str(c) for c in command[:3])}")
    sys.stdout.write(result.stdout)


def venv_executable(env: Path, name: str) -> Path:
    """Return the path of ``name`` inside the virtual environment ``env`` on any platform."""
    if sys.platform == "win32":
        return env / "Scripts" / f"{name}.exe"
    return env / "bin" / name


def verify(artifact: Path, workdir: Path) -> None:
    """Install ``artifact`` into a fresh environment and run the import probe with isolated mode."""
    env = workdir / f"venv-{artifact.suffix.lstrip('.') or 'sdist'}"
    run("uv", "venv", "--quiet", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", env)
    python = venv_executable(env, "python")
    run("uv", "pip", "install", "--quiet", "--python", python, artifact)
    sys.stdout.write(f"{artifact.name}: ")
    run(python, "-I", "-c", PROBE.format(heavy=HEAVY))
    run(venv_executable(env, "bricksmith"), "context", "show")


def main() -> int:
    """Build (unless ``--dist`` is given) and verify the wheel and the source archive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, help="directory that already holds one wheel and one sdist")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dist = args.dist or work / "dist"
        if args.dist is None:
            run("uv", "build", "--quiet", "--out-dir", dist, ROOT)
        wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            sys.exit(f"expected one wheel and one sdist in {dist}, found {len(wheels)} and {len(sdists)}")
        for artifact in (*wheels, *sdists):
            verify(artifact, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
