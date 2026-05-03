# AGENTS.md — avm-contributor-agent

Orientation for any agent working in this repo.

---

## What this is

A two-agent CLI pipeline that fixes AVM Terraform module issues automatically:

1. **Developer agent** — reads the upstream issue, forks the module repo, makes code changes, commits them.
2. **Reviewer agent** — inspects the diff before every push; rejects if it violates AVM conventions.
3. **CI signal** — `kewalaka/avm-contributions` runs the real tests via `repository_dispatch`; the pipeline polls for results and opens a draft PR with evidence when CI is green.

---

## Agent framework

| Concern | Detail |
| ------- | ------ |
| Class | `ChatAgent` from `agent_framework` |
| Client | `AzureAIAgentClient` from `agent_framework.azure` |
| Tool decorator | `@ai_function` from `agent_framework` |
| Invoke | `await agent.get_response(message_str)` |
| Factory | `agents/base.py::create_specialist(name, instructions, tools, mcp_tools=None)` |
| Config | `config.py::AgentConfig` — reads from env; singleton at `config.config` |

---

## MCP tools

MCP (Model Context Protocol) servers extend specialist agents with server-side capabilities
beyond local Python tools.  Two runtime tiers:

| Mode | MCP | Local tools (`@ai_function`) |
| ---- | --- | ----------------------------- |
| Local (`FOUNDRY_HOSTED=false`) | None — gh CLI wrappers used instead | All tools in `DEVELOPER_TOOLS` |
| Foundry-hosted (`FOUNDRY_HOSTED=true`) | GitHub + Azure + EVA/AzAPI (if configured) | Same local tools, plus MCPTool objects appended |

`create_specialist()` in `agents/base.py` auto-injects MCP tools when both
`config.foundry_hosted` and `config.has_mcp` are true.  Pass `mcp_tools=[]`
explicitly to suppress injection.

### Configured MCP servers

| Env var | Server label | MCP URL | When to use |
| ------- | ------------ | ------- | ----------- |
| `GITHUB_MCP_CONNECTION_ID` | `github` | `https://api.githubcopilot.com/mcp` | Reading issues, PR content, repo files from GitHub without gh CLI |
| `AZURE_MCP_CONNECTION_ID` | `azure` | `https://mcp.azure.com` | Querying Azure resources / ARM APIs |
| `EVA_MCP_SERVER_URL` | `eva_azapi` | custom URL | AzAPI resource type discovery |

`GITHUB_MCP_CONNECTION_ID` is the **Foundry project connection name** (not a PAT).
Create it in your Foundry project → Settings → Connected Resources → GitHub.
The `gh` CLI tools in `tools/github_ops.py` remain available as fallback in local mode.

---

## Entry points

```python
python main.py dev   --upstream-repo Azure/terraform-azurerm-avm-res-... \
                     --issue 167 [--fork-owner kewalaka]
python main.py dev   --upstream-repo ...  --pr 2  --fork-owner kewalaka
python main.py dev   --upstream-repo ...  --existing-repo ./path/to/local/fork
python main.py chat  # interactive dev session
python main.py test  # legacy test-only path (preserved; separate from dev pipeline)
```

`run_developer_pipeline(DevRequest)` in `agents/orchestrator.py` is the core async function.

---

## DevRequest modes

`request.py::DevRequest.mode` returns one of three strings:

| Mode | Trigger field | Behaviour |
| ---- | ------------- | --------- |
| `issue-driven` | `issue_number` set | fork → sync → clone → branch → fix |
| `existing-repo` | `local_path` set | local `git clone --local` into `~/.tfdev/ws/` → branch → fix |
| `existing-pr` | `pr_number` set | clone fork head branch → new agent branch → continue |

`validate()` enforces exactly one mode; raises `ValueError` with a clear message otherwise.

---

## File map

| Path | Role |
| ---- | ---- |
| `agents/orchestrator.py` | Pipeline driver (~800 lines); `run_developer_pipeline` entry point |
| `agents/reviewer.py` | Pre-push diff gatekeeper; returns `DiffReview` |
| `agents/base.py` | `create_specialist` factory (auto-injects MCP tools in Foundry mode); `AgentResult` dataclass |
| `agents/prompts/developer-additive.md` | Appended to module SKILL.md for Developer instructions |
| `agents/prompts/reviewer-additive.md` | Appended to reviewer skill |
| `agents/skills/avm-review-skill.md` | Static AVM review skill (Reviewer) |
| `runtime/local.py` | Local-mode agent factory — `ChatAgent` + `AzureAIAgentClient`; no MCP |
| `runtime/foundry.py` | Foundry-hosted agent factory — `build_mcp_tools()` builds MCPTool declarations; `create_agent` wires MCP into chat mode |
| `tools/dispatch_ci.py` | `dispatch_module_checks`, `dispatch_module_e2e`, `dispatch_upgrade_test` — all use `AGENT_DISPATCH_TOKEN` via `urllib`, never `gh` |
| `tools/fork_ops.py` | `ensure_fork`, `sync_fork_default_branch`, `clone_fork` |
| `tools/git_ops.py` | `create_branch`, `commit_files`, `push_branch` (5 guardrails), `verify_branch_provenance` |
| `tools/github_ops.py` | `create_pull_request`, `update_pr_body_section`, `flip_pr_ready`, `download_workflow_artifacts` — gh CLI wrappers; local-mode fallback when GitHub MCP is absent |
| `tools/module_discovery.py` | `discover_module_structure`, `ingest_local_module`, `list_module_examples`, `read_module_skill` |
| `config.py` | `AgentConfig` (env vars); `validate_dev_mode()` checks `gh` auth + token; `has_mcp` property |
| `request.py` | `DevRequest` + `TestRequest` dataclasses |
| `models.py` | `FixAttempt`, `CIResult`, `DiffReview` |

---

## Auth model

| Credential | Env var | Scope | Used by |
| ---------- | ------- | ----- | ------- |
| GitHub CLI session | — (run `gh auth login`) | Developer's account — fork, issue, PR ops | `tools/github_ops.py`, subprocess `gh` calls (local mode) |
| Fine-grained PAT | `AGENT_DISPATCH_TOKEN` | `kewalaka/avm-contributions` only (Actions:RW, Contents:R, Metadata:R) | `tools/dispatch_ci.py` — never touches other repos |
| Azure workload identity | — (DefaultAzureCredential) | Foundry AI project | `agents/base.py::create_specialist`, `runtime/foundry.py` |
| Foundry project connection | `GITHUB_MCP_CONNECTION_ID` | GitHub MCP server (Foundry-managed OAuth) | `runtime/foundry.py::build_mcp_tools` — Foundry-hosted mode only |
| Foundry project connection | `AZURE_MCP_CONNECTION_ID` | Azure MCP server | `runtime/foundry.py::build_mcp_tools` — Foundry-hosted mode only |

Required env vars:

```text
AZURE_AI_PROJECT_ENDPOINT   # Foundry project endpoint URL
MODEL_DEPLOYMENT_NAME       # defaults to gpt-4.1
AGENT_DISPATCH_TOKEN        # fine-grained PAT (see above)

# Optional — Foundry-hosted mode only:
FOUNDRY_HOSTED              # set to 'true' to enable MCP injection
GITHUB_MCP_CONNECTION_ID    # Foundry connection name for GitHub MCP
AZURE_MCP_CONNECTION_ID     # Foundry connection name for Azure MCP
EVA_MCP_SERVER_URL          # URL for EVA/AzAPI MCP server
```

---

## Workspace isolation

All agent work happens under `~/.tfdev/ws/<run_id>/<repo_name>/`.

`push_branch` in `tools/git_ops.py` enforces five hard guardrails — any violation raises and the push is blocked:

1. Branch must match `^agent/(issue-\d+|manual)-[a-z0-9-]+$`
2. Remote `origin` owner must match `fork_owner`; never pushes to `Azure/*`
3. No force-push
4. Commit provenance: every agent commit must carry `Agent-Run-Id:` trailer
5. Workspace must be under `~/.tfdev/ws/`

---

## Branch / commit / PR conventions

- **Branch**: `agent/issue-<N>-<slug>-<run_id[:6]>` or `agent/manual-<slug>-<run_id[:6]>`
- **Commits**: Conventional Commits + trailers `Co-authored-by: Copilot <...>` and `Agent-Run-Id: <full_run_id>`
- **PR body**: managed regions `<!-- agent:summary -->…<!-- /agent:summary -->` and `<!-- agent:evidence -->…<!-- /agent:evidence -->`; everything outside those markers is human territory; never overwrite
- **PR lifecycle**: open as draft → flip to ready only when CI is green (`flip_pr_ready`)

---

## Developer skill loading

`_load_module_skill_content(workspace_path)` in `orchestrator.py`:

1. Looks for `.agents/skills/AVM-Terraform-Development/SKILL.md` in the workspace
2. If absent, runs `./avm pre-commit` (deterministic; generates SKILL.md as a side effect)
3. If still absent, falls back to `_DEVELOPER_INSTRUCTIONS_FALLBACK`

Always runs `./avm pre-commit` at pipeline start regardless — it aligns the module before the Developer sees it. The Developer also runs `run_precommit_and_commit` post-implementation as a deterministic step (issue #15 — tools not yet wired into `DEVELOPER_TOOLS`).

---

## CI dispatch

`tools/dispatch_ci.py` — sends `repository_dispatch` to `kewalaka/avm-contributions`, then polls until the run completes (up to 3600s). Returns structured JSON payloads; the Developer/Reviewer agents never see raw GHA stdout.

| Function | Event type | When used |
| -------- | ---------- | --------- |
| `dispatch_module_checks` | `module-checks` | After every successful Reviewer pass |
| `dispatch_module_e2e` | `module-e2e` | When checks pass, before flipping PR ready |
| `dispatch_upgrade_test` | `module-upgrade` | **Not yet wired into orchestrator** (issue #16) |

---

## Stop conditions

- 3 consecutive Reviewer rejects → escalate: open draft PR + comment on upstream issue
- 3 CI failures with no progress → same escalation path
- Fork diverged from upstream → immediate `escalated` outcome; no retry

---

## AVM / Terraform conventions enforced by the pipeline

- AzAPI (`azapi_resource`) preferred over `azurerm` for new resources; enforced in both Developer and Reviewer prompts
- `snake_case` resource names, required outputs `id` and `resource`, no hardcoded locations
- `./avm pre-commit` must pass cleanly before push

---

## Open issues (as of this PR)

| # | Title | Status |
| --- | ----- | ------ |
| #15 | Add `run_precommit_and_commit`, `terraform_validate`, `terraform_plan` tools to Developer | Open — tools not yet in `DEVELOPER_TOOLS` |
| #16 | Wire `dispatch_upgrade_test` into orchestrator success path | Open — function exists, not called |
| #17 | Surface unresolved PR review comments to Developer in `existing-pr` mode | Open |
| #18 | Deploy pipeline as ACA service with KEDA Storage Queue scaling | Open — Phase 7 design |

Phase 5 E2E smoke (`Azure/terraform-azurerm-avm-res-app-managedenvironment` issue #167) is the immediate next step.

---

## Known gotchas

- **`dispatch_ci.py` polls only**: no webhook callback yet. CI results consumed by polling. Phase 7 adds push-based delivery.
- **`existing-pr` PR lookup**: with `--fork-owner`, looks in the fork repo (for fork-internal draft PRs), not upstream. Without it, looks in upstream. This is intentional.
- **`dispatch_upgrade_test` is unwired**: the function exists in `tools/dispatch_ci.py` but is never called by the orchestrator (issue #16).
- **`chat` is the bare default**: running `python main.py` with no subcommand launches `chat`, not `dev`.
- **MCP tools require Foundry connection setup**: `GITHUB_MCP_CONNECTION_ID` must be a valid Foundry project connection name, not a PAT or token. Create the connection in your Foundry project before setting the env var. In local mode, the `gh` CLI tools in `tools/github_ops.py` are used instead.
- **MCPTool injection is additive**: `create_specialist()` appends MCP tools to whatever `tools` list is passed — it does not replace local tools. This means both local `@ai_function` tools and MCP tools are available to agents in Foundry-hosted mode.
- **Reviewer agent suppresses MCP tools**: `review_diff()` explicitly passes `mcp_tools=[]` to `create_specialist()` so the Reviewer never gets GitHub or Azure MCP access. The Reviewer only performs static diff analysis and does not need server-side tool access; suppressing MCP injection reduces the agent's attack surface.
