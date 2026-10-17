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

{{- define "aegisrag.secretName" -}}
{{- default (printf "%s-secrets" (include "aegisrag.fullname" .)) .Values.secrets.existingSecret -}}
{{- end }}

{{- define "aegisrag.image" -}}
{{- if .root.Values.global.imageRegistry -}}{{ .root.Values.global.imageRegistry }}/{{- end -}}{{ .image.repository }}:{{ .image.tag }}
{{- end }}

{{- define "aegisrag.appEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef: {name: {{ include "aegisrag.secretName" . }}, key: database-url}
- name: REDIS_URL
  valueFrom:
    secretKeyRef: {name: {{ include "aegisrag.secretName" . }}, key: redis-url}
{{- range $name, $key := dict "MINIO_ACCESS_KEY" "minio-access-key" "MINIO_SECRET_KEY" "minio-secret-key" "JWT_SECRET" "jwt-secret" "FOLDER_CURSOR_SECRET" "folder-cursor-secret" "LLM_API_KEY" "llm-api-key" "EMBEDDING_API_KEY" "embedding-api-key" }}
- name: {{ $name }}
  valueFrom:
    secretKeyRef: {name: {{ include "aegisrag.secretName" $ }}, key: {{ $key }}}
{{- end }}
{{- end }}

{{- define "aegisrag.migrationWait" -}}
- name: wait-for-schema
  image: {{ include "aegisrag.image" (dict "root" . "image" .Values.api.image) | quote }}
  imagePullPolicy: {{ .Values.api.image.pullPolicy }}
  command: ["uv", "run", "--no-sync", "python", "-c"]
  args:
    - |
      import asyncio, os
      from sqlalchemy import text
      from sqlalchemy.ext.asyncio import create_async_engine
      async def check():
          engine = create_async_engine(os.environ['DATABASE_URL'], connect_args={'timeout': 5, 'command_timeout': 5})
          try:
              async with engine.connect() as connection:
                  revisions = list(await connection.scalars(text('SELECT version_num FROM alembic_version')))
                  if revisions != [os.environ['EXPECTED_REVISION']]:
                      raise RuntimeError('Schema migration is pending')
          finally:
              await engine.dispose()
      asyncio.run(check())
  env:
    - name: DATABASE_URL
      valueFrom:
        secretKeyRef: {name: {{ include "aegisrag.secretName" . }}, key: database-url}
    - name: EXPECTED_REVISION
      value: {{ .Values.migration.expectedRevision | quote }}
{{- end }}
