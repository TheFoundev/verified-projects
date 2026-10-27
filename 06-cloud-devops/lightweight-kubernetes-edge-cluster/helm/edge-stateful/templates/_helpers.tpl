{{/*
Helpers for the edge-stateful chart. Kept small and explicit on purpose.
*/}}
{{- define "edge-stateful.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "edge-stateful.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "edge-stateful.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "edge-stateful.labels" -}}
app.kubernetes.io/name: {{ include "edge-stateful.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- range $k, $v := .Values.commonLabels }}
{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "edge-stateful.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "edge-stateful.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
