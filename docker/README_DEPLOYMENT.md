# AegisRAG Docker 本地部署指南

## 快速开始

### 前置要求

1. **Docker Desktop** for Windows
   - 检查：`docker --version`
   - 确保 Docker Desktop 正在运行（托盘图标显示绿色）

2. **Node.js** (用于前端构建)
   - 当前安装：v24.x ✓
   - 检查：`node --version`

### 首次部署

#### 方案 A：使用批处理脚本（推荐本地开发）

```powershell
# 1. 复制 .env.example 并编辑
Copy-Item .env.example .env

# 2. 修改以下敏感值（或使用密码管理器生成）：
#    - JWT_SECRET (至少 32 字符)
#    - POSTGRES_PASSWORD
#    - POSTGRES_MIGRATION_PASSWORD
#    - POSTGRES_RUNTIME_PASSWORD
#    - MINIO_SECRET_KEY
#    - GRAFANA_ADMIN_PASSWORD

# 3. 启动所有服务
.\<scripts>\start.bat

# 4. 访问 Web UI
浏览器打开：http://localhost:3100
```

#### 方案 B：使用 Docker Compose 命令（推荐）

```powershell
# 1. 准备 .env 文件
Copy-Item .env.example .env
# 编辑敏感值...

# 2. 构建并启动服务
docker compose --env-file .env -f docker/compose.yaml up -d --build \
    postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding
```

### 理解架构

#### 服务列表

| 服务 | 镜像 | 端口 | 用途 |
|------|------|------|------|
| API | 本地构建 (uvicorn) | 8000 | FastAPI 后端 |
| Web | 本地构建 (Next.js) | 3100 | Next.js 前端界面 |
| Worker (in) | 本地构建 (rq) | - | 文档_ingestion_任务队列 |
| Worker (embed) | 本地构建 (rq) | - | Embedding 计算任务队列 |
| PostgreSQL | `pgvector:pg17` | 5432 | 数据库 + 向量存储 |
| Redis | `redis:7.4-alpine` | 6379 | 消息缓存/队列 |
| MinIO | S3 兼容对象存储 | 9000 | 文档存储 (API)、控制台 9001 |

#### 监控服务

| 服务 | 端口 | 用途 |
|------|------|------|
| Prometheus | 9090 | metrics 采集 |
| Grafana | 3101 | dashboard 可视化 (admin:yourpwd) |
| Jaeger | 16686 | distributed tracing |

### 验证部署

```powershell
# 检查所有服务状态
docker compose --env-file .env -f docker/compose.yaml ps

# API health check
curl.exe http://localhost:8000/health

# Web UI ready check
curl.exe -I http://localhost:3100/

# GraphQL readyness (如果启用)
curl.exe http://localhost:8000/ready
```

### 停止服务

#### 临时关闭（保留数据）

```powershell
.\scripts\stop.bat
# OR
docker compose --env-file .env -f docker/compose.yaml down
```

#### 彻底重置（删除所有数据）

```powershell
docker compose --env-file .env -f docker/compose.yaml down -v
```

### 使用场景配置

#### 1. 纯文档问答 (MVP)

```env
# .env 配置
EMBEDDING_PROVIDER=fake      # 使用模拟 data
LLM_PROVIDER=fake            # 使用 mock response
RERANK_PROVIDER=fake         # 无需重排序器
OCR_PROVIDER=tesseract       # 系统自带 OCR
VECTOR_STORE_TYPE=pgvector   # 默认选择

# 启动 (会跳过 embedding/LLM 计算)
docker compose up -d --build api postgres redis minio
```

#### 2. 集成真实 LLM API

```env
# .env 配置
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=<your-api-key>

LLM_PROVIDER=openai_compatible
LLM_MODEL=gpt-4-turbo
LLM_BASE_URL=https://api.openai.com/v1/chat/completions
LLM_API_KEY=<your-api-key>

RERANK_PROVIDER=openai_compatible
RERANK_MODEL=text-embed-embedding-inference-v2

# 重启服务（不重新构建以保留数据）
docker compose up -d api worker-ingestion
```

#### 3. 集成本地 Ollama/RAGI

```env
# .env 配置 (Docker Desktop 内 Ollama)
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b
LLM_BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768

# Ollama 容器端口 (默认 11434)
```

#### 4. MinIO 文档存储（可选）

```env
MINIO_ACCESS_KEY=myminioadmin
MINIO_SECRET_KEY=<your-secure-minio-secret-key>
MINIO_BUCKET=aegisrag-docs
MINIO_DOCUMENT_PREFIX=raw-documents

# 访问 MinIO Console
http://localhost:9001 (用户名：myminioadmin)
```

### 故障排查

#### API 无法启动

```powershell
# 检查 PostgreSQL 是否健康
docker compose --env-file .env exec postgres pg_isready -h postgres -U rag_runtime

# 查看详细日志
docker compose logs api

# 检查数据库配置
grep 'DATABASE_URL=' .env
```

#### Web UI 无法访问

```powershell
# 检查端口占用
netstat -ano | findstr :3100

# 重启 web 服务
docker compose up -d --build web

# 检查前端构建日志
docker compose logs web
```

#### Worker 没有处理任务

这是 Story 1.6 的预期结果。要启用 worker:

```powershell
# 确保 worker 已启动
docker compose ps | findstr worker

# 查看 worker 日志
docker compose logs worker-ingestion
docker compose logs worker-embedding
```

#### Worker 已启动但没有处理任务

这是 Story 1.6 的预期结果。本故事只提供 worker 启动、队列隔离、JSON 序列化和 payload 安全边界的实现，worker 会等待来自 API 的文档_ingestion_任务后才会启动。

### 性能优化建议

#### 1. CPU 密集计算（如 Embedding）

使用独立机器运行 embedding worker：
- Docker Desktop: `docker run -d --name embedding-worker ghcr.io/astral-sh/uv:python3.11-bookworm-slim <...>`
- Linux VM: 部署独立的 embedding service，与 API/worker 分离

#### 2. GPU 加速（如需要）

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
USER root
RUN pip index add nvidia https://pypi.nvidia.com
# 在 Dockerfile 中添加 CUDA 支持...
```

### 数据安全

⚠️ **重要**：生产环境前修改以下配置！

1. 更换所有 `replace-with-...` 占位符
2. 禁用 Dev auth headers: `ENABLE_DEV_AUTH_HEADERS=false`
3. 设置合理的 `RATE_LIMIT_MAX_REQUESTS`
4. 使用 HTTPS (添加 Traefik/Nginx TLS)

### 数据备份

```powershell
# PostgreSQL 全备
docker compose --env-file .env exec postgres pg_dump -U rag_runtime rag_app > backup-sql-$(Get-Date -Format 'yyyyMMdd-HHmm').sql
Compress-Archive -Path *.sql -DestinationPath backup-$(Get-Date -Format 'yyyyMMdd').zip

# Redis 快照 (手动)
docker compose --env-file .env exec redis redis-cli BGSAVE

# MinIO 备份
mc alias set local http://localhost:9000 \
    -u myminioadmin \
    -p <your-secret-key>
mc ls local/ && mc mirroring add backup.local local/aegisrag-docs s3://<other-bucket>/...
```

### 下一步开发

启动后，可以：

1. 上传第一个测试文档到 UI
2. 查看 `/ingestion` 任务状态
3. 监控 Grafana dashboard (http://localhost:3101)
4. 追踪 trace (Jaeger: http://localhost:16686)

---

## 附录：常用命令速查

```powershell
# 查看所有服务的详细日志
docker compose logs -f --tail=100

# 进入 API 容器 shell
docker compose exec api python -c "import uvicorn; from apps.api.main import app; print(uvicorn.run(app, host='0.0.0.0', port=8000))"

# 查询文档列表（需要认证）
curl.exe http://localhost:8000/v1/documents/search?document_id=%22test-doc%22 -H "Authorization: Bearer <token>"

# 查看正在处理的_queuing_任务
docker compose exec redis redis-cli KEYS "*"

# 进入 PostgreSQL 查询
docker compose --env-file .env exec -e PGPASSWORD=rag_runtime postgres psql -U rag_runtime -d rag_app
```
