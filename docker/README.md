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
```

持久化 volume：

```text
postgres-data
redis-data
minio-data
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

## 常用命令

校验服务图：

```powershell
docker compose -f docker/compose.yaml config
```

构建并启动本地栈：

```powershell
docker compose -f docker/compose.yaml up -d --build postgres redis minio migration api worker-ingestion worker-embedding
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

## 故障排查

如果 `docker compose config` 失败，优先检查这些必需环境变量：

```text
POSTGRES_PASSWORD
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
```

如果 `/ready` 返回 `ready=false`，查看 dependency 的 `name`、`status`、
`latency_ms` 和 `error_code`。响应会刻意省略 URL、密码、token 和 secret。

如果 worker 已启动但没有处理任务，这是 Story 1.6 的预期结果。本 Story
只提供 worker 启动、队列隔离、JSON 序列化和 payload 安全边界。
