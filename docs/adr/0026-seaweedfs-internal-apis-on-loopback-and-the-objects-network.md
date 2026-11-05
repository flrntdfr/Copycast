# 0026 SeaweedFS keeps its internal APIs on loopback; only the key holders reach its gateway

Status: accepted (2.3.0); amends ADR 0019 (how the compose stack runs SeaweedFS)

## Context

ADR 0019 ships SeaweedFS in the compose stack with "one identity from the environment,
anonymous access refused". That holds for the S3 gateway only. `weed server` also runs a
master (9333, gRPC 19333), a volume server (8080, gRPC 18080) and a filer (8888, gRPC 18888),
and in the 2.2 entrypoint they all listened on every interface of the container without a
credential. The filer alone lists and serves every object of every Feed (SEC-2). Any
container on the compose network could reach them, and so could the api itself on behalf of a
Source URL such as `http://seaweedfs:8888/` until the fetch guard (ADR 0023).

The plan asked to bind master, volume and filer to the compose network only. Testing
SeaweedFS 4.48 turned up a second hole. The S3 gateway's own gRPC port (18333) serves
`SeaweedS3IamCache.PutIdentity`, `PutPolicy` and lifecycle deletes with no credential, and one
`PutIdentity` call grants a working Admin key. No flag binds that port apart from the
gateway's HTTP port. A busybox `nc` relay in front of a loopback-only gateway was built and
tested, then dropped on 2026-11-05: the operator chose not to run a hand-made relay in the
data path.

## Decision

**The entrypoint** (`deploy/compose/seaweedfs/entrypoint.sh`) starts `weed server` with
`-ip=127.0.0.1 -ip.bind=127.0.0.1`. Master, volume server and filer listen on, and advertise,
loopback only: nothing outside the container reaches them. The S3 gateway listens on the
container network (`-s3.ip.bind=0.0.0.0`) on its usual ports, 8333 (S3 over HTTP, which
refuses anonymous requests) and 18333 (its gRPC). The Iceberg and Lance listeners are off
(`-s3.port.iceberg=0 -s3.port.lance=0`) and the master sends no telemetry
(`-master.telemetry=false`). The bucket bootstrap talks to the master on `127.0.0.1:9333`,
and the container is ready once the gateway answers.

**The network is the boundary for 18333.** In `docker-compose.yml`, SeaweedFS sits only on an
internal network, `objects`, that it shares with the api's network namespace (the `tailscale`
sidecar, or `api-direct`) and the worker. Those already hold the S3 keys. SeaweedFS is not on
`default`, publishes no port and has no egress. Postgres and Valkey are not on `objects`. The
development compose file publishes S3 on `127.0.0.1` only.

## Consequences

- A container that does not hold the keys (Postgres, Valkey, a reverse proxy sharing the
  default network) cannot reach SeaweedFS at all. Anything on `objects` can still reach 18333
  and add an identity, and so can the Docker host itself, which routes to every bridge,
  internal ones included. Both already hold the keys or the volume. gRPC mTLS through
  SeaweedFS's `security.toml` is the follow-up that closes 18333 itself. The image ships no
  `openssl` to mint the certificates, so it needs a step of its own.
- A one-off S3 client (a backup sync) joins `<project>_objects`. To reach an off-site target
  it also joins `<project>_default`, because `objects` has no egress.
- The api runs in the `tailscale` sidecar's network namespace, so the sidecar carries the
  api's networks. Recreating the sidecar alone leaves the api in a deleted namespace with no
  network: recreate the api with it, as a plain `docker compose up -d` does.
- A production compose file other than the repository's needs the same changes by hand
  (docs/operations.md, "Upgrading to 2.3.0"). The first start after the switch takes about
  10 s longer while the master re-elects itself under 127.0.0.1. The data volume needs no
  migration.
- Operators who skip the network part still get the loopback binds, which close the filer
  and master paths. Only the identity injection on 18333 stays open to every container on
  the compose network.
- Tests: the `s3` testcontainer runs this entrypoint and checks which sockets it leaves
  beyond loopback (`tests/integration/storage/test_s3.py`); `tests/unit/test_compose.py`
  pins the bind flags and the network layout.
