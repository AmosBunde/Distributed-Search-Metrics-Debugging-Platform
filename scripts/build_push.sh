#!/usr/bin/env bash
# Build every service image and push it to one cloud registry.
#
# The registry is chosen by which variable is set, so the same target works on
# all three clouds without a --cloud flag to get wrong:
#
#   make build-push AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1
#   make build-push ACR_NAME=searchmetricsacr
#   make build-push GCP_PROJECT=my-project GCP_REGION=us-central1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

SERVICES=(telemetry-collector metrics-engine debug-service api-gateway query-simulator dashboard)
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"

if [[ -n "${AWS_ACCOUNT_ID:-}" ]]; then
    REGION="${AWS_REGION:-us-east-1}"
    REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/search-metrics"
    echo "→ Amazon ECR: ${REGISTRY}"
    aws ecr get-login-password --region "$REGION" \
        | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
    for service in "${SERVICES[@]}"; do
        aws ecr describe-repositories --region "$REGION" --repository-names "search-metrics/${service}" >/dev/null 2>&1 \
            || aws ecr create-repository --region "$REGION" --repository-name "search-metrics/${service}" >/dev/null
    done
elif [[ -n "${ACR_NAME:-}" ]]; then
    REGISTRY="${ACR_NAME}.azurecr.io/search-metrics"
    echo "→ Azure Container Registry: ${REGISTRY}"
    az acr login --name "$ACR_NAME"
elif [[ -n "${GCP_PROJECT:-}" ]]; then
    REGION="${GCP_REGION:-us-central1}"
    REGISTRY="${REGION}-docker.pkg.dev/${GCP_PROJECT}/search-metrics"
    echo "→ Artifact Registry: ${REGISTRY}"
    gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
else
    cat >&2 <<'USAGE'
No registry configured. Set exactly one of:

  make build-push AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1
  make build-push ACR_NAME=searchmetricsacr
  make build-push GCP_PROJECT=my-project GCP_REGION=us-central1
USAGE
    exit 1
fi

echo "Tag: ${TAG}"
echo

for service in "${SERVICES[@]}"; do
    image="${REGISTRY}/${service}:${TAG}"
    echo "── building ${service}"
    docker build -f "services/${service}/Dockerfile" -t "$image" .
    docker push "$image"
done

echo
echo "Pushed ${#SERVICES[@]} images at tag ${TAG}. Deploy with:"
echo
echo "  helm upgrade --install search-metrics ./helm \\"
echo "    --namespace search-metrics --create-namespace \\"
echo "    --values helm/values-<cloud>.yaml \\"
echo "    --set image.registry=${REGISTRY} --set image.tag=${TAG}"
