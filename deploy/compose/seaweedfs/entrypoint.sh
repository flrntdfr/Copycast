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
# Nothing SeaweedFS runs listens beyond loopback. The master (9333/19333), the volume server
# (8080/18080) and the filer (8888/18888) check no credential (the filer lists and serves every
# object), and neither does the S3 gateway's gRPC port: in 4.48 it serves
# SeaweedS3IamCache.PutIdentity, so one call adds an Admin identity. No flag splits that port
# from the gateway's bind address, so the whole process binds to and advertises 127.0.0.1: the
# gateway on 8334, its gRPC on 18334. A busybox nc relay is the only network listener: it
# carries 8333 to the gateway, so the network (another container, the host, anything on a
# Docker bridge) meets S3 over HTTP, which refuses anonymous requests, and nothing else. The
# relay or the server ending ends the container, and the restart policy brings both back.
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
  -s3 -s3.config=/etc/seaweedfs/s3.json -s3.ip.bind=127.0.0.1 -s3.port=8334 -s3.port.grpc=18334 \
  -s3.port.iceberg=0 -s3.port.lance=0 \
  -master.volumeSizeLimitMB="$VOLUME_SIZE_MB" -volume.max="$VOLUME_MAX" &
server=$!
# One nc per connection, each dialling the gateway on loopback.
nc -lk -p 8333 -e nc 127.0.0.1 8334 &
relay=$!
trap 'kill -TERM "$server" "$relay" 2>/dev/null || true; wait "$server" || true; exit 0' TERM INT

# The bucket: created once, through the master (the gateway refuses anonymous calls).
until echo "s3.bucket.list" | weed shell -master=127.0.0.1:9333 >/tmp/buckets 2>/dev/null; do
  sleep 1
done
if ! grep -qw "$BUCKET" /tmp/buckets; then
  echo "s3.bucket.create -name $BUCKET" | weed shell -master=127.0.0.1:9333
fi
# Ready once the gateway answers on loopback (the relay dials it per connection).
until nc -z 127.0.0.1 8334 && nc -z 127.0.0.1 8333; do
  sleep 1
done
touch /tmp/ready

# busybox's `wait -n` misses a job killed by a signal: poll both instead. The sleep runs in
# the background so that a TERM interrupts the wait at once.
while kill -0 "$server" 2>/dev/null && kill -0 "$relay" 2>/dev/null; do
  sleep 2 &
  wait $! || true
done
echo "entrypoint: weed server or the S3 relay stopped; stopping the container" >&2
kill -TERM "$server" "$relay" 2>/dev/null || true
wait "$server" 2>/dev/null || true
exit 1
