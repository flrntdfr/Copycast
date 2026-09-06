# 0005 Modular monolith, two processes, one image

Status: accepted (v1)

## Context

The work splits into request handling (UI, API, MCP, feeds, media) and long-running engine
jobs (listing, downloading, pruning). Downloads must not compete with serving feeds, a
crashed download must not take the UI down, and graceful shutdown means different things
for each. Yet one operator runs it all and does not want a fleet of services.

## Decision

One Python package structured as ports and adapters: `domain` (pure rules), `application`
(models, capabilities, services, ports, events), `adapters` (api, mcp, db, storage, engine,
sources, feeds, assets), `worker`. Import-linter contracts keep the layers honest. One
container image with two entry points: `copycast api` (uvicorn: UI, API, MCP, feeds, SSE)
and `copycast worker` (job runner, scheduler, engine). They share Postgres, the data
directory and the composition root (`build_container`). The worker is a singleton guarded by
a session-level advisory lock; the api may scale when the data volume is shared.

Postgres is also the message bus: jobs are claimed with `FOR UPDATE SKIP LOCKED` and events
travel over `NOTIFY` to the api's SSE hub. No broker.

## Consequences

- Compose and Kubernetes deploy the same image twice with different arguments.
- A rebuild of the database is a worker job under the worker lock; the CLI form refuses to
  run while the worker is up.
- High availability is active-passive by definition; the Kubernetes worker uses the Recreate
  strategy and is co-located with the api pod for a ReadWriteOnce volume.
