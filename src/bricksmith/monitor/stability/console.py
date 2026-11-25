"""The local stability console: a JSON API and one page over the workspace operations, standard library only.

The console owns no query. Every route calls one function of :class:`bricksmith.monitor.stability.ops.Operations`, the
same layer as the ``stability`` verbs, so what is tested is the verbs and the request guards, not the pixels.

Guards: a request is served only when its ``Host`` is one of the allowed hosts (DNS-rebinding defence), and a change
is accepted only as a ``POST`` with ``Content-Type: application/json`` and, when the browser sends one, an ``Origin``
on an allowed host (a cross-site form cannot send either). Errors of the operations are returned as ``400`` with their
message; anything else is a ``500`` with no detail.
"""

# ruff: noqa: E501 - the embedded page is compact JavaScript

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from bricksmith.errors import BricksmithError
from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bricksmith.monitor.stability.ops import Operations

TOKEN_HEADER = "x-forwarded-access-token"  # noqa: S105 - the header's name, not a secret
EMAIL_HEADER = "x-forwarded-email"
_MAX_BODY = 64 * 1024
type Reply = tuple[int, str, bytes]
type Headers = Mapping[str, str]

_READS = ("alerts", "marks", "actions", "runs")
_REQUIRED = {
    "/api/mark": ("dataset", "period", "reason"),
    "/api/unmark": ("dataset", "period", "reason"),
    "/api/action": ("dataset", "alert_id", "action", "reason"),
    "/api/reprocess": ("periods",),
}


def _json(status: int, payload: object) -> Reply:
    return status, "application/json", json.dumps(payload, default=str).encode()


class Console:
    """Route requests to the operations; ``ops_for(token)`` builds them (per user token under on-behalf-of)."""

    def __init__(
        self,
        ops_for: Callable[[str | None], Operations],
        *,
        allowed_hosts: tuple[str, ...],
        job_id: str | None = None,
        on_behalf_of: bool = False,
    ) -> None:
        """Bind the operations factory, the hosts the console answers to and the reprocess job."""
        self._ops_for = ops_for
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        self.job_id = job_id
        self.on_behalf_of = on_behalf_of

    def handle(self, method: str, target: str, headers: Headers, body: bytes = b"") -> Reply:
        """Serve one request and return ``(status, content type, body)``."""
        lowered = {k.lower(): v for k, v in headers.items()}
        parts = urlsplit(target)
        try:
            self._guard(method, lowered)
            if method == "GET" and parts.path == "/":
                return 200, "text/html; charset=utf-8", PAGE.encode()
            ops = self._operations(lowered)
            if method == "GET":
                return _json(200, self._read(ops, parts.path, parse_qs(parts.query)))
            return _json(200, self._change(ops, parts.path, body, lowered))
        except _Refused as refused:
            return _json(refused.status, {"error": refused.message})
        except (MonitorError, BricksmithError) as error:
            return _json(400, {"error": error.message})
        except Exception:  # noqa: BLE001 - nothing of an unexpected failure is shown to the browser
            return _json(500, {"error": "internal error"})

    def _guard(self, method: str, headers: Headers) -> None:
        if headers.get("host", "").lower() not in self.allowed_hosts:
            raise _Refused(403, "host not allowed")
        if method not in ("GET", "POST"):
            raise _Refused(405, "method not allowed")
        if method == "POST":
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                raise _Refused(415, "a change must be sent as application/json")
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() not in self.allowed_hosts:
                raise _Refused(403, "origin not allowed")

    def _operations(self, headers: Headers) -> Operations:
        token = headers.get(TOKEN_HEADER) if self.on_behalf_of else None
        if self.on_behalf_of and not token:
            raise _Refused(401, f"missing {TOKEN_HEADER}: open the app through the workspace")
        return self._ops_for(token)

    @staticmethod
    def _read(ops: Operations, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        one = {k: v[0] for k, v in query.items() if v}
        name = path.removeprefix("/api/")
        if not path.startswith("/api/") or name not in ("datasets", "series", *_READS):
            raise _Refused(404, "not found")
        if name == "datasets":
            return {name: ops.status()}
        if name == "series":
            filters = {k: one[k] for k in ("column", "metric", "segment") if k in one}
            return {name: ops.series(one.get("dataset", ""), **filters)}
        fn = getattr(ops, name)
        return {name: fn(one["dataset"]) if "dataset" in one else fn()}

    def _change(self, ops: Operations, path: str, body: bytes, headers: Headers) -> dict[str, Any]:
        if path not in _REQUIRED:
            raise _Refused(404, "not found")
        data = _fields(body)
        data.setdefault("author", headers.get(EMAIL_HEADER, ""))
        missing = [k for k in (*_REQUIRED[path], *(("mark",) if path == "/api/mark" else ())) if not data.get(k)]
        if missing:
            raise _Refused(400, f"missing: {', '.join(missing)}")
        if path in ("/api/mark", "/api/unmark"):
            keys = ("dataset", "period", "reason", "author", "segment", "column", "metric")
            extra = ("mark", "valid_until") if path == "/api/mark" else ()
            fields = {k: data[k] for k in (*keys, *extra) if k in data}
            return {"mark": ops.mark(unmark=path == "/api/unmark", **fields)}
        if path == "/api/action":
            keys = ("dataset", "alert_id", "action", "reason", "author")
            extra = {k: data[k] for k in ("resolution", "valid_until") if k in data}
            return {"action": ops.act(**{k: data.get(k, "") for k in keys}, **extra)}
        job = data.get("job_id") or self.job_id
        if not job:
            raise _Refused(400, "no reprocess job is configured (start the console with --job)")
        return {"reprocess": ops.reprocess(job, data["periods"])}


class _Refused(Exception):  # noqa: N818 - a control-flow signal carrying an HTTP status
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _fields(body: bytes) -> dict[str, str]:
    if len(body) > _MAX_BODY:
        raise _Refused(413, "request too large")
    try:
        data: Any = json.loads(body or b"{}")
    except ValueError:
        raise _Refused(400, "the body must be a JSON object") from None
    if not isinstance(data, dict):
        raise _Refused(400, "the body must be a JSON object")
    return {k: v for k, v in data.items() if isinstance(v, str)}


def make_server(console: Console, host: str, port: int) -> ThreadingHTTPServer:
    """Return a server (not started) that answers with ``console``; port ``0`` picks a free one."""

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            size = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(size, _MAX_BODY + 1)) if size else b""
            status, kind, payload = console.handle(self.command, self.path, dict(self.headers.items()), body)
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"
            )
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _serve  # noqa: N815 - http.server's method names

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base signature
            """Keep the terminal quiet."""

    return ThreadingHTTPServer((host, port), Handler)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Stability console</title>
<style>
body{font:14px system-ui,sans-serif;margin:1.5rem;color:#222}table{border-collapse:collapse;margin:.5rem 0}
td,th{border:1px solid #ccc;padding:.25rem .5rem;text-align:left}tr.pick{cursor:pointer}tr.pick:hover{background:#eef}
.red{color:#b00020}.yellow{color:#9a6700}.green{color:#1a7f37}section{margin-bottom:1.5rem}
input,select,button{font:inherit;margin:.15rem}#err{color:#b00020}svg{border:1px solid #ccc;background:#fafafa}
</style></head><body>
<h1>Stability console</h1><div id="err"></div>
<section><h2>Datasets</h2><table id="datasets"></table></section>
<section id="detail" hidden><h2 id="title"></h2>
<label>column <select id="column"></select></label><label>metric <select id="metric"></select></label>
<svg id="chart" width="720" height="240"></svg>
<h3>Marks in force</h3><table id="marks"></table>
<h3>Alert queue</h3><table id="alerts"></table>
<h3>Run history</h3><table id="runs"></table>
<h3>Mark a period</h3>
<form id="markform"><input name="period" placeholder="period" required>
<select name="mark"><option>accepted</option><option>excluded</option><option>regime_change</option><option>invalid</option>
<option value="unmark">unmark</option></select>
<input name="reason" placeholder="reason" required><input name="author" placeholder="author"><button>Save</button></form>
<h3>Act on an alert</h3>
<form id="actform"><input name="alert_id" placeholder="alert id" required>
<select name="action"><option>analysis</option><option>closed</option><option>risk_accepted</option></select>
<input name="resolution" placeholder="resolution (closed)"><input name="valid_until" placeholder="until YYYY-MM-DD">
<input name="reason" placeholder="reason" required><input name="author" placeholder="author"><button>Save</button></form>
<h3>Reprocess</h3>
<form id="redoform"><input name="periods" placeholder="2026-01,2026-02..2026-03" required><button>Run job</button></form>
</section>
<script>
const $=id=>document.getElementById(id);let dataset="";let points=[];
const err=m=>{$("err").textContent=m||""};
async function api(path,body){const r=await fetch(path,body?{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function fill(t,cols,rows,pick){t.replaceChildren();const h=t.insertRow();cols.forEach(c=>{const th=document.createElement("th");
th.textContent=c;h.appendChild(th)});rows.forEach(r=>{const tr=t.insertRow();if(pick){tr.className="pick";tr.onclick=()=>pick(r)}
cols.forEach(c=>{const td=tr.insertCell();td.textContent=r[c]??"";if(c==="status"||c==="light")td.className=r[c]||""})})}
async function load(){try{err();const d=(await api("/api/datasets")).datasets;
fill($("datasets"),["dataset","status","latest_period","open_alerts","last_run_status","last_run_at"],d,r=>open(r.dataset))}catch(e){err(e.message)}}
async function open(name){dataset=name;$("detail").hidden=false;$("title").textContent=name;await refresh(true)}
function opts(sel,vals){const keep=sel.value;sel.replaceChildren();vals.forEach(v=>{const o=document.createElement("option");o.textContent=v;sel.appendChild(o)});
if(vals.includes(keep))sel.value=keep}
async function refresh(all){try{err();const q="?dataset="+encodeURIComponent(dataset);
const s=(await api("/api/series"+q)).series;opts($("column"),[...new Set(s.map(p=>p.column))]);opts($("metric"),[...new Set(s.map(p=>p.metric))]);
points=s;draw();
fill($("marks"),["period","mark","reason","author","valid_until"],(await api("/api/marks"+q)).marks);
fill($("alerts"),["alert_id","severity","effective_state","action","message"],(await api("/api/alerts"+q)).alerts);
fill($("runs"),["started_at","status","gate","latest_period","periods_reprocessed","message"],(await api("/api/runs"+q)).runs)}catch(e){err(e.message)}}
function draw(){const svg=$("chart");svg.replaceChildren();const c=$("column").value,m=$("metric").value;
const p=points.filter(x=>x.column===c&&x.metric===m&&!x.segment);
const ys=p.flatMap(x=>[x.value,x.trend_lower,x.trend_upper]).map(Number).filter(Number.isFinite);if(!p.length||!ys.length)return;
const lo=Math.min(...ys),hi=Math.max(...ys),sp=(hi-lo)||1,X=i=>30+i*(660/Math.max(p.length-1,1)),Y=v=>220-(v-lo)/sp*200;
const ns="http://www.w3.org/2000/svg",el=(n,a)=>{const e=document.createElementNS(ns,n);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));svg.appendChild(e);return e};
const band=p.map((x,i)=>[x,i]).filter(([x])=>x.trend_lower!==null&&x.trend_lower!==""&&x.trend_upper!==null&&x.trend_upper!=="");
if(band.length){el("polygon",{points:[...band.map(([x,i])=>X(i)+","+Y(x.trend_upper)),...band.reverse().map(([x,i])=>X(i)+","+Y(x.trend_lower))].join(" "),fill:"#cfe0ff"})}
el("polyline",{points:p.map((x,i)=>X(i)+","+Y(Number(x.value))).join(" "),fill:"none",stroke:"#333"});
p.forEach((x,i)=>{const col={red:"#b00020",yellow:"#9a6700",green:"#1a7f37"}[x.light||x.status]||"#666";
const dot=el("circle",{cx:X(i),cy:Y(Number(x.value)),r:x.mark?6:3,fill:col,stroke:x.mark?"#000":"none"});
const t=document.createElementNS(ns,"title");t.textContent=x.period+" "+x.value+(x.mark?" ["+x.mark+"]":"");dot.appendChild(t)})}
$("column").onchange=$("metric").onchange=draw;
function form(id,send){$(id).onsubmit=async e=>{e.preventDefault();const b=Object.fromEntries([...new FormData(e.target)].filter(([,v])=>v));
try{err();await send(b);e.target.reset();await refresh()}catch(x){err(x.message)}}}
form("markform",b=>{b.dataset=dataset;const un=b.mark==="unmark";return api(un?"/api/unmark":"/api/mark",b)});
form("actform",b=>{b.dataset=dataset;return api("/api/action",b)});
form("redoform",b=>api("/api/reprocess",b));
load();
</script></body></html>
"""

__all__ = ["Console", "make_server"]
