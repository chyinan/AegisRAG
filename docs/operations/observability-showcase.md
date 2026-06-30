# 可观测性展示 — Grafana + Prometheus + Jaeger + OpenTelemetry

面向技术面试的可观测性方案展示文档，涵盖 Metrics、Traces、Logs 三层架构。

## 1. 概览

AegisRAG 实现了完整的三层可观测性架构：

- **Metrics（指标）**：基于 Prometheus + Grafana，采集 API 请求量、延迟分布、错误率、请求体积等 RED（Rate / Error / Duration）指标，通过 `prometheus-fastapi-instrumentator` 自动暴露 `/metrics` 端点，15s 抓取间隔，预置 8 面板仪表板。
- **Traces（链路追踪）**：基于 OpenTelemetry + Jaeger，自动埋点 FastAPI、httpx、Redis、SQLAlchemy，Span 通过 OTLP gRPC 导出至 Jaeger Collector，支持 W3C TraceContext 跨服务传播。
- **Logs（日志）**：基于 structlog 输出 JSON 结构化日志，每条日志携带 `trace_id`、`request_id`、`session_id`，可在 Jaeger 中通过 trace_id 关联全链路日志与调用链。

## 2. Grafana 仪表板面板清单

仪表板 UID：`aegisrag-api`，每 10s 自动刷新，默认时间窗口 15m。

| # | 面板名称 | 类型 | PromQL 指标 | 阈值 |
|---|---------|------|------------|------|
| 1 | Request Rate | Stat | `sum(rate(http_requests_total[1m]))` | 无（纯展示） |
| 2 | Avg Latency | Stat | `sum(rate(http_request_duration_seconds_sum[1m])) / sum(rate(http_request_duration_seconds_count[1m])) * 1000` | 🟢 < 5000ms / 🟡 5000ms / 🔴 10000ms |
| 3 | Error Rate | Stat | `sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m])) * 100` | 🟢 < 1% / 🟡 1% / 🔴 5% |
| 4 | Requests/sec by Endpoint | Time Series | `sum(rate(http_requests_total[1m])) by (handler)` | 无（按 handler 多线展示） |
| 5 | Latency by Endpoint (p95, ms) | Time Series | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le, handler)) * 1000` | 无（按 handler 多线展示） |
| 6 | Status Codes Distribution | Bar Gauge | `sum(rate(http_requests_total[5m])) by (status)` | 🟢 < 1 / 🟡 1 / 🔴 5 req/s |
| 7 | Request Size Distribution | Heatmap | `sum(rate(http_request_size_bytes_bucket[5m])) by (le)` | 无（热力图分布） |
| 8 | Slowest Endpoints (p99) | Table | `histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, handler)) * 1000` | 无（表格排序） |

### 指标来源

以上面板全部基于 Prometheus 从 API 服务 `/metrics` 端点抓取的 4 个核心指标：

| 指标名 | 类型 | 说明 |
|-------|------|------|
| `http_requests_total` | Counter | 请求总数（按 method、handler、status 分组） |
| `http_request_duration_seconds` | Histogram | 请求延迟分布 |
| `http_request_size_bytes` | Histogram | 请求体大小分布 |
| `http_response_size_bytes` | Histogram | 响应体大小分布 |

## 3. Jaeger 分布式追踪

### 自动埋点架构

```python
from packages.common.tracing import setup_tracing, instrument_app

setup_tracing(service_name="aegisrag-api")   # OTLP gRPC → Jaeger
instrument_app(app)                            # 注入 FastAPI/httpx/Redis/SQLAlchemy
```

### 追踪链路

每条 `/query` RAG 请求在 Jaeger 中可展开为完整调用树：

```
POST /query                           ← FastAPI 自动埋点（server_request_hook 重命名为路由模式）
├── HTTP POST /embeddings             ← httpx 自动传播 W3C TraceContext（traceparent header）
│   └── Embedding API 调用
├── HTTP POST /chat/completions       ← LLM 调用（DeepSeek API），同样传播 traceparent
│   └── LLM 推理（最外层 Span，耗时最长）
├── REDIS GET cache:prefix:*          ← Redis 自动埋点（缓存命中查询）
├── REDIS SET cache:prefix:*          ← Redis 缓存写入
├── SQL SELECT * FROM documents       ← SQLAlchemy 自动埋点（pgvector 向量检索）
├── SQL SELECT * FROM chunks          ← 文档块查询
└── HTTP POST /rerank                 ← 重排序服务调用（httpx 传播 TraceContext）
```

### 技术要点

- **W3C TraceContext**：所有出站 HTTP 请求（LLM、Embedding、Reranker）自动携带 `traceparent` header，确保跨服务 Span 归入同一 Trace。
- **Span 重命名**：`_server_request_hook` 将 FastAPI 自动生成的 span name 重写为 `{METHOD} {route.path}`（如 `POST /query`），在 Jaeger UI 中更清晰。
- **优雅降级**：OpenTelemetry SDK 导入失败或 OTLP 端点不可达时，追踪功能静默关闭，不影响主业务。
- **采样策略**：`ALWAYS_ON`（全量采样），开发/演示阶段保留所有 Trace。

### Jaeger UI

访问 `http://localhost:16686`，按 Service 名称（`aegisrag-api`）筛选后可见所有 Trace 列表，点击任意 Trace 查看 Gantt 图调用链。

## 4. 访问方式

### Docker Compose（本地开发）

```bash
# 启动可观测性组件
docker compose --env-file .env -f docker/compose.yaml up -d prometheus grafana

# Grafana（Docker 映射至 3101，内部 3000）
# 默认凭证：admin / admin
open http://localhost:3101

# Jaeger
open http://localhost:16686

# Prometheus
open http://localhost:9090
```

### Kubernetes（Helm）

```bash
helm install aegisrag ./helm/aegisrag -n aegisrag --create-namespace \
  --set observability.grafana.enabled=true \
  --set observability.jaeger.enabled=true
```

部署后 Grafana 在 K8s Service 端口 3000，Jaeger UI 在 16686。

## 5. 面试展示清单

按以下顺序演示，预计 5-8 分钟：

- [ ] **Grafana 仪表板**：打开 AegisRAG — API Observability 仪表板，重点展示 Request Rate / Avg Latency / Error Rate 三个 Stat 面板的实时数值和颜色阈值变化。
- [ ] **请求分布**：向下滚动展示 Requests/sec by Endpoint（按 handler 分线）和 Latency by Endpoint (p95) 两张时序图，说明可通过端点维度快速定位瓶颈。
- [ ] **Jaeger 全链路**：选一条最近的 `/query` Trace，展开 Gantt 图展示完整调用链（API → LLM → Embedding → PostgreSQL → Redis → Reranker），指出各级 Span 耗时占比。
- [ ] **trace_id 关联**：从 structlog JSON 日志中复制一条 `/query` 请求的 `trace_id`（如 `"trace_id": "a1b2c3d4e5f6..."`），在 Jaeger 搜索框中粘贴搜索，验证日志与 Trace 的一一对应关系。
- [ ] **日志过滤演示**：在终端中 `grep` 同一 `trace_id` 的所有日志行，展示同一请求从入口 -> 鉴权 -> 检索 -> LLM -> 响应的完整日志时间线。
- [ ] **状态码分布**：展示 Status Codes Distribution 柱状图，说明可通过比例快速发现异常状态码突增。
- [ ] **慢端点排查**：展示 Slowest Endpoints (p99) 表格，指出 `/query` 的 p99 延迟由外部 LLM 调用主导，本地 embedding 和检索服务在毫秒级。

> 注意：当前未配置 Prometheus Alertmanager 告警规则。如需展示告警能力，可在 `docker/prometheus/` 下新增 `rules.yml` 并配置 Error Rate > 5% / Latency p95 > 10s 的触发规则。

## 6. 截图方案

> 以下为面试者自行截图的操作指南（按步骤操作即可获得高质量截图）。

### Grafana 截图步骤

1. 启动服务：`docker compose -f docker/compose.yaml up -d`
2. 发起压测流量（让面板有数据）：
   ```bash
   python evaluation/load_test.py --users 10 --duration 60
   ```
3. 在压测运行期间打开 `http://localhost:3101`（admin/admin）
4. 进入 Dashboards → AegisRAG — API Observability
5. **截图 1**：顶部三列 Stat 面板（Request Rate / Avg Latency / Error Rate），展示实时指标和颜色阈值
6. **截图 2**：中间两列时序图（Requests/sec by Endpoint + Latency by Endpoint p95），展示压测期间的多端点负载分布
7. **截图 3**：底部三列面板（Status Codes / Request Size Heatmap / Slowest Endpoints），展示 HTTP 状态码构成和慢端点排名

### Jaeger 截图步骤

1. 确保 Jaeger 运行中：`docker compose -f docker/compose.yaml up -d`（jaeger 默认已在 compose 中）
2. 发送一条 RAG 查询请求：
   ```bash
   curl -X POST http://localhost:8000/query \
     -H "Content-Type: application/json" \
     -d '{"question": "什么是向量检索？"}'
   ```
3. 打开 `http://localhost:16686`
4. Service 下拉选择 `aegisrag-api`，点击 Find Traces
5. **截图 4**：Trace 列表页，展示多条 Trace 及各自的 Span 数和耗时
6. 点击刚产生的 `/query` Trace
7. **截图 5**：Trace 详情 Gantt 图，完整展开所有子 Span，清晰展示 API → LLM → Embedding → PostgreSQL 调用链分层
8. **截图 6**：展开某个 Span（如 LLM 调用），展示 Tags 中的 `http.url`、`http.status_code`、`span.kind` 等属性
