{{/*
AegisRAG — common template helpers
*/}}

{{- define "aegisrag.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "aegisrag.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "aegisrag.labels" -}}
app.kubernetes.io/name: {{ include "aegisrag.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "aegisrag.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aegisrag.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
