# 0003 UI, API and MCP share one capability contract

Status: accepted (v1)

## Context

v1 has three consumers of the same functionality: the React UI, the HTTP API and an MCP
server for agents. Three hand-maintained surfaces drift: a feature lands in the UI but not
in MCP, error shapes differ, an agent sees a parameter the UI never sends.

## Decision

Every user-facing operation is a service function registered in `CAPABILITIES` with a name,
a request model and a response model (`application/capabilities.py`, models in
`application/models.py`). A route is a thin wrapper that sets `operation_id` and
`x-capability` to the name; a tool is a thin wrapper that sets `meta.capability` to the
name and takes the identical request model. A convergence test fails when any capability
lacks its route, when any non-exempt capability lacks its tool, or when a route or tool
names an unregistered capability. The UI consumes routes by capability name through a
generated OpenAPI client and a checked operations map; it never composes URLs.

Explicitly exempt from tools: `rebuild`, `archive_item`, `get_request`, `list_requests`
(agents use `archive_episodes` and `add_to_inbox` instead). Internal capabilities
(`record_download`, `subscribe_events`, `ensure_default_inbox`, `resolve_inbox`) have no tool.

## Consequences

- Adding a capability is a checklist (see AGENTS.md) and the tests enforce it.
- Errors have one vocabulary: domain exceptions map to RFC 9457 problem+json in the API and
  to `ToolError` in MCP.
- `web/openapi.json` is committed and `make openapi-check` fails when it is stale.
