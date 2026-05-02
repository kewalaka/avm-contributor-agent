# tf-module-developer-agent

An AI-powered developer agent for [Azure Verified Modules (AVM)](https://azure.github.io/Azure-Verified-Modules/)
Terraform modules. It takes GitHub issues from upstream AVM repositories, implements fixes on a fork,
gates them through an LLM reviewer, dispatches deterministic CI to
[`kewalaka/avm-contributions`](https://github.com/kewalaka/avm-contributions), and opens upstream PRs
with evidence.

Built with the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (Python).

---

## Architecture

```
Developer (maker)
    │  implements code changes on fork branch
    ▼
Reviewer (checker)
    │  approves / requests changes / rejects diff
    ▼
kewalaka/avm-contributions CI
    │  module-checks, module-e2e, module-upgrade workflows
    ▼
Upstream PR with UPGRADE.md evidence
```

**Key principles:**

- **Read, Don't Duplicate** — the Developer loads the module's own AVM skill
  (`<module>/.agents/skills/AVM-Terraform-Development/SKILL.md`) at runtime.
  If absent, the orchestrator runs `./avm pre-commit` automatically to align the
  module and generate the skill before starting the Developer.
- **Deterministic CI** — all test execution runs in `avm-contributions` via
  `repository_dispatch`. The agent never runs `terraform apply` locally.
- **Push guardrails** — branches are restricted to `agent/issue-*` and
  `agent/manual-*` prefixes; force-push is forbidden; every agent commit carries
  `Agent-Run-Id` and `Co-authored-by` trailers.

---

## Prerequisites

- Python 3.12+
- [GitHub CLI](https://cli.github.com/) — `gh auth login` before first run
- A [Microsoft Foundry](https://learn.microsoft.com/azure/ai-services/agents/) project
  with a `gpt-4.1` (or compatible) model deployment
- A fine-grained PAT (`AGENT_DISPATCH_TOKEN`) scoped to
  `kewalaka/avm-contributions` with `Actions: read/write`, `Contents: read`, `Metadata: read`
- A fork of the upstream AVM module you want to work on (the agent creates/syncs
  this automatically)

---

## Quick Start — Local

```bash
git clone https://github.com/kewalaka/tf-module-developer-agent
cd tf-module-developer-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.sample .env
# Edit .env — set AZURE_AI_PROJECT_ENDPOINT and AGENT_DISPATCH_TOKEN at minimum

gh auth login          # one-time browser auth

# Resolve an upstream issue on a fork
python main.py dev \
  --upstream-repo Azure/terraform-azurerm-avm-res-storage-storageaccount \
  --issue 42 \
  --fork-owner <your-github-org>
```

> **Pre-flight for best results:** If you have a local checkout of the module,
> run `./avm pre-commit` inside it first. This generates
> `.agents/skills/AVM-Terraform-Development/SKILL.md` which the Developer agent
> will load automatically for module-specific guidance.

---

## Quick Start — Foundry (existing AI landing zone)

If you have a Foundry AI landing zone already deployed:

```bash
cp .env.sample .env
# Set:
#   AZURE_AI_PROJECT_ENDPOINT=<your project endpoint>
#   AGENT_DISPATCH_TOKEN=<PAT as above>
#   FOUNDRY_HOSTED=true
#   GITHUB_MCP_CONNECTION_ID=<connection name in your Foundry project>

python main.py dev \
  --upstream-repo Azure/terraform-azurerm-avm-res-app-managedenvironment \
  --issue 17 \
  --fork-owner <your-github-org>
```

The `AZURE_AI_PROJECT_ENDPOINT` is the **Project endpoint** on the Overview page
of your Foundry resource in the Azure portal (format:
`https://<resource>.services.ai.azure.com/api/projects/<project>`).

---

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | **Yes** | — | Foundry project endpoint URL |
| `AGENT_DISPATCH_TOKEN` | **Yes** | — | Fine-grained PAT for `kewalaka/avm-contributions` CI dispatch |
| `MODEL_DEPLOYMENT_NAME` | No | `gpt-4.1` | Model deployment name in your Foundry project |
| `FOUNDRY_HOSTED` | No | `false` | Set `true` to enable Foundry-hosted mode with MCP servers |
| `MULTI_AGENT` | No | `false` | Enable multi-agent orchestration (Developer + Reviewer pipeline) |
| `GITHUB_MCP_CONNECTION_ID` | No | — | Foundry project connection ID for GitHub MCP server |
| `AZURE_MCP_CONNECTION_ID` | No | — | Foundry project connection ID for Azure MCP server |
| `EVA_MCP_SERVER_URL` | No | — | URL for the EVA/AzAPI MCP server |

---

## CLI Reference

```
python main.py <subcommand> [options]
```

| Subcommand | Description |
|------------|-------------|
| `dev` | **Issue-driven development** — fork, implement, review, dispatch CI, open PR |
| `chat` | Interactive chat mode (single agent, local or Foundry-hosted) |
| `test` | Legacy batch test-request mode |

### `dev` options

```
--upstream-repo   OWNER/REPO   Upstream AVM module repository (required)
--issue           N            Issue number to work on (required)
--fork-owner      OWNER        GitHub org/user that owns your fork (required)
--existing-repo   PATH         Use a local checkout instead of cloning (stub — see issue #12)
```

---

## How CI dispatch works

The agent dispatches to `kewalaka/avm-contributions` using `repository_dispatch`
events authenticated with `AGENT_DISPATCH_TOKEN`. Three workflows are available:

| Event | Workflow | Purpose |
|-------|----------|---------|
| `module-checks` | `checks.yml` | Linting and pre-commit validation |
| `module-e2e` | `e2e-tests.yml` | Full `terraform apply` + idempotency on examples |
| `module-upgrade` | `upgrade-tests.yml` | Two-phase upgrade: apply base → plan head, diff outputs |

The agent polls the dispatched run and downloads structured `summary.json` artifacts
to feed back into the Developer/Reviewer loop.

---

## Security

- **Push guardrails**: agent branches must match `^agent/(issue-\d+|manual)-[a-z0-9-]+$`.
  Never `main`, `master`, or `develop`. Force-push is disabled.
- **Auth separation**: `gh auth login` (developer's own identity) handles upstream/fork ops.
  `AGENT_DISPATCH_TOKEN` (scoped PAT) handles CI dispatch only.
- **Workspace isolation**: all clones live under `~/.tfdev/ws/<run_id>/`.
- **Provenance tracking**: first agent commit SHA is recorded; subsequent pushes
  are rejected if any intervening commit lacks an `Agent-Run-Id` trailer.

---

## Deploy to Foundry

See [agent.yaml](agent.yaml) for the hosted agent definition.
Follow the [hosted agents quickstart](https://learn.microsoft.com/azure/ai-services/agents/quickstart-hosted-agents)
to push the container and register the agent in your Foundry project.

---

See [ROADMAP.md](ROADMAP.md) for planned features and current status.
