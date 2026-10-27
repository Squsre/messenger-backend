#!/bin/sh
# Idempotent MinIO initialization for the development Compose stack.
#
# Creates the private media bucket and a least-privilege application user. The
# application never receives the MinIO root credentials. No secret is printed.
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required}"
: "${MINIO_BUCKET:?MINIO_BUCKET is required}"

ALIAS=messenger
POLICY_NAME=messenger-media
POLICY_FILE=/tmp/messenger-media-policy.json

mc alias set "$ALIAS" "http://minio:9000" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

# Compose waits for the MinIO healthcheck, but keep this script usable on its own.
attempt=0
until mc admin info "$ALIAS" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "MinIO did not become ready in time." >&2
        exit 1
    fi
    sleep 1
done

mc mb --ignore-existing "$ALIAS/$MINIO_BUCKET" >/dev/null
mc anonymous set none "$ALIAS/$MINIO_BUCKET" >/dev/null

cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET/*"]
    }
  ]
}
EOF

if ! mc admin policy info "$ALIAS" "$POLICY_NAME" >/dev/null 2>&1; then
    mc admin policy create "$ALIAS" "$POLICY_NAME" "$POLICY_FILE" >/dev/null
fi

# Recreate the application user when its password changed in .env. This keeps
# repeated `docker compose up` runs deterministic without exposing the value.
if ! mc admin user add "$ALIAS" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; then
    mc admin user remove "$ALIAS" "$MINIO_ACCESS_KEY" >/dev/null 2>&1 || true
    mc admin user add "$ALIAS" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
fi

mc admin policy attach "$ALIAS" "$POLICY_NAME" --user "$MINIO_ACCESS_KEY" >/dev/null 2>&1 || true

echo "MinIO bucket '$MINIO_BUCKET' and application policy are ready."
