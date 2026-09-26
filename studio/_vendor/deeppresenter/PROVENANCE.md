# Restricted DeepPresenter fork

Source: https://github.com/icip-cas/PPTAgent

Pinned tag: `v1.1.38`; commit: `2e68c095a86bdbb91635dc4d91dad4662aba163c`.
Upstream's package metadata at this tag says 1.1.37; the commit is authoritative.
License: MIT, preserved in LICENSE.

Vendored files: agents/agent.py, agents/design.py, utils/typings.py,
utils/constants.py. Imports are namespaced under studio._vendor.deeppresenter.
AgentEnv is an annotation alias, not an import of upstream Docker/MCP machinery.
Empty package initializers are Studio-owned. utils/config.py and utils/log.py
are explicit Studio compatibility layers: injected model client, strict JSON,
no content/reasoning logging. No upstream endpoint configuration is used.

The upstream Design.action/execute/loop runs against Studio's restricted tool
environment. Only one validated tool call per turn is admitted by the bridge.
Context folding and history saving are disabled. The request is a minimal typed
duck object containing only designagent_prompt (including the actual canvas).
InputRequest.copy_to_workspace, MCPServer, upstream clients and container tools
are not called. No upstream default 16:9 ratio is imposed on the uploaded deck.

This is NOT the full, unmodified DeepPresenter product. Research, shell, network
tools, free-form HTML/JavaScript authoring, VLM reflection and old PPTAgent eval
are not integrated. Studio keeps the native template-aware editable renderer.
Quality of the complete upstream product is not implied by this integration.
