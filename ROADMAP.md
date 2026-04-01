# Infrastructure Testing Agent — Roadmap

## Phase 1: Module Upgrade Testing (current) ✅ scaffolded

Core capability: deploy an existing AVM module version, plan the upgrade to a
new version, and produce a structured diff report.

- [x] Project scaffold (Dockerfile, agent.yaml, config, main.py)
- [x] Workspace management tools (create, delete, list, read, write files)
- [x] Terraform tools (init, plan, apply, destroy, show, output)
- [x] Git tools (clone repo, clone registry module)
- [x] Analysis tools (summarise plan JSON, read UPGRADE.md)
- [x] Azure tools (resource group CRUD, identity check, RBAC check)
- [x] System instructions with workflow conventions
- [ ] Local testing with mock/stub (no Azure dependency)
- [ ] Idempotency check workflow (apply → plan → assert empty)
- [ ] UPGRADE.md cross-reference reporting
- [ ] Support specifying module examples (not just `default`)
- [ ] End-to-end test with a real Foundry project + Application Gateway module

## Phase 2: Enhanced Reporting

- [ ] Structured JSON report output (machine-readable)
- [ ] Markdown report generation (human-readable, suitable for PR comments)
- [ ] Diff visualisation for plan changes
- [ ] Cost estimation integration (e.g. Infracost)
- [ ] Track test history across runs (via Foundry conversations)

## Phase 3: Azure Landing Zone / Generic Module Testing

- [ ] `terraform test` integration (native testing framework)
- [ ] Post-deploy validation via Azure Resource Graph queries
- [ ] Integration test runner (deploy module A → check module B can connect)
- [ ] Parameterised test scenarios (matrix of inputs/examples)
- [ ] ALZ module dependency graph awareness
- [ ] Compliance/policy check after deployment (Azure Policy)

## Phase 4: CI/CD & GitHub Integration

- [ ] GitHub Actions workflow for triggering the agent
- [ ] GitHub Agentic Workflows integration (when available)
- [ ] PR comment integration (post test results as PR comments)
- [ ] Webhook trigger support (deploy on PR open/update)
- [ ] State management for tracking which module versions have been tested

## Phase 5: Multi-Module & Composition Testing

- [ ] Test module composition (deploy module A + module B together)
- [ ] Cross-module dependency validation
- [ ] Landing zone end-to-end deployment pipeline
- [ ] Environment promotion testing (dev → staging config diffs)

## Phase 6: Intelligence & Learning

- [ ] Learn from previous test runs (common failure patterns)
- [ ] Auto-generate UPGRADE.md content from plan diffs
- [ ] Suggest fixes for common Terraform errors
- [ ] Foundry evaluation integration (track agent quality over time)

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
