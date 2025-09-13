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
| `config.NET_TARGET_PCT` | Target network utilization percentage | `"25.0"` |
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
  NET_TARGET_PCT: "25.0"      # Target network utilization % (above Oracle 20% threshold)
  
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
  # port: Uses config.HEALTH_PORT (8080 by default)

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

### Security Overview

This Helm chart implements comprehensive security hardening following Kubernetes security best practices:

**🔒 Key Security Features:**
- Read-only root filesystem with tmpfs for temporary files
- Non-root execution with dropped capabilities
- Service account token automount disabled by default
- NetworkPolicy enabled by default for traffic isolation
- hostPath mounts are opt-in only (disabled by default)

### NetworkPolicy

Network policies are **enabled by default** for enhanced security:

```yaml
networkPolicy:
  enabled: true              # Enabled by default
  allowDNS: true             # Allow DNS resolution
  allowPeersFromSameApp: true # Allow communication with same app pods
  extraEgress: []            # Additional egress rules for network peers
```

Default policies:
- **Ingress**: Only allows Prometheus scraping when ServiceMonitor is enabled
- **Egress**: DNS resolution + communication with same-app pods only
- **Removed**: Previous overly permissive iperf-server rules (security fix)

### Security Context

Comprehensive security hardening:

```yaml
securityContext:
  capabilities:
    drop: [ALL]                    # Drop all capabilities
  readOnlyRootFilesystem: true     # Read-only root filesystem (security improvement)
  allowPrivilegeEscalation: false  # Prevent privilege escalation
  runAsNonRoot: true              # Non-root execution
  runAsUser: 1000                 # Specific user ID
  runAsGroup: 1000                # Specific group ID
  seccompProfile:
    type: RuntimeDefault          # Default seccomp profile

podSecurityContext:
  fsGroup: 2000
  fsGroupChangePolicy: OnRootMismatch  # Efficient volume permissions

serviceAccount:
  automount: false              # Disable token automount (security hardening)
```

### ⚠️ hostPath Mount Security

**Critical Security Notice**: hostPath mounts reduce container isolation and may be blocked by Pod Security Standards.

```yaml
security:
  procHostMountEnabled: false    # Disabled by default for security
```

**Only enable hostPath mounts if you need host-level system monitoring:**

```yaml
security:
  procHostMountEnabled: true     # ⚠️ Reduces security - only enable if needed
config:
  NET_SENSE_MODE: "host"         # Automatically mounts /sys when needed
```

**Impact of enabling hostPath mounts:**
- Breaks container isolation
- May violate Pod Security Standards "restricted" policy
- Exposes host process information to container
- May be blocked by admission controllers

### Security Compatibility

**Pod Security Standards:**
- **Restricted**: Compatible when `security.procHostMountEnabled: false` (default)
- **Baseline**: Always compatible
- **Privileged**: Always compatible

**Admission Controllers:** Compatible with most security policies when using default settings.

## Troubleshooting

### Common Issues

#### 1. Pod Startup Failures

**Symptoms**: Pod stuck in `Pending`, `CrashLoopBackOff`, or `CreateContainerConfigError`

**Solutions**:
```bash
# Check pod events for specific error messages
kubectl describe pod -l app.kubernetes.io/name=loadshaper

# Common fixes:
# - Insufficient resources: Check node capacity and resource requests
# - Image pull issues: Verify image repository and pull secrets
# - Security policy violations: Check Pod Security Standards compatibility
# - hostPath mount blocked: Disable security.procHostMountEnabled (default: false)
```

#### 2. Security Policy Violations

**Symptoms**: Pod creation blocked with "violates PodSecurity" errors

**Solutions**:
```yaml
# Ensure security settings are compatible (default configuration)
security:
  procHostMountEnabled: false    # Must be false for "restricted" PSS
securityContext:
  readOnlyRootFilesystem: true   # Required for security compliance
serviceAccount:
  automount: false              # Recommended security hardening
```

#### 3. Network Connectivity Issues

**Symptoms**: Network load generation not working, iperf connection failures

**Solutions**:
```bash
# Check NetworkPolicy configuration
kubectl get networkpolicy -l app.kubernetes.io/name=loadshaper -o yaml

# Verify network peers configuration
kubectl get configmap -l app.kubernetes.io/name=loadshaper -o yaml | grep NET_PEERS

# Test connectivity (disable NetworkPolicy temporarily if needed)
networkPolicy:
  enabled: false    # Temporarily for testing only
```

#### 4. Storage and Persistence Issues

**Symptoms**: Metrics not persisting, database errors in logs

**Solutions**:
```bash
# Check PVC status
kubectl get pvc -l app.kubernetes.io/name=loadshaper

# Verify storage class exists
kubectl get storageclass

# Check volume mount permissions
kubectl exec -it <pod-name> -- ls -la /var/lib/loadshaper
```

#### 5. Multi-Replica Configuration Issues

**Symptoms**: Deployment fails with multiple replicas

**Solutions**:
```yaml
# Option 1: Use ReadWriteMany storage
persistence:
  accessModes:
    - ReadWriteMany

# Option 2: Use separate PVCs per replica (requires manual setup)
# Option 3: Use single replica (recommended for most use cases)
replicaCount: 1
```

#### 6. Oracle Cloud Specific Issues

**Symptoms**: Instances still being reclaimed despite loadshaper running

**Solutions**:
```bash
# Verify metrics are showing proper utilization
kubectl logs -l app.kubernetes.io/name=loadshaper | grep "95th percentile"

# Check shape-specific configuration
# E2.1.Micro: Use values-e2-micro.yaml
# A1.Flex: Use values-a1-flex.yaml and verify memory target < 80%

# Ensure 7-day metrics retention
kubectl exec -it <pod-name> -- ls -la /var/lib/loadshaper/metrics.db
```

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
kubectl port-forward svc/loadshaper 8080:8080  # Uses HEALTH_PORT
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