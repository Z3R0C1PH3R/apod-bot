#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-youtube-apod-rg}"
LOCATION="${AZURE_LOCATION:-eastus}"
SPEECH_NAME="${AZURE_SPEECH_NAME:-youtube-apod-speech}"
ACR_NAME="${AZURE_ACR_NAME:-youtubeapodacr}"
ENV_NAME="${AZURE_CONTAINERAPPS_ENV:-youtube-apod-env}"
JOB_NAME="${AZURE_JOB_NAME:-youtube-apod-daily}"
STORAGE_ACCOUNT="${AZURE_STORAGE_ACCOUNT:-youtubeapodstor}"
STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-videos}"
IMAGE_NAME="youtube-apod:latest"
# Daily at 12:11 PM IST (06:41 UTC)
CRON="${AZURE_CRON:-41 6 * * *}"

require() {
  if [[ -z "${!1:-}" ]]; then
    echo "Missing required env var: $1" >&2
    exit 1
  fi
}

echo "==> Ensuring resource group $RESOURCE_GROUP in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Ensuring Speech Services resource (S0 paid tier)"
if ! az cognitiveservices account show -g "$RESOURCE_GROUP" -n "$SPEECH_NAME" &>/dev/null; then
  az cognitiveservices account create \
    --name "$SPEECH_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --kind SpeechServices \
    --sku S0 \
    --location "$LOCATION" \
    --yes \
    --output none
else
  az cognitiveservices account update \
    --name "$SPEECH_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --sku S0 \
    --output none
fi

SPEECH_KEY="$(az cognitiveservices account keys list -g "$RESOURCE_GROUP" -n "$SPEECH_NAME" --query key1 -o tsv)"
SPEECH_REGION="$LOCATION"

require NASA_API_KEY

echo "==> Ensuring Storage account for public video hosting"
if ! az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" &>/dev/null; then
  az storage account create \
    -g "$RESOURCE_GROUP" \
    -n "$STORAGE_ACCOUNT" \
    -l "$LOCATION" \
    --sku Standard_LRS \
    --allow-blob-public-access true \
    --output none
fi
STORAGE_KEY="$(az storage account keys list -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query '[0].value' -o tsv)"
az storage container create \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY" \
  --name "$STORAGE_CONTAINER" \
  --public-access blob \
  --output none 2>/dev/null || true
STORAGE_CONN="$(az storage account show-connection-string -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query connectionString -o tsv)"
VIDEO_PUBLIC_BASE="https://${STORAGE_ACCOUNT}.blob.core.windows.net/${STORAGE_CONTAINER}"
echo "Public video base URL: $VIDEO_PUBLIC_BASE"

echo "==> Ensuring Azure Container Registry $ACR_NAME"
if ! az acr show -n "$ACR_NAME" &>/dev/null; then
  az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" --sku Basic --output none
fi
az acr update -n "$ACR_NAME" --admin-enabled true --output none

ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)"
ACR_USER="$(az acr credential show -n "$ACR_NAME" --query username -o tsv)"
ACR_PASS="$(az acr credential show -n "$ACR_NAME" --query 'passwords[0].value' -o tsv)"

echo "==> Building and pushing container image via ACR cloud build"
az acr build -r "$ACR_NAME" -t "$IMAGE_NAME" "$ROOT_DIR" --output none

echo "==> Ensuring Container Apps environment"
if ! az containerapp env show -g "$RESOURCE_GROUP" -n "$ENV_NAME" &>/dev/null; then
  az containerapp env create -g "$RESOURCE_GROUP" -n "$ENV_NAME" -l "$LOCATION" --output none
fi

ENV_ID="$(az containerapp env show -g "$RESOURCE_GROUP" -n "$ENV_NAME" --query id -o tsv)"

SECRET_ARGS=(
  "nasa-api-key=$NASA_API_KEY"
  "azure-speech-key=$SPEECH_KEY"
  "azure-storage-conn=$STORAGE_CONN"
)
if [[ -n "${INSTAGRAM_ACCESS_TOKEN:-}" ]]; then
  SECRET_ARGS+=("instagram-access-token=$INSTAGRAM_ACCESS_TOKEN")
fi
if [[ -f token_youtube_v3.pickle ]]; then
  YOUTUBE_TOKEN_B64="$(base64 -w0 token_youtube_v3.pickle)"
  SECRET_ARGS+=("youtube-token-b64=$YOUTUBE_TOKEN_B64")
fi

ENV_ARGS=(
  "NASA_API_KEY=secretref:nasa-api-key"
  "AZURE_SPEECH_KEY=secretref:azure-speech-key"
  "AZURE_SPEECH_REGION=$SPEECH_REGION"
  "AZURE_SPEECH_VOICE=${AZURE_SPEECH_VOICE:-en-US-Andrew:DragonHDLatestNeural}"
  "AZURE_SPEECH_STYLE=${AZURE_SPEECH_STYLE:-documentary-narration}"
  "AZURE_SPEECH_RATE=${AZURE_SPEECH_RATE:-+12%}"
  "AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-conn"
  "AZURE_STORAGE_CONTAINER=$STORAGE_CONTAINER"
  "HEADLESS=1"
)
if [[ -n "${INSTAGRAM_ACCESS_TOKEN:-}" ]]; then
  ENV_ARGS+=("INSTAGRAM_ACCESS_TOKEN=secretref:instagram-access-token")
  ENV_ARGS+=("INSTAGRAM_USER_ID=${INSTAGRAM_USER_ID:-17841467200020320}")
fi
if [[ -f token_youtube_v3.pickle ]]; then
  ENV_ARGS+=("YOUTUBE_TOKEN_B64=secretref:youtube-token-b64")
fi

echo "==> Creating/updating scheduled Container Apps job"
if az containerapp job show -g "$RESOURCE_GROUP" -n "$JOB_NAME" &>/dev/null; then
  az containerapp job secret set -g "$RESOURCE_GROUP" -n "$JOB_NAME" \
    --secrets "${SECRET_ARGS[@]}" \
    --output none
  az containerapp job update -g "$RESOURCE_GROUP" -n "$JOB_NAME" \
    --image "$ACR_LOGIN_SERVER/$IMAGE_NAME" \
    --cpu 2.0 --memory 4Gi \
    --replica-timeout 1800 \
    --replica-retry-limit 1 \
    --cron-expression "$CRON" \
    --replace-env-vars "${ENV_ARGS[@]}" \
    --output none
else
  az containerapp job create -g "$RESOURCE_GROUP" -n "$JOB_NAME" \
    --environment "$ENV_ID" \
    --trigger-type Schedule \
    --cron-expression "$CRON" \
    --replica-timeout 1800 \
    --replica-retry-limit 1 \
    --parallelism 1 \
    --replica-completion-count 1 \
    --image "$ACR_LOGIN_SERVER/$IMAGE_NAME" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_USER" \
    --registry-password "$ACR_PASS" \
    --cpu 2.0 --memory 4Gi \
    --secrets "${SECRET_ARGS[@]}" \
    --env-vars "${ENV_ARGS[@]}" \
    --output none
fi

echo
echo "Deployment complete."
echo "Resource group: $RESOURCE_GROUP"
echo "Speech service: $SPEECH_NAME ($SPEECH_REGION, S0)"
echo "Video hosting:  $VIDEO_PUBLIC_BASE/"
echo "Scheduled job:  $JOB_NAME (cron: $CRON)"
echo
echo "Run once now:"
echo "  az containerapp job start -g $RESOURCE_GROUP -n $JOB_NAME"
echo
echo "Tail logs:"
echo "  az containerapp job logs show -g $RESOURCE_GROUP -n $JOB_NAME --follow"
