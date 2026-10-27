#!/bin/sh
# SeaweedFS for Copycast's Object store: one process (master, volume, filer, S3 gateway), one
# identity from the environment, the bucket created on first start.
#
#   COPYCAST_S3_ACCESS_KEY / COPYCAST_S3_SECRET_KEY   the only key pair the gateway accepts
#   COPYCAST_S3_BUCKET                                 created when absent (default: copycast)
#
# Anonymous requests are refused. The S3 port (8333) is only published inside the compose
# network; Copycast proxies every byte it serves (docs/adr/0019).
set -eu

: "${COPYCAST_S3_ACCESS_KEY:?set COPYCAST_S3_ACCESS_KEY}"
: "${COPYCAST_S3_SECRET_KEY:?set COPYCAST_S3_SECRET_KEY}"
BUCKET="${COPYCAST_S3_BUCKET:-copycast}"

mkdir -p /etc/seaweedfs
umask 077
printf '{"identities":[{"name":"copycast","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
  "$COPYCAST_S3_ACCESS_KEY" "$COPYCAST_S3_SECRET_KEY" > /etc/seaweedfs/s3.json

weed server -s3 -s3.config=/etc/seaweedfs/s3.json -dir=/data -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=1024 -volume.max=0 &
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
