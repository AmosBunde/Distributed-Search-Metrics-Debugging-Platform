{{/*
Naming and labelling helpers, so every object in the release agrees.
*/}}

{{- define "search-metrics.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "search-metrics.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "search-metrics.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "search-metrics.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "search-metrics.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: search-metrics
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "search-metrics.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "search-metrics.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
The image tag is required. A chart that silently falls back to `latest` makes a
rollback ambiguous and a rolling restart non-deterministic, so this fails the
render instead.
*/}}
{{- define "search-metrics.imageTag" -}}
{{- if .Values.image.tag }}
{{- .Values.image.tag }}
{{- else }}
{{- fail "image.tag is required: set it to an immutable tag, e.g. --set image.tag=$(git rev-parse --short HEAD)" }}
{{- end }}
{{- end }}

{{- define "search-metrics.image" -}}
{{- printf "%s/%s:%s" .root.Values.image.registry .service (include "search-metrics.imageTag" .root) }}
{{- end }}

{{/*
Configuration shared by every service. Endpoints come from values; credentials
only ever come from the existing Secret.
*/}}
{{- define "search-metrics.commonEnv" -}}
- name: ENVIRONMENT
  value: {{ .Values.environment | quote }}
- name: LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: KAFKA_BOOTSTRAP_SERVERS
  value: {{ .Values.dependencies.kafka.bootstrapServers | quote }}
- name: KAFKA_TOPIC_EVENTS
  value: {{ .Values.config.topics.events | quote }}
- name: KAFKA_TOPIC_RESULTS
  value: {{ .Values.config.topics.results | quote }}
- name: KAFKA_TOPIC_ERRORS
  value: {{ .Values.config.topics.errors | quote }}
- name: KAFKA_TOPIC_ANOMALIES
  value: {{ .Values.config.topics.anomalies | quote }}
- name: KAFKA_TOPIC_SPANS
  value: {{ .Values.config.topics.spans | quote }}
- name: CLICKHOUSE_HOST
  value: {{ .Values.dependencies.clickhouse.host | quote }}
- name: CLICKHOUSE_PORT
  value: {{ .Values.dependencies.clickhouse.port | quote }}
- name: CLICKHOUSE_DB
  value: {{ .Values.dependencies.clickhouse.database | quote }}
- name: CLICKHOUSE_USER
  value: {{ .Values.dependencies.clickhouse.user | quote }}
- name: CLICKHOUSE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: clickhouse-password
- name: POSTGRES_HOST
  value: {{ .Values.dependencies.postgres.host | quote }}
- name: POSTGRES_PORT
  value: {{ .Values.dependencies.postgres.port | quote }}
- name: POSTGRES_DB
  value: {{ .Values.dependencies.postgres.database | quote }}
- name: POSTGRES_USER
  value: {{ .Values.dependencies.postgres.user | quote }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: postgres-password
- name: REDIS_HOST
  value: {{ .Values.dependencies.redis.host | quote }}
- name: REDIS_PORT
  value: {{ .Values.dependencies.redis.port | quote }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: redis-password
      optional: true
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.config.tracing.endpoint | quote }}
- name: OTEL_TRACES_SAMPLER_ARG
  value: {{ .Values.config.tracing.samplerArg | quote }}
{{- end }}

{{/*
Spread replicas across zones. Scheduling stays possible when a zone is full:
ScheduleAnyway means a pod that cannot spread still runs, which is what you want
during an incident.
*/}}
{{- define "search-metrics.topologySpread" -}}
{{- if .root.Values.topologySpreadEnabled }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: {{ include "search-metrics.name" .root }}
        app.kubernetes.io/component: {{ .component }}
{{- end }}
{{- end }}
