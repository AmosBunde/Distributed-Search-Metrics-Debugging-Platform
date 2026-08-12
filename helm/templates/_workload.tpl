{{/*
One workload template for every service.

Five near-identical Deployments drift: one gets a probe tuned, another gets a
security context, and six months later nobody knows which differences are
deliberate. Defining the shape once means a difference between services is
always a values difference, and therefore visible.
*/}}
{{- define "search-metrics.workload" -}}
{{- $root := .root -}}
{{- $name := .name -}}
{{- $service := .service -}}
{{- $config := .config -}}
{{- $fullname := printf "%s-%s" (include "search-metrics.fullname" $root) $name -}}
{{- if $config.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "search-metrics.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $name }}
spec:
  {{- if not $config.autoscaling.enabled }}
  replicas: {{ $config.replicaCount }}
  {{- end }}
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "search-metrics.name" $root }}
      app.kubernetes.io/instance: {{ $root.Release.Name }}
      app.kubernetes.io/component: {{ $name }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        {{- include "search-metrics.labels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ $name }}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ $config.port | quote }}
        prometheus.io/path: /metrics
    spec:
      serviceAccountName: {{ include "search-metrics.serviceAccountName" $root }}
      {{- with $root.Values.image.pullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml $root.Values.podSecurityContext | nindent 8 }}
      {{- include "search-metrics.topologySpread" (dict "root" $root "component" $name) | nindent 6 }}
      {{- with $root.Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $root.Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: {{ $name }}
          image: {{ include "search-metrics.image" (dict "root" $root "service" $service) }}
          imagePullPolicy: {{ $root.Values.image.pullPolicy }}
          securityContext:
            {{- toYaml $root.Values.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: {{ $config.port }}
              protocol: TCP
          env:
            {{- include "search-metrics.commonEnv" $root | nindent 12 }}
            {{- with .extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- if .probes }}
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          # Slow starts are a startup problem, not a liveness problem: without
          # this, a cold JIT or a slow dependency lookup gets the pod killed in
          # a loop.
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 5
            failureThreshold: 24
          {{- end }}
          resources:
            {{- toYaml $config.resources | nindent 12 }}
          volumeMounts:
            # The root filesystem is read-only, so anything that needs to write
            # gets an explicit emptyDir.
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "search-metrics.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $name }}
spec:
  type: ClusterIP
  ports:
    - port: {{ $config.port }}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    app.kubernetes.io/name: {{ include "search-metrics.name" $root }}
    app.kubernetes.io/instance: {{ $root.Release.Name }}
    app.kubernetes.io/component: {{ $name }}
{{- if $config.autoscaling.enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "search-metrics.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $name }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ $fullname }}
  minReplicas: {{ $config.autoscaling.minReplicas }}
  maxReplicas: {{ $config.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ $config.autoscaling.targetCPUUtilizationPercentage }}
{{- end }}
{{- if $config.podDisruptionBudget.enabled }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "search-metrics.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $name }}
spec:
  minAvailable: {{ $config.podDisruptionBudget.minAvailable }}
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "search-metrics.name" $root }}
      app.kubernetes.io/instance: {{ $root.Release.Name }}
      app.kubernetes.io/component: {{ $name }}
{{- end }}
{{- if $root.Values.serviceMonitor.enabled }}
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "search-metrics.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $name }}
    {{- with $root.Values.serviceMonitor.labels }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "search-metrics.name" $root }}
      app.kubernetes.io/instance: {{ $root.Release.Name }}
      app.kubernetes.io/component: {{ $name }}
  endpoints:
    - port: http
      path: /metrics
      interval: {{ $root.Values.serviceMonitor.interval }}
{{- end }}
{{- end }}
{{- end }}
