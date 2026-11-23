"""The key registry: every configuration key, once, with what an editor needs to show and write it.

One entry per editable leaf (`shared.autonomy.maxWorkers`) and one per structured catalog, which
is read-only (`specs.subjects`). Each entry carries its type, default, category, scope, reader and
its copy in English and pt-BR. `cq config schema` prints it for the OpenCode settings page;
`cq config set` validates against it; `test_config_schema.py` holds it equal to
`plugin-configuration.md`'s table and to the seed.

Scope says which layer may hold the key:

- `project` — the team's contract, in `.agents/quenching.json`; a local value is ignored and
  flagged, and an editor shows the key locked to the project layer;
- `machine` — this checkout on this machine (slot limits, the work host, context budgets): the
  local layer wins over the project;
- `either` — the project wins, and the local layer fills a key the project leaves out.
"""
from __future__ import annotations

from quenching.codex.config import CHOICES as CODEX_CHOICES, DEFAULTS as CODEX
from quenching.config.autonomy import COMPLEXITY, DEFAULTS as AUTONOMY, MERGE
from quenching.config.host import DEFAULTS as WORK_HOST
from quenching.session.commands.context import BUDGETS
from quenching.session.cachebar import COST_STYLES, DEFAULTS as CACHE_BAR, TTL_CHOICES

SCOPES = ("project", "machine", "either")
TYPES = ("string", "integer", "boolean", "enum", "paths", "strings", "prose", "number", "object")

CATEGORIES = (
    ("worktrees", "Worktrees and isolation", "Worktrees e isolamento"),
    ("autonomy", "Autonomy and orchestration", "Autonomia e orquestração"),
    ("specs", "Specs and cards", "Specs e cards"),
    ("git", "Git and PR", "Git e PR"),
    ("codex", "Codex", "Codex"),
    ("proof", "Proof and tests", "Prova e testes"),
    ("ops", "Operations", "Operações"),
    ("knowledge", "Knowledge", "Conhecimento"),
    ("cache", "Prompt cache", "Cache de prompt"),
)

SLOTS = {"min": 4, "max": 12, "waitMinutes": 30, "pollSeconds": 30, "setupIdempotent": False}

_ENTRIES: list[dict] = []


def _k(key: str, type_: str, default, category: str, scope: str, reader: str,
       en: tuple[str, str], pt: tuple[str, str], **extra) -> None:
    _ENTRIES.append({"key": key, "type": type_, "default": default, "category": category,
                     "scope": scope, "reader": reader, "editable": type_ != "object",
                     "copy": {"en": {"title": en[0], "summary": en[1]},
                              "pt-BR": {"title": pt[0], "summary": pt[1]}}, **extra})


# — Worktrees and isolation —
_k("shared.worktreeSlots.min", "integer", SLOTS["min"], "worktrees", "machine",
   "cq git stale / prune",
   ("Warm slots kept", "Free worktree slots cleanup never removes."),
   ("Slots mantidos", "Slots livres que a limpeza nunca remove."), minimum=1)
_k("shared.worktreeSlots.max", "integer", SLOTS["max"], "worktrees", "machine",
   "cq git worktree acquire",
   ("Slot ceiling", "At most this many worktree slots; at the limit, acquire waits or stops."),
   ("Limite de slots", "No máximo este número de slots; no limite, o acquire espera ou para."),
   minimum=1)
_k("shared.worktreeSlots.waitMinutes", "integer", SLOTS["waitMinutes"], "worktrees", "machine",
   "cq git worktree acquire --wait",
   ("Wait for a slot (minutes)", "How long acquire --wait waits before telling the human."),
   ("Espera por slot (minutos)", "Quanto o acquire --wait espera antes de avisar a pessoa."),
   minimum=1)
_k("shared.worktreeSlots.pollSeconds", "integer", SLOTS["pollSeconds"], "worktrees", "machine",
   "cq git worktree acquire --wait",
   ("Poll interval (seconds)", "How often a waiting acquire looks for a free slot."),
   ("Intervalo de consulta (segundos)", "De quanto em quanto tempo o acquire procura um slot."),
   minimum=1)
_k("shared.worktreeSlots.setupIdempotent", "boolean", SLOTS["setupIdempotent"], "worktrees",
   "project", "cq specs doctor",
   ("Setup is idempotent", "The worktree setup is safe to run again over a reused slot."),
   ("Setup é idempotente", "O setup pode rodar de novo sobre um slot reutilizado."))
_k("shared.worktreeSetup", "string", None, "worktrees", "project", "cq git worktree acquire / add",
   ("Worktree setup command", "Shell command run inside a slot after each acquire."),
   ("Comando de setup do worktree", "Comando executado dentro do slot após cada acquire."))
_k("shared.sharedPaths", "paths", [], "worktrees", "project", "cq git worktree link",
   ("Shared paths", "Repo-relative paths linked into every worktree from one store."),
   ("Caminhos compartilhados", "Caminhos ligados em todo worktree a partir de um só lugar."))
_k("shared.regenerablePaths", "paths", [], "worktrees", "project",
   "cq git worktree retire / release, cq git stale",
   ("Regenerable paths", "Ignored paths that may be discarded with a worktree (caches)."),
   ("Caminhos regeneráveis", "Caminhos ignorados que podem ser descartados (caches)."))
_k("shared.workHost.target", "string", WORK_HOST["target"], "worktrees", "machine",
   "cq config host",
   ("Work host", "Where heavy commands run: an ssh host or a Docker context; empty runs here."),
   ("Host de trabalho", "Onde comandos pesados rodam: host ssh ou contexto Docker; vazio é local."))
_k("shared.workHost.dockerContext", "string", WORK_HOST["dockerContext"], "worktrees", "machine",
   "cq config host", ("Docker context", "The Docker context the work host uses."),
   ("Contexto Docker", "O contexto Docker usado pelo host de trabalho."))
_k("shared.workHost.runner", "string", WORK_HOST["runner"], "worktrees", "machine",
   "cq config host", ("Runner", "The command that runs work on the host."),
   ("Runner", "O comando que executa o trabalho no host."))
_k("shared.workHost.remoteRoot", "string", WORK_HOST["remoteRoot"], "worktrees", "machine",
   "cq config host", ("Remote root", "The directory the repository syncs to on the host."),
   ("Raiz remota", "O diretório para onde o repositório é sincronizado no host."))
_k("shared.workHost.syncFlags", "strings", WORK_HOST["syncFlags"], "worktrees", "machine",
   "cq config host", ("Sync flags", "rsync flags for the sync to the host."),
   ("Flags de sincronização", "Flags do rsync na sincronização com o host."))
_k("shared.workHost.syncExclude", "paths", WORK_HOST["syncExclude"], "worktrees", "machine",
   "cq config host", ("Sync exclusions", "Paths never synced to the host."),
   ("Exclusões da sincronização", "Caminhos que nunca vão para o host."))
_k("shared.workHost.pullBack", "paths", WORK_HOST["pullBack"], "worktrees", "machine",
   "cq config host", ("Pull back", "Paths copied back from the host after a run."),
   ("Trazer de volta", "Caminhos copiados do host depois de uma execução."))
_k("shared.workHost.protectedPaths", "paths", WORK_HOST["protectedPaths"], "worktrees",
   "machine", "cq config host", ("Protected paths", "Paths a sync never overwrites."),
   ("Caminhos protegidos", "Caminhos que a sincronização nunca sobrescreve."))
_k("shared.workHost.minFreeGb", "integer", WORK_HOST["minFreeGb"], "worktrees", "machine",
   "cq config host", ("Minimum free disk (GB)", "The host must keep this much disk free."),
   ("Disco livre mínimo (GB)", "O host precisa manter este espaço livre."), minimum=0)

# — Autonomy and orchestration —
_k("shared.autonomy.runAloneUpTo", "enum", AUTONOMY["runAloneUpTo"], "autonomy", "either",
   "/quenching-specs execute, orchestrate",
   ("Run alone up to", "The highest spec complexity built without stopping for a human."),
   ("Rodar sozinho até", "A maior complexidade de spec construída sem parar para uma pessoa."),
   choices=list(COMPLEXITY))
_k("shared.autonomy.merge", "enum", AUTONOMY["merge"], "autonomy", "either",
   "/quenching-specs conclude, orchestrate",
   ("Merge policy", "Which PRs are merged without asking."),
   ("Política de merge", "Quais PRs são mesclados sem perguntar."), choices=list(MERGE))
_k("shared.autonomy.maxWorkers", "integer", AUTONOMY["maxWorkers"], "autonomy", "either",
   "/quenching-specs orchestrate", ("Parallel workers", "Specs built at the same time."),
   ("Workers em paralelo", "Specs construídas ao mesmo tempo."), minimum=1, maximum=8)
_k("shared.autonomy.maxLaunches", "integer", AUTONOMY["maxLaunches"], "autonomy", "either",
   "/quenching-specs orchestrate, autopilot",
   ("Agent launches per run", "Sub-agent launches one run may spend."),
   ("Lançamentos por execução", "Quantos sub-agentes uma execução pode lançar."), minimum=1)
_k("shared.autonomy.livenessMinutes", "integer", AUTONOMY["livenessMinutes"], "autonomy",
   "either", "/quenching-specs orchestrate",
   ("Worker liveness (minutes)", "Silence after which a worker is checked on."),
   ("Vida do worker (minutos)", "Silêncio depois do qual um worker é verificado."), minimum=1)
_k("shared.contextBudgets.main", "integer", BUDGETS["main"], "autonomy", "machine",
   "cq components session context", ("Main session budget", "Tokens the main session plans around."),
   ("Orçamento da sessão principal", "Tokens que a sessão principal considera."), minimum=1)
_k("shared.contextBudgets.worker", "integer", BUDGETS["worker"], "autonomy", "machine",
   "cq components session context", ("Worker budget", "A size estimate for one worker, never a limit."),
   ("Orçamento do worker", "Estimativa de tamanho de um worker, nunca um limite."), minimum=1)
_k("shared.contextBudgets.conductor", "integer", BUDGETS["conductor"], "autonomy", "machine",
   "cq components session context", ("Conductor budget", "Tokens the orchestrating session plans around."),
   ("Orçamento do condutor", "Tokens que a sessão que orquestra considera."), minimum=1)
# — Prompt cache (the quenching-cache bar) —
_k("shared.cache.rebuildLimitUsd", "number", CACHE_BAR["rebuildLimitUsd"], "cache", "either",
   "quenching-cache plugin",
   ("Rebuild limit (USD)", "Above this rebuild cost an expired chat turns red and suggests a handoff."),
   ("Limite de reconstrução (USD)", "Acima deste custo de reconstrução o chat expirado fica vermelho e sugere um handoff."),
   minimum=0)
_k("shared.cache.warnMinutes", "integer", CACHE_BAR["warnMinutes"], "cache", "either",
   "quenching-cache plugin",
   ("Warning time (minutes)", "How early the 1-hour cache turns yellow before it expires."),
   ("Aviso (minutos)", "Quanto antes de expirar o cache de 1 hora fica amarelo."), minimum=1)
_k("shared.cache.ttlMinutes", "enum", CACHE_BAR["ttlMinutes"], "cache", "machine",
   "quenching-cache plugin",
   ("Cache lifetime", "auto follows each reply's cache write; 5 or 60 force it."),
   ("Duração do cache", "auto segue a escrita de cache de cada resposta; 5 ou 60 a forçam."),
   choices=list(TTL_CHOICES))
_k("shared.cache.costStyle", "enum", CACHE_BAR["costStyle"], "cache", "either",
   "quenching-cache plugin",
   ("Show cost as", "dollars at list price, or times more than a normal message."),
   ("Mostrar custo como", "dólares a preço de tabela, ou quantas vezes mais que uma mensagem normal."),
   choices=list(COST_STYLES))
_k("shared.hooks", "object", {}, "autonomy", "project", "the command that owns the event",
   ("Hooks", "Commands run on flow events; edit in the file."),
   ("Hooks", "Comandos executados em eventos do fluxo; edite no arquivo."))
_k("shared.profiles", "object", None, "autonomy", "project", "cq deploy",
   ("Installed profiles", "Which fronts are installed; edit in the file."),
   ("Perfis instalados", "Quais frentes estão instaladas; edite no arquivo."))

# — Specs and cards —
_k("backend", "enum", "git", "specs", "project", "store selection",
   ("Spec store", "Where specs live: the quenching branch, or a deprecated tracker."),
   ("Armazenamento das specs", "Onde as specs ficam: a branch quenching, ou um tracker obsoleto."),
   choices=["git", "github", "azure-boards"])
_k("specs.card", "object", None, "specs", "project", "the card layer",
   ("Card provider", "The tracker card beside each spec; edit in the file."),
   ("Provedor de card", "O card no tracker ao lado de cada spec; edite no arquivo."))
_k("specs.cardAt", "enum", None, "specs", "project", "the card layer",
   ("Card created at", "When the card is created."), ("Card criado em", "Quando o card é criado."),
   choices=["capture"])
_k("specs.artifactLanguage", "string", None, "specs", "project", "cq specs",
   ("Artifact language", "The language spec documents are written in."),
   ("Idioma dos artefatos", "O idioma em que as specs são escritas."))
_k("specs.chat.model", "string", "sonnet", "specs", "either", "the portal's --chat",
   ("Portal chat model", "The model answering in the portal chat."),
   ("Modelo do chat do portal", "O modelo que responde no chat do portal."))
_k("specs.chat.budgetUsd", "number", 0.5, "specs", "either", "the portal's --chat",
   ("Portal chat budget (USD)", "Spend cap for one portal chat."),
   ("Orçamento do chat (USD)", "Teto de gasto de um chat do portal."), minimum=0)
_k("specs.chat.timeoutSeconds", "integer", 300, "specs", "either", "the portal's --chat",
   ("Portal chat timeout (seconds)", "How long one chat answer may take."),
   ("Tempo limite do chat (segundos)", "Quanto uma resposta do chat pode levar."), minimum=1)
_k("specs.estimate.writeOnly", "paths", [], "specs", "project", "cq specs estimate",
   ("Write-only paths", "Generated or tool-edited paths the estimate lists but never counts."),
   ("Caminhos só de escrita", "Caminhos gerados ou editados por ferramenta que a estimativa lista e não conta."))
for _name, _en, _pt in (
        ("azureStates", "Azure states", "Estados do Azure"),
        ("azurePlacement", "Azure placement", "Posicionamento no Azure"),
        ("azureColumns", "Azure columns", "Colunas do Azure"),
        ("subjects", "Subjects", "Assuntos"),
        ("tagCatalog", "Tag catalog", "Catálogo de tags"),
        ("workItemTypes", "Work item types", "Tipos de item de trabalho")):
    _k(f"specs.{_name}", "object", None, "specs", "project", "cq specs",
       (_en, "A structured catalog; edit in the file."),
       (_pt, "Um catálogo estruturado; edite no arquivo."))

# — Git and PR —
for _name, _en, _pt in (
        ("commitSubject", "Commit subject", "Assunto do commit"),
        ("branchName", "Branch name", "Nome da branch"),
        ("prTitle", "PR title", "Título do PR"),
        ("prBody", "PR body", "Corpo do PR"),
        ("mergeSubject", "Merge subject", "Assunto do merge")):
    _k(f"shared.gitConventions.{_name}", "prose", None, "git", "project", "cq git conventions",
       (_en, "A prose directive the git pillar follows, never a template."),
       (_pt, "Uma diretiva em prosa que o pilar git segue, nunca um template."))
_k("shared.gitIdentity.allowedEmails", "strings", None, "git", "project",
   "cq git commit, the identity gate",
   ("Allowed emails", "Who may author or commit here: exact addresses or patterns."),
   ("E-mails permitidos", "Quem pode ser autor ou committer aqui: endereços ou padrões."))
_k("delivery.provider", "string", None, "git", "project", "the delivery front",
   ("Delivery provider", "The CI provider, declared explicitly."),
   ("Provedor de entrega", "O provedor de CI, declarado explicitamente."))

# — Codex —
_k("shared.codex.maxUsedPercent.primary", "integer", CODEX["maxUsedPercent"]["primary"],
   "codex", "machine", "cq codex", ("Primary quota ceiling (%)", "Codex stops above this use."),
   ("Teto da cota primária (%)", "O Codex para acima deste uso."), minimum=0, maximum=100)
_k("shared.codex.maxUsedPercent.secondary", "integer", CODEX["maxUsedPercent"]["secondary"],
   "codex", "machine", "cq codex",
   ("Secondary quota ceiling (%)", "Codex stops above this weekly use."),
   ("Teto da cota secundária (%)", "O Codex para acima deste uso semanal."), minimum=0,
   maximum=100)
_k("shared.codex.timeoutSeconds", "integer", CODEX["timeoutSeconds"], "codex", "either",
   "cq codex", ("Codex timeout (seconds)", "How long one Codex call may run."),
   ("Tempo limite do Codex (segundos)", "Quanto uma chamada ao Codex pode durar."), minimum=1)
_k("shared.codex.executor.enabled", "boolean", CODEX["executor"]["enabled"], "codex", "either",
   "cq codex implement", ("Codex writes tasks", "Codex writes eligible tasks' code at execute."),
   ("Codex escreve tarefas", "O Codex escreve o código das tarefas elegíveis no execute."))
for _name, _en, _pt in (("modelSmall", "Executor model (small)", "Modelo do executor (pequeno)"),
                        ("modelVerified", "Executor model (verified)",
                         "Modelo do executor (verificado)"),
                        ("modelSmallTier", "Executor model (small tier)",
                         "Modelo do executor (faixa pequena)")):
    _k(f"shared.codex.executor.{_name}", "string", CODEX["executor"][_name], "codex", "either",
       "cq codex implement", (_en, "The Codex model for this class of task."),
       (_pt, "O modelo do Codex para esta classe de tarefa."))
_k("shared.codex.executor.reasoningEffort", "enum", CODEX["executor"]["reasoningEffort"],
   "codex", "either", "cq codex implement",
   ("Executor reasoning effort", "How hard Codex thinks when writing a task."),
   ("Esforço de raciocínio", "Quanto o Codex pensa ao escrever uma tarefa."),
   choices=list(CODEX_CHOICES["reasoningEffort"]))
_k("shared.codex.executor.maxTaskTokens", "integer", CODEX["executor"]["maxTaskTokens"], "codex",
   "either", "cq codex eligible", ("Largest task (tokens)", "Bigger tasks stay with the agent."),
   ("Maior tarefa (tokens)", "Tarefas maiores ficam com o agente."), minimum=1)
_k("shared.codex.executor.reservePercent", "integer", CODEX["executor"]["reservePercent"],
   "codex", "machine", "cq codex eligible",
   ("Quota reserve (%)", "Quota kept free below each ceiling."),
   ("Reserva de cota (%)", "Cota mantida livre abaixo de cada teto."), minimum=0, maximum=100)
_k("shared.codex.executor.stopLossPercent", "integer", CODEX["executor"]["stopLossPercent"],
   "codex", "machine", "cq codex implement",
   ("Stop loss (%)", "Quota one task may burn before it is stopped."),
   ("Stop loss (%)", "Cota que uma tarefa pode gastar antes de ser parada."), minimum=0,
   maximum=100)

# — Proof and tests —
_k("proof.proofRoot", "string", "tests", "proof", "project", "cq proof",
   ("Proof root", "Where the tests live."), ("Raiz da prova", "Onde ficam os testes."))
_k("proof.measuredRoots", "paths", [], "proof", "project", "cq proof",
   ("Measured roots", "Source roots the coverage is measured over."),
   ("Raízes medidas", "Raízes de código sobre as quais a cobertura é medida."))
_k("proof.proofExclusions", "paths", [], "proof", "project", "cq proof",
   ("Proof exclusions", "Paths the proof does not measure."),
   ("Exclusões da prova", "Caminhos que a prova não mede."))
_k("proof.ratchetPath", "string", None, "proof", "project", "cq proof",
   ("Ratchet file", "Where the coverage ratchet is stored."),
   ("Arquivo da catraca", "Onde a catraca de cobertura fica."))
_k("proof.layers", "object", None, "proof", "project", "cq proof",
   ("Proof layers", "The declared test layers; edit in the file."),
   ("Camadas de prova", "As camadas de teste declaradas; edite no arquivo."))
_k("proof.cost", "object", None, "proof", "project", "cq proof",
   ("Test cost contract", "The test suite's cost budget; edit in the file."),
   ("Contrato de custo dos testes", "O orçamento de custo da suíte; edite no arquivo."))

# — Operations —
_k("ops.opsRoot", "string", None, "ops", "project", "cq ops",
   ("Ops root", "Where the operational entry points live."),
   ("Raiz de operações", "Onde ficam os pontos de entrada operacionais."))
_k("ops.router", "string", None, "ops", "project", "cq ops",
   ("Ops router", "The router file of the operational commands."),
   ("Roteador de operações", "O arquivo roteador dos comandos operacionais."))
_k("ops.registry", "string", None, "ops", "project", "cq ops registry",
   ("Ops registry", "The registry of operational commands."),
   ("Registro de operações", "O registro dos comandos operacionais."))

_k("shared.mcp", "object", None, "ops", "project", "cq deploy",
   ("MCP registration", "Whether deploy registers the quenching MCP server; edit in the file."),
   ("Registro do MCP", "Se o deploy registra o servidor MCP do quenching; edite no arquivo."))

# — Knowledge —
_k("shared.okfValidate.warnAsError", "boolean", False, "knowledge", "either",
   "cq knowledge validate", ("Warnings fail validation", "Treat knowledge warnings as errors."),
   ("Avisos reprovam", "Tratar avisos do conhecimento como erros."))
_k("shared.okfValidate.ignoreGlobs", "strings", [], "knowledge", "either",
   "cq knowledge validate", ("Ignored globs", "Paths the knowledge validation skips."),
   ("Globs ignorados", "Caminhos que a validação do conhecimento pula."))

REGISTRY: dict[str, dict] = {entry["key"]: entry for entry in _ENTRIES}


def entry(key: str) -> dict | None:
    return REGISTRY.get(key)


def registry_leaves(document: dict) -> list[tuple[str, object]]:
    """`(key, value)` for every leaf of a layer document, cut at registry granularity: a registry
    key is a leaf even when its value is an object (a catalog), and anything the registry does not
    know is a leaf at the deepest path that holds a non-object."""
    out: list[tuple[str, object]] = []

    def walk(prefix: str, value) -> None:
        if prefix in REGISTRY or not isinstance(value, dict) or not value:
            out.append((prefix, value))
            return
        for name, sub in value.items():
            walk(f"{prefix}.{name}" if prefix else name, sub)

    for name, value in document.items():
        walk(name, value)
    return out


def scope_of(key: str) -> str | None:
    """The scope of `key`, or of the catalog that holds it; None for a key the registry lacks."""
    parts = key.split(".")
    for n in range(len(parts), 0, -1):
        found = REGISTRY.get(".".join(parts[:n]))
        if found:
            return found["scope"]
    return None


def unknown_leaves(document: dict) -> list[str]:
    """Dotted leaves inside the known namespaces of a repository document that nothing reads.

    A leaf is read when the registry knows it or a catalog above it, when it sits under one of
    the keys `cq specs` reads from its merged `shared`+`specs` view, or under `specs.store`.
    Top-level keys are left to `unknownKeys`, which the envelope already reports."""
    from quenching.common.config import NAMESPACES
    from quenching.specs.config import BRANCH_CONFIG_KEYS, CONFIG_KEYS   # deferred: specs imports config

    merged = (set(CONFIG_KEYS) - {"backend"}) | set(BRANCH_CONFIG_KEYS)
    out: list[str] = []
    for name in NAMESPACES:
        block = document.get(name)
        if not isinstance(block, dict):
            continue
        for key, _ in registry_leaves({name: block}):
            first = key.split(".")[1] if "." in key else None
            if (scope_of(key) is not None
                    or any(known.startswith(key + ".") for known in REGISTRY)
                    or (name in ("shared", "specs") and first in merged)
                    or (name == "specs" and first == "store")):
                continue
            out.append(key)
    return sorted(out)


def local_decision(key: str, project_declares: bool) -> tuple[bool, str | None]:
    """Whether a local-layer `key` applies, and why not when it does not."""
    scope = scope_of(key)
    if scope is None:
        return False, f"{key} is not a known key"
    if scope == "project":
        return False, f"{key} is a project key; the local layer cannot set it"
    if scope == "either" and project_declares:
        return False, None
    return True, None


def path_of(key: str) -> tuple[str, ...]:
    """`shared.autonomy.maxWorkers` → `("shared", "autonomy", "maxWorkers")`; `backend` stays
    top-level."""
    return tuple(key.split("."))


def validate(key: str, value) -> tuple[object, str | None]:
    """`(value, None)` when `value` fits `key`'s declared type, else `(None, reason)`."""
    spec = REGISTRY.get(key)
    if spec is None:
        return None, f"unknown key: {key}"
    if not spec["editable"]:
        return None, f"{key} is a structured catalog; edit it in the file"
    kind = spec["type"]
    if kind in ("integer", "number"):
        ok = isinstance(value, int) if kind == "integer" else isinstance(value, (int, float))
        if not ok or isinstance(value, bool):
            return None, f"{key} takes {'an integer' if kind == 'integer' else 'a number'}"
        if "minimum" in spec and value < spec["minimum"]:
            return None, f"{key} is at least {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return None, f"{key} is at most {spec['maximum']}"
    elif kind == "boolean" and not isinstance(value, bool):
        return None, f"{key} takes true or false"
    elif kind == "enum" and value not in spec["choices"]:
        return None, f"{key} is one of: {', '.join(spec['choices'])}"
    elif kind in ("string", "prose") and not (isinstance(value, str) and value.strip()):
        return None, f"{key} takes a non-empty string"
    elif kind in ("paths", "strings") and not (
            isinstance(value, list) and all(isinstance(i, str) and i for i in value)):
        return None, f"{key} takes a list of non-empty strings"
    return value, None


def schema() -> dict:
    """The registry as `cq config schema --json` prints it."""
    return {"scopes": list(SCOPES),
            "categories": [{"id": cid, "title": {"en": en, "pt-BR": pt}}
                           for cid, en, pt in CATEGORIES],
            "keys": [dict(e) for e in _ENTRIES]}
