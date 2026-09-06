# 0004 No authentication: the network is the security boundary

Status: accepted (v1); carries the v0 stance forward

## Context

Copycast is a single-operator, self-hosted service. Podcast clients speak plain RSS and
HTTP and most cannot send credentials; MCP clients are configured by the same operator.
Building accounts, sessions and tokens would add surface without protecting anything the
operator cannot protect better at the network layer.

## Decision

Copycast has no authentication, no CORS policy, no trusted-host check and no CSRF
protection. It is deployed behind a private network: the compose stack runs the api in the
network namespace of a Tailscale sidecar (`tailscale` profile) so it is reachable only inside
the tailnet with a Tailscale-issued certificate; on Kubernetes the Tailscale operator's
Ingress plays the same role. The `direct` profile publishes the port for trusted LANs or an
operator-managed reverse proxy. Tailscale Funnel is explicitly disabled in the served config.

## Consequences

- Never expose the `direct` profile to the internet; the README says so.
- Destructive operations rely on intent, not identity: `delete_feed` over MCP requires
  `confirm=true` and warns that it may delete the only copy.
- Feed URLs contain no secrets; a leaked URL is a network problem, not an application one.
- If a future need for authentication arises it belongs in front of Copycast (Tailscale
  ACLs, a proxy), not inside it.
