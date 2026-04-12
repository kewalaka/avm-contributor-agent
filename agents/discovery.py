"""Discovery agent -- scans a Module Under Test to build a ModuleMap.

This agent has a narrow focus: ingest the module, scan its structure,
and return a ModuleMap with all discovered components. It does NOT
deploy anything or interact with Azure.
"""

from __future__ import annotations

from agents.base import create_specialist

from tools.terraform import (
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
)
from tools.git_ops import clone_registry_module, clone_repo
from tools.module_discovery import (
    discover_module_structure,
    ingest_local_module,
    list_module_examples,
    read_module_skill,
)
from tools.analysis import read_upgrade_doc

DISCOVERY_INSTRUCTIONS = """\
You are the Discovery Agent, a specialist in scanning Terraform modules.

Your job:
1. Ingest the module (local path, registry, or git clone).
2. Call `discover_module_structure` to build a ModuleMap.
3. List all examples with `list_module_examples`.
4. Read key skill files (especially example-test.md and AzAPI.md if present).
5. Read UPGRADE.md if it exists.
6. Return a complete structured summary of what you found.

You ONLY discover and report. You do NOT deploy, plan, or modify anything.

Output format: Return a JSON object with:
- module_map: the full ModuleMap
- examples: detailed example list
- skills_read: list of skill file paths you read
- upgrade_md_present: boolean
- key_findings: any notable observations about the module structure
"""

DISCOVERY_TOOLS = [
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
    clone_repo,
    clone_registry_module,
    ingest_local_module,
    discover_module_structure,
    read_module_skill,
    list_module_examples,
    read_upgrade_doc,
]


def create_discovery_agent():
    """Create the discovery specialist agent."""
    return create_specialist("discovery", DISCOVERY_INSTRUCTIONS, DISCOVERY_TOOLS)
