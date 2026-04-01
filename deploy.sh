#!/bin/bash

# SoManyLemons MCP Server - Deployment Script
# Usage: ./deploy.sh <environment> [--yes]
#
# Examples:
#   ./deploy.sh qas          # Deploy to QAS (with confirmation)
#   ./deploy.sh prod --yes   # Deploy to production (skip confirmation)

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log() {
    local level="$1"; shift
    local ts
    ts="$(date +%H:%M:%S)"
    case "$level" in
        INFO)    echo -e "${BLUE}[$ts]${NC} $*" ;;
        WARN)    echo -e "${YELLOW}[$ts] $*${NC}" ;;
        ERROR)   echo -e "${RED}[$ts] $*${NC}" ;;
        SUCCESS) echo -e "${GREEN}[$ts] $*${NC}" ;;
    esac
}

# ─── Parse arguments ─────────────────────────────────────────────────────────
ENV="${1:-}"
AUTO_YES=false

for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
    esac
done

if [[ -z "$ENV" || "$ENV" == --* ]]; then
    echo "Usage: ./deploy.sh <environment> [--yes]"
    echo "  Environments: qas, prod"
    exit 1
fi

# ─── Load config ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/deploy-config.json"

if [[ ! -f "$CONFIG_FILE" ]]; then
    log ERROR "Config file not found: $CONFIG_FILE"
    exit 1
fi

# Read config values
PROJECT_ID=$(jq -r ".environments.${ENV}.project_id // empty" "$CONFIG_FILE")
SERVICE_NAME=$(jq -r ".environments.${ENV}.service_name // empty" "$CONFIG_FILE")
API_URL=$(jq -r ".environments.${ENV}.api_url // empty" "$CONFIG_FILE")
REGISTRY=$(jq -r ".environments.${ENV}.artifact_registry // empty" "$CONFIG_FILE")
REGION=$(jq -r ".defaults.region" "$CONFIG_FILE")

# Resources: env-specific overrides on top of defaults
MEMORY=$(jq -r "(.defaults.resources.memory) as \$d | .environments.${ENV}.resources.memory // \$d" "$CONFIG_FILE")
CPU=$(jq -r "(.defaults.resources.cpu) as \$d | .environments.${ENV}.resources.cpu // \$d" "$CONFIG_FILE")
CONCURRENCY=$(jq -r "(.defaults.resources.concurrency) as \$d | .environments.${ENV}.resources.concurrency // \$d" "$CONFIG_FILE")
TIMEOUT=$(jq -r "(.defaults.resources.timeout) as \$d | .environments.${ENV}.resources.timeout // \$d" "$CONFIG_FILE")
MIN_INSTANCES=$(jq -r "(.defaults.resources.min_instances) as \$d | .environments.${ENV}.resources.min_instances // \$d" "$CONFIG_FILE")
MAX_INSTANCES=$(jq -r "(.defaults.resources.max_instances) as \$d | .environments.${ENV}.resources.max_instances // \$d" "$CONFIG_FILE")

if [[ -z "$PROJECT_ID" || -z "$SERVICE_NAME" ]]; then
    log ERROR "Unknown environment: $ENV"
    exit 1
fi

IMAGE="${REGISTRY}/${SERVICE_NAME}:latest"

# ─── Confirmation ────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BLUE}SoManyLemons MCP Server Deploy${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
log INFO "Environment:  ${GREEN}${ENV}${NC}"
log INFO "Project:      ${PROJECT_ID}"
log INFO "Service:      ${SERVICE_NAME}"
log INFO "Region:       ${REGION}"
log INFO "API URL:      ${API_URL}"
log INFO "Image:        ${IMAGE}"
log INFO "Resources:    ${MEMORY} / ${CPU} CPU / ${MIN_INSTANCES}-${MAX_INSTANCES} instances"
echo ""

if [[ "$AUTO_YES" != true ]]; then
    read -p "Deploy to ${ENV}? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        log WARN "Cancelled."
        exit 0
    fi
fi

DEPLOY_START=$SECONDS

# ─── Step 1: Set GCP project ────────────────────────────────────────────────
log INFO "Setting GCP project to ${PROJECT_ID}..."
gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 2: Build and push image ───────────────────────────────────────────
log INFO "Building and pushing Docker image..."
gcloud builds submit \
    --tag "$IMAGE" \
    --project "$PROJECT_ID" \
    --quiet

log SUCCESS "Image pushed: ${IMAGE}"

# ─── Step 3: Deploy to Cloud Run ────────────────────────────────────────────
log INFO "Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --platform managed \
    --allow-unauthenticated \
    --memory "$MEMORY" \
    --cpu "$CPU" \
    --concurrency "$CONCURRENCY" \
    --timeout "$TIMEOUT" \
    --min-instances "$MIN_INSTANCES" \
    --max-instances "$MAX_INSTANCES" \
    --set-env-vars "SML_API_URL=${API_URL}" \
    --port 8080 \
    --quiet

# ─── Step 4: Verify ─────────────────────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --format 'value(status.url)')

log INFO "Checking health..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${SERVICE_URL}/health" || echo "000")

if [[ "$HTTP_STATUS" == "200" ]]; then
    log SUCCESS "Health check passed"
else
    log WARN "Health check returned ${HTTP_STATUS} (service may still be starting)"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
ELAPSED=$(( SECONDS - DEPLOY_START ))
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${GREEN}Deploy complete (${ELAPSED}s)${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
log SUCCESS "Service URL:  ${SERVICE_URL}"
log SUCCESS "SSE endpoint: ${SERVICE_URL}/sse"
log SUCCESS "Health:       ${SERVICE_URL}/health"
echo ""
log INFO "Map your domain to this service:"
log INFO "  gcloud run domain-mappings create --service=${SERVICE_NAME} --domain=mcp.somanylemons.com --region=${REGION}"
echo ""
