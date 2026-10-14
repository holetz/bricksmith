"""Fresh-process loader: ``python -m bricksmith.mlops.worker`` reads a JSON request on stdin, prints one JSON line.

It resolves nothing: the caller passes an explicit registered version. The artifact is downloaded, its identity
and runtime compatibility are checked here, and only then are the rows scored.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from bricksmith.errors import BricksmithError
from bricksmith.mlops.adapter import get_adapter
from bricksmith.mlops.errors import ModelIncompatibleError
from bricksmith.mlops.meta import check_runtime
from bricksmith.mlops.store import ModelStore, ResolvedModel


def run(request: dict) -> dict:
    """Load the requested version in this process and score the request rows."""
    adapter = get_adapter(request["adapter"])
    store = ModelStore(None, uris=(request["tracking"], request["registry"]))
    resolved = ResolvedModel(request["name"], request["model"], int(request["version"]), "", (), "active")
    with tempfile.TemporaryDirectory(prefix="bricksmith-worker-") as tmp:
        path, meta = store.fetch(resolved, Path(tmp))
        problems = check_runtime(meta.dependencies, adapter.dependencies())
        if problems:
            msg = "not compatible with this runtime: " + "; ".join(problems)
            raise ModelIncompatibleError(msg)
        model = adapter.load(path)
        outputs = adapter.predict(model, meta.features, request["rows"])
    return {"dependencies": adapter.dependencies(), "model": meta.model, "predictions": outputs}


def main() -> int:
    """Run one request; errors are reported as ``{"error": {...}}`` with exit status 1."""
    try:
        reply = run(json.loads(sys.stdin.read()))
    except BricksmithError as error:
        sys.stdout.write(json.dumps({"error": error.to_dict()}) + "\n")
        return 1
    except Exception as error:  # noqa: BLE001 - the parent reports any failure of the fresh process
        sys.stdout.write(
            json.dumps({"error": {"code": "worker_failed", "message": f"{type(error).__name__}: {error}"}}) + "\n"
        )
        return 1
    sys.stdout.write(json.dumps(reply) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
