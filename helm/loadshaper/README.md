# loadshaper Helm Chart

[![Oracle Cloud](https://img.shields.io/badge/Oracle%20Cloud-F80000?style=for-the-badge&logo=oracle&logoColor=white)](https://cloud.oracle.com)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326ce5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io)
[![Helm](https://img.shields.io/badge/Helm-0F1689?style=for-the-badge&logo=helm&logoColor=white)](https://helm.sh)

Oracle Cloud Always Free VM Keeper - Intelligent baseline load generator that prevents Oracle Cloud Always Free compute instances from being reclaimed due to underutilization.

## Overview

This Helm chart deploys `loadshaper` on a Kubernetes cluster. `loadshaper` is designed to prevent Oracle Cloud Always Free compute instances from being reclaimed by intelligently maintaining resource utilization above Oracle's thresholds while remaining completely unobtrusive to legitimate workloads.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- Oracle Cloud Always Free compute instances (E2.1.Micro or A1.Flex)

## Installation

### Quick Start

```bash
# Add the repository (when available)
helm repo add loadshaper https://charts.loadshaper.io
helm repo update

# Install with default values
helm install my-loadshaper loadshaper/loadshaper

# Or install from local chart
helm install my-loadshaper ./helm/loadshaper
```

### Oracle Cloud Shape-Specific Deployments

#### E2.1.Micro Shape
```bash
helm install my-loadshaper ./helm/loadshaper \
  -f ./helm/loadshaper/values-e2-micro.yaml \
  --set config.NET_PEERS="10.0.1.10,10.0.1.11"
```

#### A1.Flex Shape
```bash
helm install my-loadshaper ./helm/loadshaper \
  -f ./helm/loadshaper/values-a1-flex.yaml \
  --set config.NET_PEERS="10.0.1.10,10.0.1.11" \
  --set resources.limits.cpu="4000m" \
  --set resources.limits.memory="8Gi"
```

## Configuration

### Values Files

The chart includes shape-specific values files optimized for Oracle Cloud compute shapes:

- `values.yaml`: Default configuration suitable for general use
- `values-e2-micro.yaml`: Optimized for VM.Standard.E2.1.Micro (1/8 OCPU, 1GB RAM, 50 Mbps)
- `values-a1-flex.yaml`: Optimized for A1.Flex (ARM64, up to 4 vCPU, 24GB RAM, 1 Gbps per vCPU)

### Key Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of loadshaper replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/senomorf/loadshaper` |
| `image.tag` | Container image tag | `""` (uses Chart.AppVersion) |
| `config.CPU_TARGET_PCT` | Target CPU utilization percentage | `"30.0"` |
| `config.MEM_TARGET_PCT` | Target memory utilization percentage | `"60.0"` |
| `config.NET_TARGET_PCT` | Target network utilization percentage | `"10.0"` |
| `config.NET_PEERS` | Comma-separated list of peer IPs for network load | `""` |
| `persistence.enabled` | Enable persistent storage for metrics | `true` |
| `persistence.size` | Size of persistent volume | `1Gi` |

### Oracle Cloud Shape Optimization

#### E2.1.Micro Configuration
- **CPU**: Conservative 25% target for burstable 1/8 OCPU
- **Memory**: 40% target (no memory reclamation rule)
- **Network**: 25% of 50 Mbps external bandwidth (≈12.5 Mbps)
- **Resources**: Limited to 200m CPU and 256Mi memory

#### A1.Flex Configuration
- **CPU**: 35% target for ARM processors
- **Memory**: 45% target (**critical**: reduced to stay well below 80% threshold for reclamation rule)
- **Network**: 15% of 1 Gbps per vCPU
- **Resources**: Scalable based on instance size

### Complete Configuration Reference

```yaml
# Core application settings
replicaCount: 1
image:
  repository: ghcr.io/senomorf/loadshaper
  tag: ""
  pullPolicy: IfNotPresent

# Resource utilization targets
config:
  CPU_TARGET_PCT: "30.0"      # Target CPU utilization %
  MEM_TARGET_PCT: "60.0"      # Target memory utilization %
  NET_TARGET_PCT: "10.0"      # Target network utilization %
  
  # Safety thresholds
  CPU_STOP_PCT: "85.0"        # Emergency stop CPU threshold
  MEM_STOP_PCT: "90.0"        # Emergency stop memory threshold
  NET_STOP_PCT: "60.0"        # Emergency stop network threshold
  
  # Load monitoring
  LOAD_THRESHOLD: "0.6"       # Pause when load avg per core exceeds
  LOAD_RESUME_THRESHOLD: "0.4" # Resume when load avg per core drops below
  
  # Network configuration
  NET_PEERS: ""               # Comma-separated peer IPs for network load
  NET_LINK_MBIT: "1000.0"     # Network interface speed in Mbps

# Kubernetes resources
resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 100m
    memory: 128Mi

# Storage for 7-day metrics
persistence:
  enabled: true
  size: 1Gi
  storageClass: ""

# Monitoring integration
serviceMonitor:
  enabled: false
  interval: 30s
  scrapeTimeout: 10s
  port: 8080

# Security
networkPolicy:
  enabled: false
  
podSecurityContext:
  fsGroup: 2000
  
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
```

## Monitoring

### Prometheus Integration

Enable Prometheus monitoring with ServiceMonitor:

```yaml
serviceMonitor:
  enabled: true
  namespace: monitoring
  interval: 30s
  labels:
    prometheus: kube-prometheus
```

### Health Checks

The chart includes comprehensive health checks:

- **Liveness Probe**: Verifies loadshaper process is running
- **Readiness Probe**: Checks metrics database availability

## Security

### NetworkPolicy

Enable network policies for enhanced security:

```yaml
networkPolicy:
  enabled: true
```

Default policies allow:
- Inter-pod communication for iperf traffic
- DNS resolution
- Restricted external internet access for Oracle threshold compliance (iperf-servers and load-testing namespaces)
- Prometheus scraping (if ServiceMonitor enabled)

### Security Context

Runs with non-root user and appropriate security context:
- `runAsNonRoot: true`
- `runAsUser: 1000`
- `fsGroup: 2000`

## Troubleshooting

### Common Issues

1. **Pod not starting**: Check resource constraints and node capacity
2. **Metrics not persisting**: Verify persistent volume and storage class
3. **Network load not working**: Ensure NET_PEERS is configured correctly
4. **High system load**: Verify LOAD_THRESHOLD settings

### Debugging Commands

```bash
# Check pod status
kubectl get pods -l app.kubernetes.io/name=loadshaper

# View logs
kubectl logs -l app.kubernetes.io/name=loadshaper

# Check configuration
kubectl get configmap -l app.kubernetes.io/name=loadshaper -o yaml

# Verify persistent volume
kubectl get pvc -l app.kubernetes.io/name=loadshaper

# Check metrics (if ServiceMonitor enabled)
kubectl port-forward svc/loadshaper 8080:8080
curl http://localhost:8080/metrics
```

### Metrics Verification

Look for telemetry lines in logs showing 95th percentile compliance:
```bash
kubectl logs -l app.kubernetes.io/name=loadshaper | grep "\[loadshaper\]" | tail -10
```

## Uninstallation

```bash
helm uninstall my-loadshaper
```

**Note**: Persistent volumes are not automatically deleted. Remove manually if needed:
```bash
kubectl delete pvc -l app.kubernetes.io/instance=my-loadshaper
```

## Advanced Configuration

### Multi-Instance Network Load Generation

Deploy multiple instances for inter-pod network traffic:

```yaml
replicaCount: 3
config:
  NET_PEERS: "loadshaper-0.loadshaper.default.svc.cluster.local,loadshaper-1.loadshaper.default.svc.cluster.local"
service:
  type: ClusterIP
  enabled: true
```

### Custom Resource Scheduling

Pin to specific nodes or shapes:

```yaml
nodeSelector:
  oracle.com/shape: VM.Standard.E2.1.Micro

affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
      - matchExpressions:
        - key: oracle.com/shape
          operator: In
          values:
          - A1.Flex

tolerations:
- key: oracle.com/always-free
  operator: Equal
  value: "true"
  effect: NoSchedule
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](../../LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/senomorf/loadshaper/issues)
- **Documentation**: [Project README](../../README.md)
- **Contributing**: [Contributing Guide](../../CONTRIBUTING.md)