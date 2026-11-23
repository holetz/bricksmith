# `assets/zensical/` — the site setup payload (Zensical)

Inert here, like the rest of `assets/`. `/quenching-knowledge align` (Step 7) stamps these into a target
repo **once**, while scaffolding the bundle, so the OKF bundle renders as a site;
`/quenching-knowledge documentation-build` **owns** the layer after that — it installs, merges forward,
keeps the `nav` in step with the folder tree, and verifies the build. **The OKF
markdown stays generator-neutral; only this config layer names a generator.**

| File | Stamped to | Notes |
| --- | --- | --- |
| `zensical.toml.tmpl` | repo root `zensical.toml` | only if absent; fill `site_name`/`site_description`; keep `docs_dir = "site-source"` and stage only the publication allow-list |
| `requirements.txt` | repo root, only when no `pyproject.toml` is used | `zensical` |
| `quenching.css` | the bundle, at `assets/stylesheets/quenching.css` | the reduced-motion guard, and nothing else — the badge/hero/card rules went with the theme constructs the generator-neutral rule removed; `extra_css` in `zensical.toml` wires it |
| `ci-github-pages.yml` | `.github/workflows/docs.yml` | opt-in; GitHub Pages via the Pages artifact — the repo's Pages source must be "GitHub Actions" |
| `azure-pipelines-docs.yml` | `azure-pipelines-docs.yml` | opt-in; Azure DevOps publishes the strict `site/` output as `documentation-site` |

**The nav lives in the config.** Zensical runs no plugins, so nothing derives the sidebar from a
sidecar file: the `nav` list in `zensical.toml` carries the order and the section titles, and a new
page is a new line in it. The generated `site-source/` tree is the publication boundary; omitting a
page from `nav` does not prevent Zensical from processing it.

## Publication and identity contract

`site_url` is required before a site is delivered publicly: derive it from the approved destination
or leave it as an explicit configuration gap. `repo_url` comes from the target's Git remote when it
is a usable public/repository URL. Zensical does not derive Azure DevOps edit links, so an Azure
target uses a tested `edit_uri_template` such as `?path=/{path}&version=GB<branch>`; replace
`<branch>` with the target's actual branch.

Set `language`, palette colors, logo and favicon only from a target-owned language or brand
contract. The template deliberately comments these values rather than making a plausible-looking
placeholder public metadata.

The Azure template is deliberately opt-in. It installs the pinned toolchain, runs the strict build,
and publishes `documentation-site` as a pipeline artifact; it does not deploy to a remote host.
Treat the artifact as an immutable handoff, a local preview as a developer convenience, and a remote
deployment as a separate platform-owned stage with its own credentials and approval.

## Validate locally

```bash
pip install -r requirements.txt
.agents/quenching/bin/cq knowledge project docs --check
.agents/quenching/bin/cq knowledge site-source docs site-source --write
zensical build --clean --strict
# fallbacks: python -m zensical build --clean --strict · uv run zensical build --clean --strict
```

When the target repository uses uv (`uv.lock` or a `[tool.uv]` table in `pyproject.toml`), the
documentation command adds `zensical` to the development group with `uv add --dev` when absent,
refreshes `uv.lock` with `uv lock`, and runs `uv sync`; the lockfile is the authoritative resolution.
Other project managers keep their own lock/install command. Do not add both an unpinned dependency
and a second lock source for the same target, and do not create `requirements.txt` beside a managed
`pyproject.toml`.

The build command is the verification path; the documentation family reports `unverified` when
the toolchain is not installed. `zensical build` has no `--site-dir`: it writes to the configured
`site_dir` (gitignored), and `.cache/` beside the config ignores itself. Use `zensical serve` only
when a local preview is explicitly requested.

`index.md` in each section is the section landing page (`navigation.indexes`). The plan's **Mapa
editorial de publicação** decides the allowlisted site surface. Raw `catalog/` and `external/`
homes remain knowledge sources but are absent from `site-source/`, routes and nav; curate any
reader-facing use into an allowlisted page with provenance. Links use the mapped route — never an
internal `/docs/<home>/…` path. A `do not publish` home has no staged file, route or nav entry.

The glossary route is `glossary.md` in the bounded source. When the canonical root `glossary.md`
has content, it is published by default unless the accepted map explicitly says `do not publish`.
The write stage runs `.agents/quenching/bin/cq knowledge project --write`, which derives the abbreviation snippet and
records the source SHA-256; `.agents/quenching/bin/cq knowledge site-source --write` stages both without creating a
second editable glossary. The template's `abbr` and
`pymdownx.snippets.auto_append` load those definitions on every page. The derived files are
generated data, never a second hand-maintained glossary.
