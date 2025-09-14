{{/*
Expand the name of the chart.
*/}}
{{- define "loadshaper.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "loadshaper.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "loadshaper.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "loadshaper.labels" -}}
helm.sh/chart: {{ include "loadshaper.chart" . }}
{{ include "loadshaper.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "loadshaper.selectorLabels" -}}
app.kubernetes.io/name: {{ include "loadshaper.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "loadshaper.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "loadshaper.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Validate configuration compatibility
*/}}
{{- define "loadshaper.validateConfig" -}}
{{/* Validate replica count with ReadWriteOnce persistence */}}
{{- if and (gt (.Values.replicaCount | int) 1) .Values.persistence.enabled }}
  {{- if has "ReadWriteOnce" .Values.persistence.accessModes }}
    {{- fail "Cannot use replicaCount > 1 with ReadWriteOnce persistence. Use ReadWriteMany or disable persistence for multiple replicas." }}
  {{- end }}
{{- end }}

{{/* Validate ServiceMonitor requires health endpoint */}}
{{- if and .Values.serviceMonitor.enabled (ne .Values.config.HEALTH_ENABLED "true") }}
  {{- fail "ServiceMonitor requires HEALTH_ENABLED=true. Set config.HEALTH_ENABLED to 'true' when serviceMonitor.enabled is true." }}
{{- end }}

{{/* Validate metrics service requires health endpoint */}}
{{- if and .Values.service.enabled .Values.service.metrics.enabled (ne .Values.config.HEALTH_ENABLED "true") }}
  {{- fail "Metrics service requires HEALTH_ENABLED=true. Set config.HEALTH_ENABLED to 'true' when service.metrics.enabled is true." }}
{{- end }}

{{/* Validate PodDisruptionBudget requires multiple replicas or specific configuration */}}
{{- if and .Values.podDisruptionBudget.enabled (eq (.Values.replicaCount | int) 1) }}
  {{- if not (or .Values.podDisruptionBudget.minAvailable .Values.podDisruptionBudget.maxUnavailable) }}
    {{- fail "PodDisruptionBudget with single replica requires explicit minAvailable or maxUnavailable configuration." }}
  {{- end }}
{{- end }}

{{/* Validate both minAvailable and maxUnavailable are not set */}}
{{- if and .Values.podDisruptionBudget.minAvailable .Values.podDisruptionBudget.maxUnavailable }}
  {{- fail "PodDisruptionBudget cannot have both minAvailable and maxUnavailable set. Use one or the other." }}
{{- end }}

{{/* Validate NET_PROTOCOL enum */}}
{{- if not (has .Values.config.NET_PROTOCOL (list "udp" "tcp")) }}
  {{- fail (printf "Invalid NET_PROTOCOL '%s'. Must be 'udp' or 'tcp'." .Values.config.NET_PROTOCOL) }}
{{- end }}

{{/* Validate NET_SENSE_MODE enum */}}
{{- if not (has .Values.config.NET_SENSE_MODE (list "container" "host")) }}
  {{- fail (printf "Invalid NET_SENSE_MODE '%s'. Must be 'container' or 'host'." .Values.config.NET_SENSE_MODE) }}
{{- end }}

{{/* BREAKING CHANGE VALIDATION: Persistent storage is now mandatory */}}
{{- if not .Values.persistence.enabled }}
  {{- fail "persistence.enabled must be true. LoadShaper now requires a persistent volume for 7-day P95 metrics to prevent Oracle VM reclamation. Ephemeral storage (emptyDir) is no longer supported." }}
{{- end }}

{{- end }}