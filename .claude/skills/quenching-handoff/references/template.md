# Handoff template

The sections every quenching handoff carries, in this order, each as a `## ` heading exactly as
named. Both doors write it: `/quenching-handoff` (the main model, while the prompt cache is warm)
and `/quenching-cache handoff` (Haiku, once it has expired; the plugin carries a generated copy of
this file). `.agents/quenching/bin/cq specs handoff write` refuses a handoff without
`## Last Request` and `## Next Actions`. Leave out any other section that would be empty. Do not
list edited files or commits: `.agents/quenching/bin/cq specs handoff facts` adds those exactly,
under `## Facts`.

## Handoff Confidence
One line: High, Medium or Low, then why ("Medium: the reason for the last test failure is not in
the conversation").

## Objective
One short paragraph on what the user is trying to achieve overall: the end state, not the current
sub-task.

## Last Request
The user's last real message, verbatim (the facts carry it). Then one line: `Status: Answered`,
`Status: Partially answered` or `Status: Not answered`, and when it is not fully answered,
`Context needed: <file, command or decision to check first>`.

## Current Status
**Working:** what is confirmed to work, and how it was confirmed. **Broken:** the exact symptom
of anything broken, with error messages quoted verbatim.

## Decisions
Each decision with its reason, the final one only when the user changed their mind; mention a
reversal only if it stops the next session from repeating it.

## Completed Work
What was done, as references to commits, PRs, specs, ADRs or paths, never copies of their contents.

## Open Work
Anything half-done: uncommitted changes, a half-applied edit, a command still running, a release
not finished.

## Validation Evidence
The checks that ran and what they said: the command, the result, and what was not run.

## Blockers
What stops the next step, and who or what can lift it.

## Next Actions
Numbered, the first one doable at once without asking. Suggest a skill only when one of the
actions needs it.
