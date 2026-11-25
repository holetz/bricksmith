"""The `cq design` pillar: source, projections, interoperability, and rendering."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from quenching.common.output import (CQArgumentParser, OK, emit, emit_err, exit_for,
                                     finding_code, report_findings)
from quenching.common.version import VERSION
from quenching.design.align import align_plan, align_write, read_product
from quenching.design.build import build_drift, compute_build, write_build
from quenching.design.doctor import inspect_design, status_payload
from quenching.design.genre import new_genre, render_genre
from quenching.design.importer import import_design
from quenching.design.model import DesignError


def build_parser() -> argparse.ArgumentParser:
    parser = CQArgumentParser(
        prog="cq design",
        description="one DTCG source projected into portable design and editorial artifacts",
    )
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--version", action="store_true", help="print the design pillar version")
    sub = parser.add_subparsers(dest="cmd")

    align = sub.add_parser("align", help="plan or install the design front")
    mode = align.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report the install plan (default)")
    mode.add_argument("--write", action="store_true", help="install missing files and build projections")
    align.add_argument("--product-json", type=Path,
                       help="confirmed product facts keyed by PRODUCT.md headings")
    align.add_argument(
        "--design-winner", choices=("import", "build"),
        help="required for an external DESIGN.md: import its portable values or let DTCG win",
    )
    align.add_argument(
        "--product-reviewed", action="store_true",
        help="confirm an external PRODUCT.md has already been folded into the OKF source",
    )
    align.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="read the installed front and its current findings")
    status.add_argument("--json", action="store_true")

    build = sub.add_parser("build", help="emit every deterministic projection")
    build.add_argument("--check", action="store_true", help="check byte identity without writing")
    build.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="check source, generated identity, assets, and non-web drift")
    doctor.add_argument("--json", action="store_true")

    importer = sub.add_parser("import", help="fold an Impeccable-authored DESIGN.md into tokens.json")
    importer.add_argument("--check", action="store_true", help="show the fold without writing")
    importer.add_argument("--json", action="store_true")

    genre = sub.add_parser("genre", help="manage editorial genre contracts")
    genre.add_argument("--json", action="store_true", dest="genre_json",
                       help="print machine-readable output (also accepted before the child verb)")
    genre_sub = genre.add_subparsers(dest="genre_cmd")
    new = genre_sub.add_parser("new", help="mint one genre and its medium templates")
    new.add_argument("slug")
    new.add_argument("--name", required=True)
    new.add_argument("--register", required=True)
    new.add_argument("--media", action="append", required=True,
                     help="html, typst, or pdf; repeat for more than one")
    new.add_argument("--field", action="append", required=True,
                     help="name:required|optional[:description]; repeat for each field")
    new.add_argument("--engine", default="builtin",
                     help="builtin or the external command that receives the render request on stdin")
    new.add_argument("--check", action="store_true")
    new.add_argument("--json", action="store_true")

    render = sub.add_parser("render", help="render one genre into HTML, Typst, or PDF")
    render.add_argument("genre")
    render.add_argument("--medium", required=True, choices=("html", "typst", "pdf"))
    render.add_argument("--data", required=True, type=Path)
    render.add_argument("--output", type=Path)
    render.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"cq design {VERSION}")
        return OK
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return 2
    root = Path(args.root)
    as_json = bool(getattr(args, "json", False) or getattr(args, "genre_json", False))
    try:
        if args.cmd == "align":
            product = read_product(args.product_json)
            if args.write:
                payload = align_write(
                    root, product, design_winner=args.design_winner,
                    product_reviewed=args.product_reviewed,
                )
                return _payload(as_json, payload, "design align — installed")
            payload = align_plan(
                root, product, design_winner=args.design_winner,
                product_reviewed=args.product_reviewed,
            )
            findings = [{"severity": "error", "code": finding_code("design", "align-blocker"),
                         "path": ".design",
                         "message": message} for message in payload["blockers"]]
            if as_json:
                print(json.dumps({"ok": not findings, **payload, "findings": findings}, indent=2,
                                 ensure_ascii=False))
            else:
                print(f"design align — check ({len(payload['missing'])} missing, {len(findings)} blocker(s))")
                for relative in payload["missing"]:
                    print(f"  [ADD  ] {relative}")
                for finding in findings:
                    print(f"  [ERROR] {finding['message']}")
            return exit_for(findings)
        if args.cmd == "status":
            payload = status_payload(root)
            findings = payload.pop("findings")
            return report_findings(as_json, "design status", payload, findings, label_key="path")
        if args.cmd == "build":
            result = compute_build(root)
            if args.check:
                findings = build_drift(result)
                return report_findings(as_json, "design build — check",
                                       {"root": str(result.root), "sources": list(result.sources)},
                                       findings, label_key="path")
            written = write_build(result)
            return _payload(as_json, {"ok": True, "mode": "write", "root": str(result.root),
                                      "written": written, "sources": list(result.sources)},
                            f"design build — {len(written)} projection(s) written")
        if args.cmd == "doctor":
            payload, findings = inspect_design(root)
            return report_findings(as_json, "design doctor", payload, findings, label_key="path")
        if args.cmd == "import":
            payload = import_design(root, write=not args.check)
            if args.check:
                findings = [
                    {"severity": "error", "code": finding_code("design", "import-diff"),
                     "path": path, "message": "portable value differs from tokens.json; run import without --check"}
                    for path in payload["changed"]
                ]
                findings.extend(
                    {"severity": "warning", "code": finding_code("design", "import-skipped"),
                     "path": path, "message": "richer Impeccable value has no honest DTCG projection and was retained"}
                    for path in payload.get("skipped", [])
                )
                return report_findings(
                    as_json, f"design import — {len(payload['changed'])} portable value(s) differ",
                    payload, findings, label_key="path",
                )
            _payload(as_json, payload,
                     f"design import — {len(payload['changed'])} portable value(s) imported")
            return OK
        if args.cmd == "genre":
            if args.genre_cmd != "new":
                parser.error("cq design genre requires `new`")
            payload = new_genre(root, args.slug, args.name, args.register, args.media, args.field,
                                write=not args.check, engine=args.engine)
            return _payload(as_json, payload, f"design genre — {args.slug}")
        if args.cmd == "render":
            payload = render_genre(root, args.genre, args.medium, args.data, args.output)
            return _payload(as_json, payload, f"design render — {payload['output']}")
    except DesignError as exc:
        return emit_err(as_json, {"code": finding_code("design", "refusal"),
                                  "message": str(exc)})
    parser.error(f"unknown design command: {args.cmd}")
    return 2


def _payload(as_json: bool, payload: dict[str, Any], human: str) -> int:
    emit(as_json, payload, human)
    return OK
