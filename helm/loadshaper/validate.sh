#!/bin/bash
set -e

CHART_DIR="$(dirname "$0")"
CHART_NAME="loadshaper"

echo "🔍 Validating loadshaper Helm chart..."

# Check if Helm is available
if ! command -v helm &> /dev/null; then
    echo "❌ Helm not found. Please install Helm to run validation."
    exit 1
fi

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Please install kubectl to run validation."
    exit 1
fi

echo "✅ Helm and kubectl found"

# Validate chart syntax
echo "🔍 Validating chart syntax..."
helm lint "$CHART_DIR"
echo "✅ Chart syntax validation passed"

# Test template rendering with default values
echo "🔍 Testing template rendering (default values)..."
helm template test-release "$CHART_DIR" > /dev/null
echo "✅ Default template rendering successful"

# Test template rendering with E2.1.Micro values
echo "🔍 Testing template rendering (E2.1.Micro values)..."
helm template test-release "$CHART_DIR" \
    -f "$CHART_DIR/values-e2-micro.yaml" > /dev/null
echo "✅ E2.1.Micro template rendering successful"

# Test template rendering with A1.Flex values
echo "🔍 Testing template rendering (A1.Flex values)..."
helm template test-release "$CHART_DIR" \
    -f "$CHART_DIR/values-a1-flex.yaml" > /dev/null
echo "✅ A1.Flex template rendering successful"

# Dry-run installation (requires Kubernetes connection)
if kubectl cluster-info &> /dev/null; then
    echo "🔍 Testing dry-run installation..."
    helm install test-release "$CHART_DIR" --dry-run --debug > /dev/null
    echo "✅ Dry-run installation successful"
else
    echo "⚠️  Skipping dry-run installation (no Kubernetes cluster available)"
fi

# Validate specific Oracle Cloud configurations
echo "🔍 Validating Oracle Cloud specific configurations..."

# Check E2.1.Micro has appropriate resource limits
E2_CPU_LIMIT=$(helm template test-release "$CHART_DIR" \
    -f "$CHART_DIR/values-e2-micro.yaml" \
    | grep -A 5 "limits:" | grep "cpu:" | head -1 | awk '{print $2}' || echo "")

if [[ "$E2_CPU_LIMIT" == "200m" ]]; then
    echo "✅ E2.1.Micro CPU limit correctly set to $E2_CPU_LIMIT"
else
    echo "❌ E2.1.Micro CPU limit incorrect: expected 200m, got $E2_CPU_LIMIT"
    exit 1
fi

# Check A1.Flex has ARM architecture selector
A1_ARCH_SELECTOR=$(helm template test-release "$CHART_DIR" \
    -f "$CHART_DIR/values-a1-flex.yaml" \
    | grep -A 2 "nodeSelector:" | grep "kubernetes.io/arch" | awk '{print $2}' || echo "")

if [[ "$A1_ARCH_SELECTOR" == "arm64" ]]; then
    echo "✅ A1.Flex ARM architecture selector correctly set"
else
    echo "❌ A1.Flex ARM architecture selector missing or incorrect"
    exit 1
fi

# Validate required Oracle Cloud environment variables are present
REQUIRED_ENV_VARS=("CPU_TARGET_PCT" "MEM_TARGET_PCT" "NET_TARGET_PCT" "LOAD_THRESHOLD" "NET_LINK_MBIT")

for env_var in "${REQUIRED_ENV_VARS[@]}"; do
    if helm template test-release "$CHART_DIR" | grep -q "$env_var"; then
        echo "✅ Required environment variable $env_var found"
    else
        echo "❌ Required environment variable $env_var missing"
        exit 1
    fi
done

echo ""
echo "🎉 All validations passed!"
echo ""
echo "📦 Chart Summary:"
echo "   Name: $CHART_NAME"
echo "   Location: $CHART_DIR"
echo "   Templates: $(find "$CHART_DIR/templates" -name "*.yaml" -o -name "*.tpl" | wc -l | tr -d ' ')"
echo "   Values files: $(find "$CHART_DIR" -maxdepth 1 -name "values*.yaml" | wc -l | tr -d ' ')"
echo ""
echo "🚀 Ready for deployment!"
echo ""
echo "Usage examples:"
echo "  # Deploy with default values:"
echo "  helm install my-loadshaper $CHART_DIR"
echo ""
echo "  # Deploy for E2.1.Micro:"
echo "  helm install my-loadshaper $CHART_DIR -f $CHART_DIR/values-e2-micro.yaml"
echo ""
echo "  # Deploy for A1.Flex:"
echo "  helm install my-loadshaper $CHART_DIR -f $CHART_DIR/values-a1-flex.yaml"