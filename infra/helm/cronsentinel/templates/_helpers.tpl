{{- define "cs.fullname" -}}{{ .Release.Name }}-cronsentinel{{- end -}}
{{- define "cs.deployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata: {name: {{ include "cs.fullname" .root }}-{{ .name }}, namespace: {{ .root.Release.Namespace }}}
spec:
  replicas: {{ .replicas }}
  selector: {matchLabels: {app: {{ include "cs.fullname" .root }}-{{ .name }}}}
  template:
    metadata: {labels: {app: {{ include "cs.fullname" .root }}-{{ .name }}}}
    spec:
      # Numeric UID: the web image's `USER node` is a name, which kubelet can't verify under runAsNonRoot.
      securityContext: {runAsNonRoot: true, runAsUser: {{ .runAsUser | default 10001 }}, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: {{ .name }}
          image: {{ .image }}
          {{- if .command }}
          command: {{ toJson .command }}
          {{- end }}
          envFrom: [{secretRef: {name: {{ include "cs.fullname" .root }}-env}}]
          {{- if .port }}
          ports: [{containerPort: {{ .port }}}]
          readinessProbe: {httpGet: {path: {{ .readyPath | default "/readyz" }}, port: {{ .port }}}, periodSeconds: 10, timeoutSeconds: 10}
          livenessProbe: {httpGet: {path: {{ .livePath | default "/healthz" }}, port: {{ .port }}}, periodSeconds: 20, timeoutSeconds: 10, failureThreshold: 6}
          {{- end }}
          resources: {{ toYaml .resources | nindent 12 }}
{{- end -}}
