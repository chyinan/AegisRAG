# Docker 本地栈

`docker/compose.yaml` 提供 Story 1.6 的本地依赖栈。

## 服务

```text
api                 FastAPI 应用，端口 8000
worker-ingestion    RQ worker，使用 ingestion 队列
worker-embedding    RQ worker，使用 embedding 队列
migration           一次性执行 Alembic upgrade head
postgres            PostgreSQL + pgvector 镜像，端口 5432
redis               Redis，端口 6379
minio               MinIO API 端口 9000，Console 端口 9001
open-webui          可选 profile 服务，宿主机端口 3000，容器端口 8080
```

持久化 volume：

```text
postgres-data
redis-data
minio-data
open-webui-data
```

## 环境变量

先从 `.env.example` 创建本地 `.env`，再替换占位值：

```powershell
Copy-Item .env.example .env
```

`.env` 不可提交。不要把真实 API key、生产数据库密码、租户 ID、用户 ID、
本机绝对路径或企业文档内容写进 Compose 配置。

容器内部使用服务名 DNS：

```text
postgres
redis
minio
```

MinIO root 凭据只通过 `MINIO_ACCESS_KEY` 和 `MINIO_SECRET_KEY` 注入。
readiness 响应和日志不得输出这些值。

Open WebUI profile 使用独立变量：

```text
OPENWEBUI_IMAGE
OPENWEBUI_PORT
OPENWEBUI_OPENAI_API_BASE_URL
OPENWEBUI_PROVIDER_API_KEY
OPENWEBUI_SECRET_KEY
OPENWEBUI_SERVICE_TOKEN_HASHES_JSON
```

`OPENWEBUI_PROVIDER_API_KEY` 是 Open WebUI provider 里使用的明文 bearer
token。后端 API 不保存该明文，只读取 `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON`
里的 SHA-256 hash，并默认映射到 `document:read` 和 `retrieval:query`。

## 常用命令

校验服务图：

```powershell
docker compose -f docker/compose.yaml config
```

构建并启动本地栈：

```powershell
docker compose -f docker/compose.yaml up -d --build postgres redis minio migration api worker-ingestion worker-embedding
```

校验并启动 Open WebUI 演示 profile：

```powershell
docker compose -f docker/compose.yaml --profile open-webui config
docker compose -f docker/compose.yaml --profile open-webui up -d --build postgres redis minio migration api worker-ingestion worker-embedding open-webui
```

宿主机访问 Open WebUI：

```text
http://127.0.0.1:3000
```

Open WebUI 容器内连接 API 的 OpenAI-compatible base URL：

```text
http://api:8000/v1
```

宿主机手动 curl API 时使用：

```text
http://127.0.0.1:8000/v1
```

检查健康状态：

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/ready
```

停止服务：

```powershell
docker compose -f docker/compose.yaml down
```

重置本地 volume：

```powershell
docker compose -f docker/compose.yaml down -v
```

## Healthcheck

`api` 使用 Python 标准库 HTTP 探测 `/health`，镜像不需要安装 `curl`。

`postgres` 使用 `pg_isready`。

`redis` 使用 `redis-cli ping`。

`minio` 使用 MinIO 服务镜像中的 readiness 命令。

`migration` 等待 PostgreSQL healthy 后执行 Alembic。`api` 等待 PostgreSQL、
Redis、MinIO healthy 且 migration 成功后再作为本地工作流依赖。

`open-webui` 只在 `--profile open-webui` 启用时启动，并等待 `api` healthy。
它不是 `api`、worker、migration、PostgreSQL、Redis 或 MinIO 的 dependency。
默认后端栈、Python 测试、ruff 和 mypy 不依赖 Open WebUI 容器。

## 故障排查

如果 `docker compose config` 失败，优先检查这些必需环境变量：

```text
POSTGRES_PASSWORD
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
```

如果 `/ready` 返回 `ready=false`，查看 dependency 的 `name`、`status`、
`latency_ms` 和 `error_code`。响应会刻意省略 URL、密码、token 和 secret。

如果 Open WebUI 无法连接后端，先确认容器内 provider base URL 是
`http://api:8000/v1`，宿主机浏览器访问地址是 `http://127.0.0.1:3000`，
并且明文 provider key 的 SHA-256 hash 已写入后端
`OPENWEBUI_SERVICE_TOKEN_HASHES_JSON`。已经初始化过的 `open-webui-data`
volume 可能保留旧 provider 配置，需要在 Open WebUI UI 中更新或重置该 volume。

如果 worker 已启动但没有处理任务，这是 Story 1.6 的预期结果。本 Story
只提供 worker 启动、队列隔离、JSON 序列化和 payload 安全边界。
