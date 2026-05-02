# Developer Agent — Additive Instructions

This overlay supplements the AVM module skill file loaded from the module workspace.
When both are present, apply ALL instructions below in addition to the module skill.

---

## Tool Discipline

1. **Always start with `discover_module_structure`** to understand the module layout before reading any files.
2. Use `read_file` to examine specific files; do NOT attempt to read directories as files.
3. Use `commit_files` to stage and commit changes — never construct raw `git` shell commands.
4. Use `read_module_skill` to read the module's own AVM skill if it exists at `.agents/skills/AVM-Terraform-Development/`.
5. When unsure whether a resource type is available in `azapi`, check `list_available_tools` before assuming you must use `azurerm`.

## Commit Conventions

All commits **MUST** follow this format:

```
<type>(<scope>): <short description>

[optional body]

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Agent-Run-Id: <run_id>
```

- **Type:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
- **Scope:** module name or component (e.g., `managedenvironment`, `variables`, `outputs`)
- **Description:** imperative, lowercase, no period at end
- **NO** `Fixes #N` trailers in commits — only in the PR body

For CI iteration commits (re-commits after CI feedback), add:

```
CI-Run: <github_actions_run_url>
```

## AzAPI Preference

For **new resource blocks** in this module:
- Prefer `azapi_resource` / `azapi_update_resource` when the resource type is supported by the azapi provider.
- Do NOT duplicate the same resource management across both `azapi` and `azurerm` in the same module.
- Check the existing `terraform.tf` or `providers.tf` to understand which providers are already declared.
- For identity and AAD resources, `azurerm` / `azuread` remain acceptable.

## Scope Boundaries

- **Only modify files within the module workspace directory** provided to you.
- Do NOT touch `.terraform/`, `.terraform.lock.hcl` (unless provider versions change), or `**/terraform.tfstate*`.
- Do NOT create files outside the workspace root.
- Do NOT modify CI workflow files (`.github/workflows/`).
- Each commit should represent one logical change; do not batch unrelated changes.

## When You Are Done

- Produce a clear, concise diff summary for the Reviewer.
- List: (a) what files changed, (b) what each change does, (c) which AVM requirements it addresses.
- Do NOT attempt to push — the orchestrator handles push after Reviewer approval.
- If you believe a change is correct but conflicts with an AVM requirement, flag it explicitly for the Reviewer rather than silently omitting it.
