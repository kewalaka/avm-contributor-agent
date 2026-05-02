# tf-module-developer-agent — Roadmap

## Architecture

Two-agent maker/checker pipeline: **Developer** (writes code) + **Reviewer** (pre-push diff gatekeeper).
CI in `kewalaka/avm-contributions` is the third signal — deterministic tests dispatched via
`repository_dispatch`, polled, and artifact results consumed by the pipeline.

```
upstream issue
    └── fork sync
          └── Developer agent  (module AVM skill + additive instructions)
                └── Reviewer agent  (AVM review skill + additive instructions)
                      └── kewalaka/avm-contributions  (GHA: checks → e2e → upgrade)
                            └── draft PR on upstream
```

## Phase 1 — Demolition ✅

Remove old testing-agent surface; slim `main.py` system instructions.

- [x] Delete obsolete agents: `discovery.py`, `deploy.py`, `analysis.py`, `reporter.py`
- [x] Delete obsolete tools: `terraform.py`, `upgrade_test.py`, `azure.py`
- [x] Slim `main.py` (removed 100+ lines of testing-agent system instructions)
- [x] Preserve `agents/base.py` (`create_specialist` factory, `AgentResult`)

## Phase 2 — Core Surfaces ✅

New DevRequest contract; guardrailed git/fork/dispatch tools.

- [x] `DevRequest` dataclass (issue-driven + existing-repo modes, `auto_branch_name`)
- [x] `tools/fork_ops.py` — `ensure_fork`, `sync_fork_default_branch`, `clone_fork`, `get_fork_info`
- [x] `tools/git_ops.py` extended — `create_branch`, `commit_files`, `push_branch` (5 guardrails), `verify_branch_provenance`
- [x] `tools/dispatch_ci.py` — `dispatch_module_checks`, `dispatch_module_e2e`, `dispatch_upgrade_test`, `check_dispatch_token`
- [x] Auth model: `gh auth login` for GitHub ops + `AGENT_DISPATCH_TOKEN` for CI dispatch (separate domains)

## Phase 3 — Maker / Checker Pipeline ✅

Two-agent orchestrator with stop conditions.

- [x] `agents/orchestrator.py` rewritten as ~30-line two-agent driver
- [x] `agents/reviewer.py` refactored to pre-push diff gatekeeper
- [x] Stop conditions: 3 Reviewer rejects OR 3 CI failures → draft PR + upstream issue comment
- [x] Developer skill loading: dynamic per-run from module workspace `.agents/skills/AVM-Terraform-Development/SKILL.md`
- [x] Reviewer skill loading: static from `agents/skills/avm-review-skill.md` + `reviewer-additive.md`
- [x] AzAPI-first mandates enforced in both additive prompts and review skill

## Phase 4 — CLI Wiring ✅

User-facing entry points and PR lifecycle.

- [x] `python main.py dev` — issue-driven development pipeline
- [x] `python main.py chat` — interactive session (inherits dev instructions)
- [x] `python main.py test` — legacy testing-only path (preserved)
- [x] `tools/github_ops.py` extended — `update_pr_body_section` (managed regions), `download_workflow_artifacts`
- [x] Draft PR opened after CI green; managed body sections `<!-- agent:summary -->` / `<!-- agent:evidence -->`
- [x] `--fork-owner`, `--upstream-repo`, `--issue`, `--branch` CLI flags

## Phase 5 — E2E Smoke 🚧

Drive the full pipeline against a real upstream issue.

- [ ] End-to-end smoke on `Azure/terraform-azurerm-avm-res-app-managedenvironment`
- [ ] Verify CI dispatch round-trip (checks + e2e artifacts downloaded correctly)
- [ ] Verify PR body managed sections regenerate correctly on re-push
- [ ] Verify Reviewer stop condition triggers correctly on persistent failures

## Phase 6 — Existing-Repo / PR Mode (issue #12)

Start from a local fork branch or open PR rather than a GitHub issue.

- [ ] `--existing-repo <path>` CLI flag wired in `orchestrator.py` (currently modelled but ignored)
- [ ] Skip fork-creation and upstream-PR-open when in existing-repo mode
- [ ] Tutorial: `aca-managed-environment-dev.ipynb` demonstrates this path

## Deferred — Phase 7: Daemon Mode

Long-running FastAPI service + GitHub App webhook for `ci-result` callbacks.
Only build if multi-tenant / always-on becomes a requirement.
Phase 1–6 architecture is forward-compatible.

- [ ] FastAPI server with `/webhook` endpoint
- [ ] GitHub App for `check_suite` + custom `ci-result` events
- [ ] Replace polling with push-based result delivery

## External Track — avm-contributions upgrades

Work in `kewalaka/avm-contributions`, not here.

- [ ] `upgrade-tests.yml` workflow (`module-upgrade` dispatch event; base apply → head plan matrix)
- [ ] `test-upgrade-example` Make target writing `summary.json`, `upgrade-plan.json`, idempotency artifacts
- [ ] Resolves issue #10 (upgrade evidence for UPGRADE.md authoring)

## Auth Reference

| Credential | Scope | Used for |
|-----------|-------|---------|
| `gh auth login` | GitHub (developer's account) | Fork ops, issue fetch, PR open/update |
| `AGENT_DISPATCH_TOKEN` | `kewalaka/avm-contributions` only (Actions RW, Contents R, Metadata R) | `repository_dispatch` to trigger CI |
| Foundry DefaultAzureCredential | Azure AI project | Developer + Reviewer agent inference |
