# Infrastructure Testing Agent

A Microsoft Foundry hosted agent that tests Terraform / AVM module upgrades by deploying, checking idempotency, analysing plan diffs, and reporting findings via GitHub.

Built with the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (Python).

## Architecture

```
MCP Servers (GitHub, Azure, EVA/AzAPI)
         │
    Testing Agent (this repo)
         │  reads skills, examples, UPGRADE.md
    Module Under Test (MUT)
```

**Key principle:** Read, Don't Duplicate — the agent reads domain knowledge
(AzAPI patterns, test conventions, AVM workflow) from the MUT's `.agents/skills/`
directory at runtime, rather than embedding its own copy.

## Prerequisites

- Python 3.12+
- [Terraform CLI](https://developer.hashicorp.com/terraform/install)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (logged in)
- [GitHub CLI](https://cli.github.com/) (for issue/PR operations)
- A [Microsoft Foundry](https://learn.microsoft.com/azure/ai-services/agents/) project with a GPT-4.1 (or compatible) model deployment

## Quick Start

```bash
# Clone and enter the project
cd infra-testing-agent

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.sample .env
# Edit .env — set AZURE_AI_PROJECT_ENDPOINT and DEFAULT_SUBSCRIPTION_ID at minimum

# Run locally
python main.py
```

The agent listens on `http://localhost:8088` and exposes an OpenAI Responses-compatible API.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | — | Foundry project endpoint URL |
| `MODEL_DEPLOYMENT_NAME` | No | `gpt-4.1` | Deployed model name |
| `DEFAULT_LOCATION` | No | `australiaeast` | Azure region for test resources |
| `DEFAULT_SUBSCRIPTION_ID` | No | — | Target subscription for deployments |
| `CLEANUP_ON_COMPLETE` | No | `true` | Destroy resources after testing |
| `TEST_RG_PREFIX` | No | `rg-avm-test-` | Prefix for test resource groups |
| `FOUNDRY_HOSTED` | No | `false` | Enable Foundry-hosted mode with MCP |
| `GITHUB_MCP_CONNECTION_ID` | No | — | Foundry connection for GitHub MCP |
| `AZURE_MCP_CONNECTION_ID` | No | — | Foundry connection for Azure MCP |
| `EVA_MCP_SERVER_URL` | No | — | EVA/AzAPI MCP server endpoint |

## Tools

The agent exposes the following tool groups to the LLM:

| Group | Tools | Purpose |
|-------|-------|---------|
| Terraform | init, init-upgrade, plan, plan-json, apply, destroy, show, output, test, idempotency check | Core Terraform CLI operations |
| Workspace | create, delete, list/read/write files | Isolated temp directories for each test |
| Azure | resource group CRUD, identity check, RBAC check | Azure resource lifecycle |
| Git | clone repo, clone registry module | Fetch module source code |
| Module Discovery | ingest local, discover structure, read skills, list examples | Scan MUT for examples, tests, skills |
| Analysis | summarise plan JSON, read UPGRADE.md | Structured diff and documentation review |
| GitHub | create issue, create PR, add comment, search issues, get release | File bugs and propose changes |
| Reporting | test report, issue body, upgrade doc suggestion | Format findings for output |
| AVM CLI | run avm commands | Run MUT's own pre-commit, test runners |

## Docker

```bash
docker build -t infra-testing-agent .
docker run -p 8088:8088 --env-file .env infra-testing-agent
```

## Deploy to Foundry

See [agent.yaml](agent.yaml) for the deployment definition. Follow the [hosted agents quickstart](https://learn.microsoft.com/azure/ai-services/agents/quickstart-hosted-agents) to push the container and register the agent.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features and phases.
