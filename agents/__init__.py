"""Multi-agent orchestration for the Infrastructure Testing Agent.

This package implements a hub-and-spoke agent topology where:

- **Orchestrator** owns the test plan and delegates to specialist agents
- **Discovery** scans the MUT for structure, skills, and examples
- **Deploy** runs terraform init/plan/apply/destroy per example (stateless)
- **Analysis** reviews structured deploy results against UPGRADE.md and skills
- **Reviewer** cross-checks analysis findings with a fresh context
- **Reporter** formats findings and delivers via GitHub

Each agent is a focused ChatAgent with its own instructions and tool subset.
The orchestrator uses the agent-as-tools pattern to delegate work, keeping
each agent's context small and focused.
"""
