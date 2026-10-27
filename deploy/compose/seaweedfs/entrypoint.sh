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
# Anonymous requests are refused. The S3 port (8333) is only published inside the compose
# network; Copycast proxies every byte it serves (docs/adr/0019).
set -eu

: "${COPYCAST_S3_ACCESS_KEY:?set COPYCAST_S3_ACCESS_KEY}"
: "${COPYCAST_S3_SECRET_KEY:?set COPYCAST_S3_SECRET_KEY}"
BUCKET="${COPYCAST_S3_BUCKET:-copycast}"
VOLUME_MAX="${COPYCAST_S3_VOLUME_MAX:-64}"
VOLUME_SIZE_MB="${COPYCAST_S3_VOLUME_SIZE_MB:-1024}"

mkdir -p /etc/seaweedfs
umask 077
printf '{"identities":[{"name":"copycast","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
  "$COPYCAST_S3_ACCESS_KEY" "$COPYCAST_S3_SECRET_KEY" > /etc/seaweedfs/s3.json

weed server -s3 -s3.config=/etc/seaweedfs/s3.json -dir=/data -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB="$VOLUME_SIZE_MB" -volume.max="$VOLUME_MAX" &
server=$!
trap 'kill -TERM "$server" 2>/dev/null; wait "$server"' TERM INT

# The bucket: created once, through the master (the gateway refuses anonymous calls).
until echo "s3.bucket.list" | weed shell -master=localhost:9333 >/tmp/buckets 2>/dev/null; do
  sleep 1
done
if ! grep -qw "$BUCKET" /tmp/buckets; then
  echo "s3.bucket.create -name $BUCKET" | weed shell -master=localhost:9333
fi
touch /tmp/ready

wait "$server"
