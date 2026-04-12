# Infrastructure Testing Agent — Roadmap

## Architecture

Three-layer separation: MCP Servers → Testing Agent → Module Under Test (MUT).
The agent reads skills and knowledge from the MUT at runtime (Read, Don't
Duplicate). Multi-agent topology planned for phases 3-4.

## Phase 1: Foundation ✅ scaffolded → 🚧 enhanced

Core tools and module discovery.

- [x] Project scaffold (Dockerfile, agent.yaml, config, main.py)
- [x] Workspace management tools (create, delete, list, read, write files)
- [x] Terraform tools (init, plan, apply, destroy, show, output)
- [x] Git tools (clone repo, clone registry module)
- [x] Analysis tools (summarise plan JSON, read UPGRADE.md)
- [x] Azure tools (resource group CRUD, identity check, RBAC check)
- [x] System instructions with workflow conventions
- [x] Module discovery tools (ingest local, discover structure, read skills)
- [x] Idempotency check tool (apply → plan → assert empty)
- [x] Structured plan output (terraform_plan_json)
- [x] AVM CLI integration (run_avm_cli)
- [x] GitHub operations (issues, PRs, comments, search via gh CLI)
- [x] Reporting tools (test reports, issue bodies, UPGRADE.md suggestions)
- [x] Shared data models (ModuleMap, DeployResult, AnalysisFinding, etc.)
- [x] Config support for MCP connections and runtime modes
- [ ] Local testing with mock/stub (no Azure dependency)
- [ ] End-to-end test with a real Foundry project + Application Gateway module

## Phase 2: MCP Integration & Foundry Runtime

## Phase 2: MCP Integration & Foundry Runtime 🚧

- [x] MCP server declarations (GitHub, Azure, EVA/AzAPI)
- [x] Dual runtime mode (local ChatAgent vs Foundry AIProjectClient)
- [x] `runtime/local.py` and `runtime/foundry.py` modules
- [x] Connection-based auth for hosted mode
- [ ] PromptAgentDefinition with structured inputs
- [ ] End-to-end test with MCP servers connected

## Phase 3: Multi-Agent Orchestration 🚧

- [x] Extract agents into `agents/` directory
- [x] Orchestrator with agent-as-tools pattern
- [x] Specialist agents: Discovery, Deploy, Analysis, Reviewer, Reporter
- [x] Focused instructions and tool subsets per agent
- [x] Structured handoff data between agents (via models.py)
- [x] MULTI_AGENT config flag for progressive adoption
- [ ] Concurrent deploy agents (one per example)
- [ ] Sequential analysis pipeline (Analysis → Reviewer → Reporter)
- [ ] Human-in-the-loop approval gates
- [ ] Agent-as-tool wiring (ChatAgent.as_tool() integration)

## Phase 4: Full Production

- [ ] A2ATool for published Foundry agents
- [ ] Agent card discovery
- [ ] Azure Landing Zone / composition module testing
- [ ] Cost estimation integration
- [ ] Compliance/policy checks after deployment
- [ ] CI/CD triggers (GitHub Actions, webhooks)
- [ ] Foundry evaluation integration (agent quality tracking)

## Infrastructure / Ops

- [ ] Terraform/Bicep to provision: Foundry resource + project + ACR + RBAC
- [ ] Published agent identity with scoped RBAC
- [ ] Application Insights integration for tracing
- [ ] Scale-to-zero configuration for cost management
- [ ] Private networking support (when Foundry supports it)

## RBAC Reference

The agent needs different Azure RBAC depending on the task:

| Task | Minimum Role | Scope |
|------|-------------|-------|
| Deploy & destroy modules | Contributor | Test resource group |
| Read-only audit | Reader | Subscription or RG |
| TF state (if using remote) | Storage Blob Data Contributor | Storage account |
| Foundry API access | Azure AI User | Foundry resource |

For the project managed identity (unpublished agent), assign roles to the
project's system-assigned managed identity.  After publishing, re-assign to the
agent's distinct identity.
