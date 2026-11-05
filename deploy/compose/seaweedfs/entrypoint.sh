#!/bin/sh
# SeaweedFS for Copycast's Object store: one process (master, volume, filer, S3 gateway), one
# identity from the environment, the bucket created on first start.
#
#   COPYCAST_S3_ACCESS_KEY / COPYCAST_S3_SECRET_KEY   the only key pair the gateway accepts
#   COPYCAST_S3_BUCKET                                 created when absent (default: copycast)
#   COPYCAST_S3_VOLUME_MAX                             volume files allowed (default: 64)
#   COPYCAST_S3_VOLUME_SIZE_MB                         size a volume file grows to (default: 1024)
#
# Volume files are not preallocated: they grow as they are written, so the count and size
# only cap the store (64 x 1 GiB by default) and the disk stays the real limit. Never 0
# ("auto"): SeaweedFS then sizes the count from the free disk at the first start, a ceiling
# that stays even after space is freed, and its own metadata log runs out of volumes too.
#
# The master (9333/19333), the volume server (8080/18080) and the filer (8888/18888) check no
# credential (the filer lists and serves every object), so they bind to loopback and advertise
# 127.0.0.1 to each other: only this process reaches them. The S3 gateway listens on the
# container network on its usual ports: 8333 (S3 over HTTP, which refuses anonymous requests)
# and 18333 (its gRPC). In SeaweedFS 4.48, 18333 accepts IAM changes without a credential
# (SeaweedS3IamCache.PutIdentity adds an Admin key) and cannot be bound apart from 8333. The
# boundary is therefore the internal compose network "objects" (docker-compose.yml), which holds
# only SeaweedFS, the api (the tailscale sidecar or api-direct) and the worker, and those hold
# the S3 keys already. gRPC mTLS through security.toml is the follow-up that closes 18333 itself.
# The Iceberg and Lance catalogs are off and the master sends no telemetry. Copycast proxies
# every byte it serves (docs/adr/0019).
set -eu

: "${COPYCAST_S3_ACCESS_KEY:?set COPYCAST_S3_ACCESS_KEY}"
: "${COPYCAST_S3_SECRET_KEY:?set COPYCAST_S3_SECRET_KEY}"
BUCKET="${COPYCAST_S3_BUCKET:-copycast}"
VOLUME_MAX="${COPYCAST_S3_VOLUME_MAX:-64}"
VOLUME_SIZE_MB="${COPYCAST_S3_VOLUME_SIZE_MB:-1024}"

rm -f /tmp/ready  # left by an earlier run when the container restarts
mkdir -p /etc/seaweedfs
umask 077
printf '{"identities":[{"name":"copycast","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
  "$COPYCAST_S3_ACCESS_KEY" "$COPYCAST_S3_SECRET_KEY" > /etc/seaweedfs/s3.json

weed server -dir=/data -ip=127.0.0.1 -ip.bind=127.0.0.1 -master.telemetry=false \
  -s3 -s3.config=/etc/seaweedfs/s3.json -s3.ip.bind=0.0.0.0 -s3.port.iceberg=0 -s3.port.lance=0 \
  -master.volumeSizeLimitMB="$VOLUME_SIZE_MB" -volume.max="$VOLUME_MAX" &
server=$!
trap 'kill -TERM "$server" 2>/dev/null || true; wait "$server" || true; exit 0' TERM INT

# The bucket: created once, through the master (the gateway refuses anonymous calls).
until echo "s3.bucket.list" | weed shell -master=127.0.0.1:9333 >/tmp/buckets 2>/dev/null; do
  sleep 1
done
if ! grep -qw "$BUCKET" /tmp/buckets; then
  echo "s3.bucket.create -name $BUCKET" | weed shell -master=127.0.0.1:9333
fi
# Ready once the gateway answers too.
until nc -z 127.0.0.1 8333; do
  sleep 1
done
touch /tmp/ready

wait "$server"
