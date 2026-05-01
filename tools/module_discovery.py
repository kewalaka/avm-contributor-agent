"""Module discovery and ingestion tools.

Scans a Module Under Test (MUT) to build a structured ModuleMap —
the agent uses this to understand what examples, tests, skills,
and tooling are available before starting a test run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from agent_framework import tool

from models import ModuleMap
from tools.terraform import _workspace_path


def _scan_examples(module_root: Path) -> list[str]:
    """Return sorted list of example directory names."""
    examples_dir = module_root / "examples"
    if not examples_dir.is_dir():
        return []
    return sorted(
        d.name for d in examples_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _scan_tests(module_root: Path) -> dict[str, list[str]]:
    """Return test files grouped by category (unit, integration, e2e)."""
    tests: dict[str, list[str]] = {}
    tests_dir = module_root / "tests"
    if not tests_dir.is_dir():
        return tests
    for category_dir in sorted(tests_dir.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        files = sorted(
            f.name for f in category_dir.iterdir()
            if f.is_file() and (
                f.suffix in (".tf", ".go")
                or f.name.endswith(".tftest.hcl")
            )
        )
        if files:
            tests[category_dir.name] = files
    return tests


def _scan_skills(module_root: Path) -> list[str]:
    """Return relative paths to skill markdown files."""
    skills: list[str] = []
    agents_dir = module_root / ".agents"
    if not agents_dir.is_dir():
        return skills
    for md_file in sorted(agents_dir.rglob("*.md")):
        skills.append(str(md_file.relative_to(module_root)))
    return skills


def _parse_providers(module_root: Path) -> dict[str, str]:
    """Extract provider version constraints from terraform.tf or versions.tf."""
    providers: dict[str, str] = {}
    for tf_file in ["terraform.tf", "versions.tf"]:
        path = module_root / tf_file
        if not path.is_file():
            continue
        content = path.read_text()
        # Match: source = "hashicorp/azurerm" and version = "~> 3.0"
        blocks = re.findall(
            r'(\w+)\s*=\s*\{[^}]*source\s*=\s*"([^"]+)"[^}]*version\s*=\s*"([^"]+)"',
            content,
            re.DOTALL,
        )
        for name, source, version in blocks:
            providers[source] = version
    return providers


def _detect_devcontainer_image(module_root: Path) -> str | None:
    """Read devcontainer.json for the container image."""
    dc_path = module_root / ".devcontainer" / "devcontainer.json"
    if not dc_path.is_file():
        return None
    try:
        # devcontainer.json may have comments — strip them
        content = dc_path.read_text()
        lines = [l for l in content.splitlines() if not l.strip().startswith("//")]
        data = json.loads("\n".join(lines))
        return data.get("image") or data.get("build", {}).get("dockerfile")
    except (json.JSONDecodeError, KeyError):
        return None


@tool
def ingest_local_module(
    workspace_id: str,
    local_path: str,
    target_dir: str = "module",
) -> str:
    """Ingest a Terraform module from a local filesystem path into a workspace.

    Creates a symlink (or copies if symlink fails) from the local module
    path into the workspace.  Use this for modules already checked out
    on the local machine.

    Args:
        workspace_id: Workspace id from create_workspace.
        local_path: Absolute path to the module directory on disk.
        target_dir: Directory name within the workspace.

    Returns:
        JSON with ingestion result including the workspace-relative path.
    """
    source = Path(local_path).expanduser().resolve()
    if not source.is_dir():
        return json.dumps({"error": f"Local path not found or not a directory: {local_path}"})

    ws_path = _workspace_path(workspace_id)
    target = ws_path / target_dir

    if target.exists():
        return json.dumps({"error": f"Target directory already exists: {target_dir}"})

    try:
        # Prefer symlink for speed; fall back to copy
        os.symlink(source, target)
        method = "symlink"
    except OSError:
        shutil.copytree(source, target, dirs_exist_ok=True)
        method = "copy"

    return json.dumps({
        "status": "ingested",
        "source": str(source),
        "target": target_dir,
        "method": method,
        "workspace_id": workspace_id,
    })


@tool
def discover_module_structure(
    workspace_id: str,
    module_dir: str = "module",
) -> str:
    """Scan a module in the workspace and return its structure as a ModuleMap.

    Discovers examples, tests, skill files, UPGRADE.md, AVM CLI
    availability, provider requirements, and devcontainer configuration.
    Call this after cloning or ingesting a module.

    Args:
        workspace_id: Workspace id from create_workspace.
        module_dir: Directory within the workspace containing the module.

    Returns:
        JSON ModuleMap with all discovered module components.
    """
    module_root = _workspace_path(workspace_id) / module_dir
    if not module_root.is_dir():
        return json.dumps({"error": f"Module directory not found: {module_dir}"})

    # Determine source type from how the module was ingested
    if module_root.is_symlink():
        source_type = "local"
    elif (module_root / ".git").exists():
        source_type = "git"
    else:
        source_type = "local"

    module_map = ModuleMap(
        source_path=str(module_root.resolve()),
        source_type=source_type,
        examples=_scan_examples(module_root),
        tests=_scan_tests(module_root),
        skills=_scan_skills(module_root),
        upgrade_md="UPGRADE.md" if (module_root / "UPGRADE.md").is_file() else None,
        avm_cli=(module_root / "avm").is_file() and os.access(module_root / "avm", os.X_OK),
        providers=_parse_providers(module_root),
        devcontainer_image=_detect_devcontainer_image(module_root),
    )

    return module_map.to_json()


@tool
def read_module_skill(
    workspace_id: str,
    skill_path: str,
    module_dir: str = "module",
) -> str:
    """Read a skill file from the Module Under Test.

    Skills are markdown files in the MUT's .agents/skills/ directory that
    provide domain knowledge (AzAPI patterns, test conventions, etc.).
    The agent reads these at runtime instead of embedding its own copy.

    Args:
        workspace_id: Workspace id from create_workspace.
        skill_path: Relative path within the module (e.g. '.agents/skills/.../AzAPI.md').
        module_dir: Directory within the workspace containing the module.

    Returns:
        JSON with the skill file content.
    """
    module_root = _workspace_path(workspace_id) / module_dir
    full_path = module_root / skill_path

    # Prevent path traversal outside module
    try:
        full_path.resolve().relative_to(module_root.resolve())
    except ValueError:
        return json.dumps({"error": "Path traversal not allowed"})

    if not full_path.is_file():
        return json.dumps({"error": f"Skill file not found: {skill_path}"})

    try:
        content = full_path.read_text()
        if len(content) > 20000:
            content = content[:20000] + "\n\n[... truncated ...]"
        return json.dumps({
            "skill_path": skill_path,
            "content": content,
            "size_bytes": full_path.stat().st_size,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def list_module_examples(
    workspace_id: str,
    module_dir: str = "module",
) -> str:
    """List all examples in a module with their descriptions.

    Scans each example directory for a README.md to extract the
    description, and checks for .tfvars files.

    Args:
        workspace_id: Workspace id from create_workspace.
        module_dir: Directory within the workspace containing the module.

    Returns:
        JSON array of examples with name, description, and tfvars files.
    """
    module_root = _workspace_path(workspace_id) / module_dir
    examples_dir = module_root / "examples"
    if not examples_dir.is_dir():
        return json.dumps({"error": "No examples directory found", "examples": []})

    examples = []
    for ex_dir in sorted(examples_dir.iterdir()):
        if not ex_dir.is_dir() or ex_dir.name.startswith("."):
            continue

        entry: dict = {"name": ex_dir.name}

        # Extract description from README
        readme = ex_dir / "README.md"
        if readme.is_file():
            lines = readme.read_text().splitlines()[:5]
            desc = next((l.lstrip("# ").strip() for l in lines if l.strip()), "")
            entry["description"] = desc

        # List tfvars files
        tfvars = [f.name for f in ex_dir.iterdir() if f.suffix in (".tfvars", ".auto.tfvars")]
        if tfvars:
            entry["tfvars_files"] = tfvars

        # List .tf files
        tf_files = [f.name for f in ex_dir.iterdir() if f.suffix == ".tf"]
        entry["tf_files"] = tf_files

        examples.append(entry)

    return json.dumps(examples, indent=2)
