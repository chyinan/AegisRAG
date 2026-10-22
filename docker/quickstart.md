# 🚀 AegisRAG 一键部署 (Windows)

## ✅ 前置要求（已安装）

```powershell
docker --version      # Docker version 29.4.1 ✓
node --version        # v24.15.0 ✓
```

## 🎯 两步部署

### Step 1: 创建 .env 配置并编辑

```powershell
# 复制示例文件
Copy-Item .env.example .env

# ⚠️ 重要：编辑 .env，替换以下占位符的值 (每行一次修改):
notepad .env
```

需要在 notepad 中修改的地方（按 `Ctrl+F` 搜索每个关键词）:

| 搜索词 | 新值示例 (复制即可) |
|--------|--------------------|
| JWT_SECRET | `replace-with-a-secure-random-secret-at-least-32-bytes-generated-locally` |
| POSTGRES_PASSWORD | `AegisRAG_Secure_Basic_1234_!@#%` |
| POSTGRES_MIGRATION_PASSWORD | `MigrationSecure_Pass_2024!$%^` |
| POSTGRES_RUNTIME_PASSWORD | `RuntimePass_Rag2024!&*()` |
| MINIO_SECRET_KEY | `Minio_Aegis_RAG_Secret_Key_24@#%` |
| GRAFANA_ADMIN_PASSWORD | `GrafanaAdmin_Pass_Welcome_24` |
| FOLDER_CURSOR_SECRET | `Folder_Cursor_Secure_Token_X9Z7!` |

**快速生成随机密码：**
```powershell
# 复制输出并粘贴到 .env
openssl rand -base64 32 | ForEach-Object { $_.TrimEnd('\r\n') }
```

### Step 2: 启动服务

```powershell
# 执行部署 (需要约 5-10 分钟首次构建)
docker compose --env-file .env -f docker/compose.yaml up -d --build \
    postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding
```

## ✨ 完成！访问服务

等待 60-90 秒后，打开浏览器访问：

🌐 **http://localhost:3100** ← 主工作台 (Web UI)


其他服务：
- 📊 Prometheus monitoring: http://localhost:9090
- 🎨 Grafana dashboard: http://localhost:3101
- 🔍 Jaeger tracing: http://localhost:16686

## 🧪 验证部署

```powershell
# 检查 API 健康状态
curl.exe http://localhost:8000/health

# 查看服务列表
docker compose ps
```

## 🛑 随时停止

```powershell
.\<scripts>\stop.bat
# OR
docker compose --env-file .env -f docker/compose.yaml down
```

## 🔧 常用命令

| 任务 | 命令 |
|-----|------|
| 查看实时日志 | `docker compose logs -f` |
| 重启某个服务 | `docker compose restart api` |
| 进入 API shell | `docker compose exec api python` |
| 清除所有数据 | `docker compose down -v` |

## 💡 提示

1. **首次部署**：耐心等待 5-10 分钟，直到 API 和 Web UI 都准备好
2. **上传测试文档**：通过 Web UI (http://localhost:3100) 上传 PDF/DOCX/TXT 文件
3. **MinIO Console (可选)**：http://localhost:9001 - 用于管理文档存储
4. **使用真实 LLM**：见 `.env.example` 中的配置说明

## 📖 更多资源

- `docker/README.md` - Docker Compose 详解
- `docker/README_DEPLOYMENT.md` - 部署最佳实践  
- `.env.example` - 所有可用配置项
