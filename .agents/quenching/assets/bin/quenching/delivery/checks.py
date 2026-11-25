"""Static C3 findings over the delivery workflow inventory."""
from __future__ import annotations

import re

from quenching.common.config import CONFIG_FILE
from quenching.common.front import register_checks
from quenching.delivery.model import DeliveryInventory, Finding, Workflow


_BANDS = {
    "delivery-trigger-unreachable": "Structural",
    "delivery-stage-unreachable": "Structural",
    "delivery-checkout-missing": "Structural",
    "delivery-setup-missing": "Structural",
    "delivery-runtime-drift": "Structural",
    "delivery-release-unreachable": "Structural",
    "delivery-provider-policy": "Judgement",
    "delivery-environment-policy": "Judgement",
    "delivery-publish-scope": "Judgement",
    "delivery-permission-policy": "Judgement",
}


def _finding(code: str, message: str, path: str, *, severity: str | None = None) -> Finding:
    band = _BANDS[code]
    return Finding(code, severity or ("warning" if band == "Judgement" else "error"), message,
                   path=path, band=band)


_RUNTIME_COMMANDS = frozenset({
    "python", "python3", "pip", "pip3", "uv", "uvx", "pytest", "poetry", "ruff", "mypy",
    "node", "npm", "npx", "yarn", "pnpm", "bun", "ruby", "bundle", "gem", "java", "mvn",
    "gradle", "go", "cargo", "rustc", "dotnet",
})


def _needs_runtime(commands: tuple[str, ...]) -> bool:
    """True when a command's first word is a language runtime or its package tool."""
    for command in commands:
        for part in re.split(r"&&|\|\||;|\|", command):
            words = [word for word in part.split() if not re.match(r"^\w+=", word)]
            if words and words[0].rsplit("/", 1)[-1] in _RUNTIME_COMMANDS:
                return True
    return False


def _reachable(workflow: Workflow) -> set[str]:
    jobs = {job.name: job for job in workflow.job_details}
    reachable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for job in jobs.values():
            if not job.active or job.name in reachable:
                continue
            if all(need in reachable for need in job.needs):
                reachable.add(job.name)
                changed = True
    return reachable


def _workflow_findings(workflow: Workflow) -> list[Finding]:
    findings: list[Finding] = []
    jobs = {job.name: job for job in workflow.job_details}
    reachable = _reachable(workflow)

    if not workflow.triggers or not workflow.jobs:
        findings.append(_finding(
            "delivery-trigger-unreachable",
            "workflow does not expose a trigger-to-job path",
            workflow.path,
        ))

    for job in workflow.job_details:
        missing = sorted(set(job.needs) - set(jobs))
        if missing or (job.active and job.needs and job.name not in reachable):
            dependency = ", ".join(missing or job.needs)
            findings.append(_finding(
                "delivery-stage-unreachable",
                f"job `{job.name}` cannot reach its declared predecessor(s): {dependency}",
                workflow.path,
            ))
        if not job.active and job.release:
            findings.append(_finding(
                "delivery-release-unreachable",
                f"release job `{job.name}` is disabled by its condition",
                workflow.path,
            ))
        if job.active and job.commands and not job.checkout:
            findings.append(_finding(
                "delivery-checkout-missing",
                f"job `{job.name}` runs commands without checkout provenance",
                workflow.path,
            ))
        if job.active and _needs_runtime(job.commands) and not job.setup:
            findings.append(_finding(
                "delivery-setup-missing",
                f"job `{job.name}` runs commands without setup provenance",
                workflow.path,
            ))

    for job in workflow.job_details:
        if job.release and job.name not in reachable:
            findings.append(_finding(
                "delivery-release-unreachable",
                f"release job `{job.name}` is not reachable from the workflow root",
                workflow.path,
            ))

    scalar_values: dict[str, set[str]] = {}
    matrix_values: dict[str, set[str]] = {}
    for job in workflow.job_details:
        for declaration in job.runtimes:
            language, _, value = declaration.partition(":")
            if value.startswith("matrix:"):
                matrix_values.setdefault(language, set()).add(value[len("matrix:"):])
            else:
                scalar_values.setdefault(language, set()).add(value)
    for language, values in sorted(scalar_values.items()):
        # A matrix axis is deliberate multi-version coverage; a scalar disagrees only with
        # another scalar or with a version the matrix does not cover.
        covered = matrix_values.get(language, set())
        if len(values) > 1 or (covered and not values <= covered):
            rendered = ", ".join(f"{language}={value}" for value in sorted(values | covered))
            findings.append(_finding(
                "delivery-runtime-drift",
                f"workflow runtime declarations disagree: {rendered}",
                workflow.path,
            ))

    return findings


def check_provider_policy(inventory: DeliveryInventory) -> list[Finding]:
    providers = {workflow.provider for workflow in inventory.workflows}
    if not providers:
        return [_finding(
            "delivery-provider-policy",
            "delivery is applicable but no recognized provider-equivalent workflow is measured",
            CONFIG_FILE,
        )]
    if inventory.applicability.provider == "multiple" or len(providers) > 1:
        return [_finding(
            "delivery-provider-policy",
            "multiple delivery providers are present; the target owner must choose the policy",
            next(iter(inventory.workflows)).path,
        )]
    return []


def check_judgement_policy(inventory: DeliveryInventory) -> list[Finding]:
    findings: list[Finding] = []
    for workflow in inventory.workflows:
        for job in workflow.job_details:
            if job.environment:
                findings.append(_finding(
                    "delivery-environment-policy",
                    f"job `{job.name}` declares environment `{job.environment}`",
                    workflow.path,
                ))
            if job.permissions:
                findings.append(_finding(
                    "delivery-permission-policy",
                    f"job `{job.name}` declares permissions requiring security review",
                    workflow.path,
                ))
            if job.release and any(
                    any(word in command.lower() for word in ("publish", "upload", "push", "release"))
                    for command in job.commands):
                findings.append(_finding(
                    "delivery-publish-scope",
                    f"release job `{job.name}` exposes a publish command whose scope is owner-decided",
                    workflow.path,
                ))
        if workflow.permissions:
            findings.append(_finding(
                "delivery-permission-policy",
                "workflow permissions require the read-only security audit",
                workflow.path,
            ))
    return findings


def run_checks(inventory: DeliveryInventory) -> list[Finding]:
    return CHECK_REGISTRY.run(inventory)


def check_workflows(inventory: DeliveryInventory) -> list[Finding]:
    return [finding for workflow in inventory.workflows
            for finding in _workflow_findings(workflow)]


CHECK_REGISTRY = register_checks(
    ("provider-policy", check_provider_policy),
    ("workflows", check_workflows),
    ("judgement-policy", check_judgement_policy),
)
