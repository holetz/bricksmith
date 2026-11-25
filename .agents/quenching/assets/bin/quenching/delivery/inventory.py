"""Build the delivery front's static workflow inventory."""
from __future__ import annotations

import os
import re
from pathlib import Path

from quenching.delivery.model import DeliveryInventory, Job, Workflow
from quenching.delivery.probe import probe_delivery, workflow_artifacts


_TOP_LEVEL = re.compile(r"^[\"']?([A-Za-z_][A-Za-z0-9_.-]*)[\"']?:(?:\s*(.*?))?\s*$")
_RUSTUP_SETUP = re.compile(r"(?:^|&&|\|\||;|\n)\s*rustup\s+(?:toolchain\s+install|default|show\s+active-toolchain)\b")
_MAPPING = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_.-]*):(?:\s*(.*?))?\s*$")
_LIST = re.compile(r"^\s*-\s*([^\s#]+)")
_AZURE_JOB = re.compile(r"^\s*-\s*(?:job|deployment):\s*([^\s#]+)")
_AZURE_STAGE = re.compile(r"^\s*-\s*stage:\s*([^\s#]+)")
_AZURE_RUNTIME_TASKS = {
    "UsePythonVersion": "python",
    "UseDotNet": "dotnet",
    "NodeTool": "node",
}


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"


def _strip_comment(value: str) -> str:
    return value.split(" #", 1)[0].strip()


def _block(lines: list[str], heading: str) -> list[str]:
    start = next((index for index, line in enumerate(lines)
                  if _TOP_LEVEL.match(line) and _TOP_LEVEL.match(line).group(1) == heading), None)
    if start is None:
        return []
    end = next((index for index in range(start + 1, len(lines))
                if _TOP_LEVEL.match(lines[index])), len(lines))
    return lines[start:end]


def _mapping_children(block: list[str]) -> list[tuple[int, str]]:
    mappings = [(index, len(line) - len(line.lstrip()), match.group(1))
                for index, line in enumerate(block[1:], 1)
                if (match := _MAPPING.match(line))]
    indent = min((width for _, width, _ in mappings), default=0)
    return [(index, name) for index, width, name in mappings if width == indent]


def _mapping_names(lines: list[str], heading: str) -> tuple[str, ...]:
    return tuple(sorted(name for _, name in _mapping_children(_block(lines, heading))))


def _list_values(lines: list[str], heading: str) -> tuple[str, ...]:
    block = _block(lines, heading)
    return tuple(sorted({_strip_comment(match.group(1)) for line in block[1:]
                         if (match := _LIST.match(line))}))


def _job_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    block = _block(lines, "jobs")
    starts = _mapping_children(block)
    result: list[tuple[str, list[str]]] = []
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(block)
        result.append((name, block[start:end]))
    return result


def _azure_job_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    block = _block(lines, "jobs")
    starts = [(index, _strip_comment(match.group(1)).strip(" '\""))
              for index, line in enumerate(block[1:], 1)
              if (match := _AZURE_JOB.match(line))]
    result: list[tuple[str, list[str]]] = []
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(block)
        result.append((name, block[start:end]))
    return result


def _azure_stage_job_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    block = _block(lines, "stages")
    stage_starts = [(index, len(line) - len(line.lstrip()),
                     _strip_comment(match.group(1)).strip(" '\""))
                    for index, line in enumerate(block)
                    if (match := _AZURE_STAGE.match(line))]
    stage_data: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = []
    for position, (start, stage_indent, stage_name) in enumerate(stage_starts):
        end = stage_starts[position + 1][0] if position + 1 < len(stage_starts) else len(block)
        stage_lines = block[start:end]
        job_starts = [(index, _strip_comment(match.group(1)).strip(" '\""))
                      for index, line in enumerate(stage_lines[1:], 1)
                      if (match := _AZURE_JOB.match(line))
                      and len(line) - len(line.lstrip()) > stage_indent]
        jobs: list[tuple[str, list[str]]] = []
        for job_position, (job_start, job_name) in enumerate(job_starts):
            job_end = (job_starts[job_position + 1][0]
                       if job_position + 1 < len(job_starts) else len(stage_lines))
            jobs.append((job_name, stage_lines[job_start:job_end]))
        stage_data.append((stage_name, stage_lines, jobs))

    jobs_by_stage = {name: tuple(job_name for job_name, _ in jobs)
                     for name, _, jobs in stage_data}
    result: list[tuple[str, list[str]]] = []
    for _, stage_lines, jobs in stage_data:
        dependencies = _values_after(stage_lines, "dependsOn")
        needs: list[str] = []
        for dependency in dependencies:
            needs.extend(jobs_by_stage.get(dependency, (dependency,)))
        for job_name, job_lines in jobs:
            result.append((job_name, job_lines +
                           [f"  needs: [{', '.join(sorted(set(needs)))}]"]
                           if needs else job_lines))
    return result


def _azure_runtime_values(lines: list[str]) -> list[str]:
    runtimes: list[str] = []
    language: str | None = None
    for line in lines:
        task = re.search(r"\btask:\s*([A-Za-z]+)@", line)
        if task:
            language = _AZURE_RUNTIME_TASKS.get(task.group(1))
            continue
        if re.match(r"^\s*-\s*", line):
            language = None
        if language:
            version = re.search(r"\bversionSpec:\s*(.*?)\s*$", line)
            if version:
                value = _strip_comment(version.group(1)).strip(" '\"")
                if value:
                    runtimes.append(f"{language}:{value}")
    return runtimes


def _provider_job_blocks(lines: list[str], kind: str) -> list[tuple[str, list[str]]]:
    github_jobs = _job_blocks(lines)
    if kind == "azure-pipelines":
        stage_jobs = _azure_stage_job_blocks(lines)
        if stage_jobs:
            return stage_jobs
        azure_jobs = _azure_job_blocks(lines)
        if azure_jobs:
            return azure_jobs
        steps = _block(lines, "steps")
        return [("default", steps)] if steps else []
    if kind != "gitlab-ci" or github_jobs:
        return github_jobs
    reserved = {"stages", "workflow", "variables", "default", "image", "include",
                "services", "before_script", "after_script", "cache"}
    starts = [(index, match.group(1)) for index, line in enumerate(lines)
              if (match := _TOP_LEVEL.match(line)) and match.group(1) not in reserved]
    result: list[tuple[str, list[str]]] = []
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        if any(re.match(r"^\s+(?:script|stage|rules|needs):", line) for line in block[1:]):
            result.append((name, block))
    return result


def _key_value(line: str, key: str) -> tuple[int, str] | None:
    match = re.match(rf"^(\s*){re.escape(key)}:\s*(.*?)\s*$", line)
    return (len(match.group(1)), _strip_comment(match.group(2))) if match else None


def _inline_values(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return tuple(item.strip(" '\"") for item in value[1:-1].split(",") if item.strip())
    return (value.strip(" '\""),) if value else ()


def _values_after(lines: list[str], key: str) -> tuple[str, ...]:
    for index, line in enumerate(lines):
        found = _key_value(line, key)
        if found is None:
            continue
        indent, inline = found
        values = list(_inline_values(inline))
        for child in lines[index + 1:]:
            child_indent = len(child) - len(child.lstrip())
            if child.strip() and child_indent <= indent:
                break
            if (match := _LIST.match(child)):
                values.append(_strip_comment(match.group(1)))
            elif (match := _MAPPING.match(child)):
                values.append(match.group(1))
        return tuple(sorted(set(values)))
    return ()


def _job(lines: list[str], name: str, kind: str) -> Job:
    actions = tuple(sorted(set(match.group(1) for line in lines
                                if (match := re.search(r"\buses:\s*([^\s#]+)", line)))))
    commands: list[str] = []
    script_indent: int | None = None
    scalar_indent: int | None = None
    keys = "run|script|bash|pwsh" if kind == "azure-pipelines" else "run|script"
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if scalar_indent is not None:
            if not line.strip():
                continue
            if indent > scalar_indent:
                if command := _strip_comment(line):
                    commands.append(command)
                continue
            scalar_indent = None
        command_key = re.match(rf"^(\s*)(-\s*)?(?:{keys}):\s*(.*?)\s*$", line)
        if command_key:
            key_indent = len(command_key.group(1)) + len(command_key.group(2) or "")
            value = _strip_comment(command_key.group(3))
            script_indent = None
            if re.fullmatch(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?", value):
                scalar_indent = key_indent
            elif value:
                commands.append(value)
            elif re.match(r"^\s*script:", line):
                script_indent = key_indent
            continue
        if script_indent is not None:
            if line.strip() and indent <= script_indent:
                script_indent = None
            elif (command := re.match(r"^\s*-\s+(.+?)\s*$", line)):
                commands.append(_strip_comment(command.group(1)))
    runtimes: list[str] = []
    for line in lines:
        match = re.search(r"\b(python|node|ruby|java|go|rust)[-_]version:\s*(.*?)\s*$",
                          line, re.IGNORECASE)
        if match:
            language = match.group(1).lower()
            value = _strip_comment(match.group(2)).strip()
            if value.startswith("["):
                # A flow list is a matrix axis: every element is one deliberate cell.
                for item in value.strip("[]").split(","):
                    if item.strip(" '\""):
                        runtimes.append(f"{language}:matrix:{item.strip(' ' + chr(39) + chr(34))}")
            elif "${{" not in value and value:
                runtimes.append(f"{language}:{value.strip(chr(39) + chr(34) + ' ')}")
        image = re.search(r"\bimage:\s*(?:[^/\s]+/)?(python|node|ruby|java|go|rust):([^\s#]+)",
                          line, re.IGNORECASE)
        if image:
            runtimes.append(image.group(1).lower() + ":" + image.group(2).strip(" '\""))
    if kind == "azure-pipelines":
        runtimes.extend(_azure_runtime_values(lines))
    environment = next((found.group(1).strip(" '\"") for line in lines
                        if (found := re.match(r"^\s*environment:\s*(.+?)\s*$", line))), None)
    stage = next((found.group(1).strip(" '\"") for line in lines
                  if (found := re.match(r"^\s*stage:\s*(.+?)\s*$", line))), None)
    needs = _values_after(lines, "needs")
    if kind == "azure-pipelines":
        needs = tuple(sorted(set(needs) | set(_values_after(lines, "dependsOn"))))
    permissions = _values_after(lines, "permissions")
    release = bool(re.search(r"(?i)(release|publish|deploy)", name)
                   or any(re.search(r"(?i)(gh\s+release|npm\s+publish|twine\s+upload|docker\s+push|publish)", value)
                          for value in commands)
                   or any(re.search(r"(?i)(release|publish|deploy)", action) for action in actions))
    job_conditions = lines[1:] if lines and not lines[0].startswith("steps:") else []
    property_indent = min((len(line) - len(line.lstrip()) for line in job_conditions
                           if _MAPPING.match(line)), default=None)
    active = not any(
        found[0] == property_indent
        and re.fullmatch(r"(?:\$\{\{\s*)?(?:false|never)(?:\s*\}\})?",
                         found[1], re.IGNORECASE)
        for line in job_conditions if (found := _key_value(line, "if")) is not None
    )
    return Job(
        name=name,
        needs=needs,
        commands=tuple(commands),
        actions=actions,
        checkout=any(action.startswith("actions/checkout@") for action in actions)
        or kind == "gitlab-ci"
        or kind == "azure-pipelines" and any(
            re.match(r"^\s*-\s*checkout:\s*self(?:\s|$)", line) for line in lines),
        setup=any(re.match(r"^(?:[^/@]+/)?setup-", action) for action in actions)
        or any(_RUSTUP_SETUP.search(command) for command in commands)
        or kind == "gitlab-ci" and any(re.match(r"^\s*(?:image|before_script):", line)
                                        for line in lines)
        or kind == "azure-pipelines" and any(
            re.search(r"\btask:\s*(?:UsePythonVersion|UseDotNet|NodeTool)@", line) for line in lines)
        or kind in {"azure-pipelines", "github-actions"} and any(
            re.match(r"^\s*container:", line) for line in lines),
        runtimes=tuple(sorted(set(runtimes))),
        stage=stage,
        release=release,
        environment=environment,
        permissions=permissions,
        active=active,
    )


def _trigger_names(lines: list[str], kind: str) -> list[str]:
    block = _block(lines, "on") or _block(lines, "trigger")
    if not block and kind == "gitlab-ci":
        block = _block(lines, "workflow") or _block(lines, "rules")
    if not block:
        return []
    first = _TOP_LEVEL.match(block[0])
    inline = _strip_comment(first.group(2) or "") if first else ""
    if inline and inline not in {"{}", "null"}:
        if inline.startswith("[") and inline.endswith("]"):
            return [item.strip(" '\"") for item in inline[1:-1].split(",") if item.strip()]
        return [inline.strip(" '\"")]
    names = [name for _, name in _mapping_children(block)]
    if not names and kind != "gitlab-ci":
        names = [_strip_comment(match.group(1)).strip("'\"") for line in block[1:]
                 if (match := _LIST.match(line))]
    return names


def _azure_extra_triggers(lines: list[str]) -> list[str]:
    names = []
    pr = _block(lines, "pr")
    if pr:
        first = _TOP_LEVEL.match(pr[0])
        inline = _strip_comment(first.group(2) or "") if first else ""
        body = [line for line in pr[1:] if _strip_comment(line)]
        items = [_strip_comment(m.group(1)).strip("'\"") for line in body
                 if (m := _LIST.match(line))]
        if inline:
            if inline.lower() != "none" and inline not in {"{}", "null"}:
                names.append("pr")
        elif body and not (items and all(item.lower() == "none" for item in items)
                           and len(items) == len(body)):
            names.append("pr")
    if _block(lines, "schedules"):
        names.append("schedules")
    return names


def _triggers(lines: list[str], kind: str) -> tuple[str, ...]:
    names = _trigger_names(lines, kind)
    # An absent trigger key is the platform's default: run on every push.
    if (kind == "azure-pipelines" and not _block(lines, "trigger")
            or kind == "gitlab-ci" and not (_block(lines, "workflow") or _block(lines, "rules"))):
        names.append("push")
    if kind == "azure-pipelines":
        names += _azure_extra_triggers(lines)
    return tuple(sorted(set(name for name in names if name.lower() != "none")))


def _workflow(root: Path, relative: str, provider: str, kind: str) -> Workflow:
    text, error = _read(root / relative)
    if error:
        return Workflow(relative, provider, kind, error)
    assert text is not None
    lines = text.splitlines()
    job_blocks = _provider_job_blocks(lines, kind)
    jobs = tuple(sorted(name for name, _ in job_blocks))
    return Workflow(
        relative,
        provider,
        kind,
        "ok",
        triggers=_triggers(lines, kind),
        jobs=jobs,
        stages=_list_values(lines, "stages"),
        job_details=tuple(_job(block, name, kind) for name, block in job_blocks),
        permissions=_values_after(lines, "permissions"),
    )


def build_inventory(root: str) -> tuple[DeliveryInventory | None, dict]:
    """Return one inventory for an applicable target, or its refusal payload."""
    repo_root = Path(os.path.abspath(root))
    applicability = probe_delivery(str(repo_root))
    if applicability.state == "refused":
        return None, applicability.config_error
    if applicability.state != "applicable":
        return None, {}
    workflows = tuple(_workflow(repo_root, relative, provider, kind)
                      for relative, provider, kind in workflow_artifacts(repo_root))
    unreadable = next((workflow for workflow in workflows if workflow.parse != "ok"), None)
    if unreadable:
        return None, {
            "code": "delivery-workflow-unreadable",
            "exit": 2,
            "path": unreadable.path,
            "message": f"workflow `{unreadable.path}` could not be read",
        }
    return DeliveryInventory(str(repo_root), applicability, workflows), {}
