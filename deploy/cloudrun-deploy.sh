#!/usr/bin/env bash
# Build the paperpipe web UI image and deploy it to Cloud Run with a
# GCS-backed volume mounted at /data, so the SQLite corpus survives
# restarts/redeploys.
#
# Prerequisites (one-time, run as a user/SA with enough IAM to set this up):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#       artifactregistry.googleapis.com storage.googleapis.com \
#       --project "$PROJECT_ID"
#
# The runtime service account (SERVICE_ACCOUNT below) needs, at minimum:
#   roles/storage.objectAdmin   on the data bucket (read/write via GCS FUSE)
# The identity running this script needs roughly:
#   roles/run.admin, roles/iam.serviceAccountUser (on SERVICE_ACCOUNT),
#   roles/artifactregistry.writer, roles/cloudbuild.builds.editor,
#   roles/storage.admin (to create the bucket once)
#
# SQLite has a single-writer model and GCS FUSE does not give real file
# locking across instances, so this deploys with --max-instances=1. That's
# fine for personal/low-traffic use; it is not a multi-instance setup.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-personal-tools-isotopes55}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-paper-pipeline}"
REPO_NAME="${REPO_NAME:-paper-pipeline}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-paper-pipeline-data}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-paper-pipeline-deploy@${PROJECT_ID}.iam.gserviceaccount.com}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"

echo "Project:         $PROJECT_ID"
echo "Region:          $REGION"
echo "Service:         $SERVICE_NAME"
echo "Image:           $IMAGE"
echo "Data bucket:     $BUCKET_NAME"
echo "Service account: $SERVICE_ACCOUNT"
echo

# 1. Artifact Registry repo for the image (idempotent).
gcloud artifacts repositories describe "$REPO_NAME" \
    --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO_NAME" \
    --project "$PROJECT_ID" --location "$REGION" \
    --repository-format=docker \
    --description="paper-pipeline images"

# 2. GCS bucket for persistent /data (idempotent).
gcloud storage buckets describe "gs://${BUCKET_NAME}" \
    --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project "$PROJECT_ID" --location "$REGION" \
    --uniform-bucket-level-access

# 3. Let the runtime service account read/write the bucket.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --project "$PROJECT_ID" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/storage.objectAdmin"

# 4. Build the image with Cloud Build (no local Docker needed).
gcloud builds submit "$(dirname "$0")/.." \
    --project "$PROJECT_ID" \
    --tag "$IMAGE"

# 5. Deploy, mounting the bucket at /data.
gcloud run deploy "$SERVICE_NAME" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "$IMAGE" \
    --service-account "$SERVICE_ACCOUNT" \
    --execution-environment gen2 \
    --add-volume "name=data,type=cloud-storage,bucket=${BUCKET_NAME}" \
    --add-volume-mount "volume=data,mount-path=/data" \
    --max-instances 1 \
    --min-instances 0 \
    --port 8080 \
    --no-allow-unauthenticated

echo
echo "Deployed (private - requires an identity token to invoke)."
echo "URL:"
echo "  gcloud run services describe $SERVICE_NAME --project $PROJECT_ID --region $REGION --format='value(status.url)'"
echo
echo "To let yourself call it:"
echo "  gcloud run services add-iam-policy-binding $SERVICE_NAME --project $PROJECT_ID --region $REGION \\"
echo "      --member=user:YOUR_EMAIL --role=roles/run.invoker"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" <service-url>"
echo
echo "To make it public instead (exposes fetch/extract/index/export and your corpus to anyone):"
echo "  gcloud run services add-iam-policy-binding $SERVICE_NAME --project $PROJECT_ID --region $REGION \\"
echo "      --member=allUsers --role=roles/run.invoker"
