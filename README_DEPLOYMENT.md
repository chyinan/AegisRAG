# ⚡ AegisRAG - 快速部署指南

## 📦 5 分钟本地 Docker 部署

### Step 1: 准备环境

确保已安装：
- ✅ Docker Desktop for Windows (检查：`docker --version`)
- ✅ Node.js 18+ (检查：`node --version`)

当前状态：
```powershell
docker --version      # Docker version 29.4.1 ✓
node --version        # v24.15.0 ✓
```

### Step 2: 生成 .env 文件

```powershell
# 复制模板文件
Copy-Item .env.example .env

# ⚠️ 重要：编辑以下敏感配置 (推荐用密码管理器)
notepad .env
```

需要修改的配置项：
```env
# JWT_SECRET=<至少 32 个随机字符>
JWT_SECRET=replace-with-a-secure-random-secret-at-least-32-bytes-generated-locally

# PostgreSQL bootstrap 密码 (仅首次初始化使用)
POSTGRES_PASSWORD=<你的安全密码，如：AegisRAG_Secure_2024!>
POSTGRES_PASSWORD=your-secure-password-here

# Migration 角色密码
POSTGRES_MIGRATION_PASSWORD=<同上，建议与 bootstrap 不同>

# Runtime 角色密码 (API/Worker 使用)
POSTGRES_RUNTIME_PASSWORD=<同上，建议独立设置>

# MinIO 存储密钥
MINIO_SECRET_KEY=<至少 16 字符的安全密钥>
MINIO_SECRET_KEY=your-minio-secret-key-here

# Grafana 管理密码
GRAFANA_ADMIN_PASSWORD=<你的 Grafana admin 密码>
GRAFANA_ADMIN_PASSWORD=your-grafana-password-here

# 其他占位符请用真实值替换
```

生成随机密码的方法：
```powershell
# 使用 OpenSSL 生成随机密码 (复制一行即可)
openssl rand -base64 32 | ForEach-Object { $_.TrimEnd('\r\n') }
```

### Step 3: 启动服务

最简单的方式：
```powershell
.\scripts\start.bat
```

或者使用 Docker Compose 命令：
```powershell
docker compose --env-file .env -f docker/compose.yaml up -d --build \
    postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding
```

### Step 4: 访问服务

启动完成后等待约 60-90 秒，然后访问：

| 服务 | URL | 用途 |
|------|-----|------|
| **Web UI** | http://localhost:3100 | 主工作台 ✓ |
| **API Health** | http://localhost:8000/health | 检查 API 状态 |
| **MinIO Console** | http://localhost:9001 | 对象存储管理（可选） |
| **Prometheus** | http://localhost:9090 | metrics dashboard |
| **Grafana** | http://localhost:3101 | Grafana admin:<PASSWORD> |
| **Jaeger** | http://localhost:16686 | 分布式追踪（可选） |

### Step 5: 验证部署

```powershell
# 检查所有服务状态
docker compose ps

# 检查 API 健康状态
curl.exe http://localhost:8000/health

# 查看 Web UI (应该返回 HTML)
curl.exe -s http://localhost:3100/

# 详细日志
docker compose logs api --tail=50
```

## 🎯 使用场景配置

### 场景 A：文档问答（MVP，推荐新手）

`.env` 中设置：
```env
EMBEDDING_PROVIDER=fake        # 模拟 embedding data
LLM_PROVIDER=fake              # 模拟 LLM response
RERANK_PROVIDER=fake           # 跳过重排序器
```

这允许你在没有真实 API key 的情况下测试所有功能。

### 场景 B：使用 OpenAI API

`.env` 中设置：
```env
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_BASE_URL=https://api.openai.com/v1/chat/completions
EMBEDDING_API_KEY=sk-your-openai-api-key

LLM_PROVIDER=openai_compatible
LLM_MODEL=gpt-4-turbo
LLM_BASE_URL=https://api.openai.com/v1/chat/completions
LLM_API_KEY=sk-your-openai-api-key

RERANK_PROVIDER=openai_compatible
RERANK_MODEL=text-embedding-embedding-inference-v2
```

### 场景 C：使用本地 Ollama

如果你使用了本地 Docker Desktop 中的 Ollama (推荐)：
```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b
LLM_BASE_URL=http://host.docker.internal:11434/v1

EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
```

### 场景 D：使用其他 LLM (DeepSeek/Qwen)

在 .env 中添加：
```env
LLM_API_KEY=your-deepseek-api-key
LLM_MODEL=deepseek-chat
# 或其他模型
```

## 🛑 停止服务

临时关闭（保留数据）：
```powershell
.\scripts\stop.bat
docker compose down
```

彻底重置（删除所有数据）：
```powershell
docker compose down -v
```

## 🔧 故障排查

**问题**: API health check 失败  
`curl http://localhost:8000/health` 返回错误

**解决**:
```powershell
# 查看 API 日志
docker compose logs api

# 检查端口占用
netstat -ano | findstr :8000

# 确保 PostgreSQL 连接正常
docker compose exec postgres pg_isready -h postgres -U rag_runtime
```

---

**问题**: Web UI 不显示内容  
**解决**:
```powershell
# 重新构建前端
docker compose up -d --build web

# 清除浏览器缓存并硬刷新 F12
```

---

## 📚 完整文档

详细配置选项请参考：
- `docker/README.md` - Docker Compose 详解
- `docker/README_DEPLOYMENT.md` - 部署最佳实践
- `.env.example` - 所有可用的环境变量

## 💡 提示

1. 首次启动时需要等待服务初始化，请耐心等待
2. API `/ready` 接口会返回详细的依赖状态
3. MinIO Console (9001 端口) 是可选的，不需要时可以直接忽略
4. 使用真实 API key 前，先在 `fake` providers 下测试所有功能

## 📝 许可证与使用

遵循项目 LICENSE 文件中的条款。
