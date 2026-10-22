# 🚀 AegisRAG - 立即部署 (无需 MinIO)

## ✅ 前提条件（已满足）

```powershell
docker --version      # Docker version 29.4.1 ✓
node --version        # v24.15.0 ✓
```

## 🎯 单行启动命令

复制粘贴执行以下命令：

```powershell
docker compose -f docker/compose.local.yaml -f docker-compose.override.yaml --env-file .env up -d --build postgres redis migration api web worker-ingestion worker-embedding
```

这个命令会:
1. ✅ 使用 `compose.local.yaml` (简化的配置，**没有 Minio**)
2. ✅ 应用 `docker-compose.override.yaml` 的覆盖设置
3. ✅ 自动构建所有服务并启动它们

## 📋 如果不想用 compose.local.yaml

直接编辑现有的 `.env.example`,然后:
1. `Copy-Item .env.example .env`
2. **在 .env 中替换以下占位符**：
   ```
   JWT_SECRET=<your-random-secret>          # OpenSSL 生成随机密码，如："AegisRAG_Secure_Local_2024!@#%&*()"
   POSTGRES_PASSWORD=AegisDocker_Local_2024!@#%
   POSTGRES_MIGRATION_PASSWORD=Migration_Aegis_2024!$%^&*()
   POSTGRES_RUNTIME_PASSWORD=Runtime_Aegis_Rag!@#$%^&*()9876
   GRAFANA_ADMIN_PASSWORD=<your-grafana-admin-password>
   FOLDER_CURSOR_SECRET=<random-cursor-secret>
   MINIO_ENABLED=false                    # 禁用 Minio,使用本地文件存储
   ```
3. 执行单行启动命令

## 📊 访问服务

完成部署并等待约 1-2 分钟后：

🌐 Web UI:              http://localhost:3100  
📊 API Health Check:    http://localhost:8000/health

文档将存储在本地 `minio-documents` Docker volume 中。

## 🧪 验证部署

```powershell
# 检查所有服务
docker compose ps | Select-String "Up"

# API 健康检查
curl.exe http://localhost:8000/health

# 查看 Web UI (应该能显示)
curl.exe -v http://localhost:3100/
```

## 📖 更多信息

- `docker/compose.local.yaml` - Simplified config with local file storage  
- `docker-compose.override.yaml` - Override for MinIO-free deployment  
- `.env.example` - Template with all required variables  
- `DEPLOYMENT_SUMMARY.md` - Detailed documentation

## ⚠️ 重要提示

首次部署需要下载以下镜像 (需要良好的网络连接):
- PostgreSQL + pgvector: docker.io/pgvector
- Redis: docker.io/library/redis  
- Minio (官方已删除，使用覆盖配置跳过): docker.io/minio 或本地文件系统

如果网络很慢，可以尝试添加 Docker Hub 代理或通过国内镜像源下载。

---

**准备就绪了吗？** 执行单行部署命令并开始吧！
