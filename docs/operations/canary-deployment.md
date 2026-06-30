# Canary 部署策略

**日期**: 2026-06-30
**适用范围**: AegisRAG Kubernetes 部署（Helm Chart `helm/aegisrag/`）

---

## 1. 概述

AegisRAG 是多租户 RAG 平台，API 版本升级直接关联检索质量与生成一致性。
检索排序退化、模型切换质量下降、新索引策略不兼容，任何问题都会影响业务，
**全量切换 + 出错回滚的粗暴发布模式不可接受**。

Canary 部署在同集群并行运行新旧两版本 API，逐步迁移流量，
以真实流量验证质量与稳定性，确保检索质量不回退、P95 延迟可控、错误率不恶化后全量切换。

| 核心需求 | 说明 |
|---------|------|
| 多租户隔离 | `tenant_id` 隔离，升级不破坏租户边界 |
| 检索质量不回退 | RAGAS faithfulness / context_relevancy ≥ stable 的 95% |
| 模型切换风险 | LLM/Embedding/Rerank 变更需独立 canary 周期验证 |
| 零感知回滚 | 一键切回 stable，不影响已有租户 |

---

## 2. 策略设计

### 2.1 Helm 双 Deployment 模式

利用现有 `helm/aegisrag/templates/api.yaml` 模板，通过 values 部署 stable 与 canary 两套 Deployment。
共用同一数据库、Redis、MinIO，Pod 以 label `version: stable|canary` 区分。

```yaml
# values-canary.yaml
api:
  stable:
    enabled: true
    replicas: 2
    image: { repository: ghcr.io/chyinan/aegisrag-api, tag: v1.2.0 }
    version: stable
  canary:
    enabled: true
    replicas: 1
    image: { repository: ghcr.io/chyinan/aegisrag-api, tag: v1.3.0-rc1 }
    version: canary
```

### 2.2 流量分割

**方案 A：Nginx Ingress Canary Annotations（推荐，零新依赖）**

```yaml
# k8s ingress-canary.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: aegisrag-api-canary
  annotations:
    nginx.ingress.kubernetes.io/canary: "true"
    nginx.ingress.kubernetes.io/canary-weight: "10"
    nginx.ingress.kubernetes.io/canary-by-header: "X-Canary"
    nginx.ingress.kubernetes.io/canary-by-header-value: "true"
spec:
  rules:
    - host: aegisrag.example.com
      http:
        paths:
          - pathType: Prefix
            path: /
            backend:
              service: { name: aegisrag-api, port: { number: 8000 } }
```

权重调整：

```bash
kubectl annotate ingress aegisrag-api-canary \
  nginx.ingress.kubernetes.io/canary-weight="50" --overwrite   # 提升到 50%
kubectl annotate ingress aegisrag-api-canary \
  nginx.ingress.kubernetes.io/canary-weight="0" --overwrite    # 归零回滚
```

**方案 B：Istio VirtualService**（需 Service Mesh，当前规模不推荐）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
spec:
  http:
    - route:
        - destination: { host: aegisrag-api, subset: stable }
          weight: 90
        - destination: { host: aegisrag-api, subset: canary }
          weight: 10
```

### 2.3 租户级灰度

AegisRAG 已有 `X-Tenant-ID` Header 机制（`apps/api/dependencies.py:28`），可实现比百分比更精准的租户级灰度：

```yaml
annotations:
  nginx.ingress.kubernetes.io/canary-by-header: "X-Tenant-ID"
  nginx.ingress.kubernetes.io/canary-by-header-value: "tenant-alpha"
```

灰度租户通过 ConfigMap 管理，operator 更新 Ingress annotation 后生效。

**渐进路径**：

| 阶段 | 策略 | 验证要求 |
|------|------|----------|
| 1. 部署 | `canary-weight: 0`，无生产流量 | Pod Ready，健康检查通过 |
| 2. 内部租户 | dev/test 租户路由到 canary | 功能冒烟测试 |
| 3. 1% 流量 | `canary-weight: 1` | 观察 30 分钟 |
| 4. 扩量 | 10% → 50% → 100% | 每阶段观察 30 分钟 |

---

## 3. 质量门

每次流量提升前必须通过以下检查：

### 3.1 RAGAS 评估

```bash
python evaluation/ragas_eval.py \
  --base-url http://aegisrag-api:8000 \
  --header "X-Canary: true" \
  --dataset evaluation/datasets/benchmark.json
```

| 指标 | 阈值 |
|------|------|
| Faithfulness | ≥ stable 的 95% |
| Context Relevancy | ≥ stable 的 95% |
| Answer Relevancy | ≥ stable 的 95% |

### 3.2 延迟与错误率

```promql
# P95 延迟对比（ms）
histogram_quantile(0.95,
  sum(rate(http_request_duration_seconds_bucket{version=~"stable|canary"}[5m])) by (le, version)
) * 1000

# 错误率
sum(rate(http_requests_total{version="canary",status=~"5.."}[5m]))
  / sum(rate(http_requests_total{version="canary"}[5m]))
```

| 指标 | 阈值 |
|------|------|
| P95 延迟 | ≤ stable 的 120% |
| 错误率 | ≤ stable 的 2x，绝对值 ≤ 1% |

---

## 4. 回滚策略

三级响应：

```bash
# 一级（秒级）：缩容 canary → 0
kubectl scale deployment aegisrag-api-canary --replicas=0

# 二级：Ingress weight 归零
kubectl annotate ingress aegisrag-api-canary \
  nginx.ingress.kubernetes.io/canary-weight="0" --overwrite

# 三级：Helm 全量回滚
helm rollback aegisrag -n aegisrag
```

| 触发条件 | 动作 | 响应时间 |
|----------|------|----------|
| 错误率 > stable 3x | 立即缩容 canary → 0 | < 1 分钟 |
| P95 延迟 > stable 150% | canary weight 减半 | < 2 分钟 |
| RAGAS faithfulness < stable 90% | 停止提升，保留当前权重 | — |
| 单租户投诉 | 移除该租户路由 | < 5 分钟 |

---

## 5. 完整流程示例（v1.2.0 → v1.3.0）

```bash
# 1. 构建 canary 镜像
docker build -t ghcr.io/chyinan/aegisrag-api:v1.3.0-rc1 -f apps/api/Dockerfile .
docker push ghcr.io/chyinan/aegisrag-api:v1.3.0-rc1

# 2. 部署 canary（weight=0，不接受生产流量）
helm upgrade aegisrag ./helm/aegisrag -n aegisrag \
  --set api.canary.enabled=true \
  --set api.canary.image.tag=v1.3.0-rc1 \
  --set api.canary.version=canary \
  --set api.stable.image.tag=v1.2.0 \
  --set api.stable.version=stable

# 3. 内部验证（port-forward 直连 canary）
kubectl port-forward -n aegisrag deploy/aegisrag-api-canary 8001:8000 &
curl http://localhost:8001/health

# 4. 1% 灰度 → 观察 30 分钟
kubectl annotate ingress aegisrag-api-canary \
  nginx.ingress.kubernetes.io/canary-weight="1" --overwrite

# 5. 质量门通过 → 逐步提升
bash scripts/canary-quality-gate.sh
kubectl annotate ingress aegisrag-api-canary \
  nginx.ingress.kubernetes.io/canary-weight="10" --overwrite
sleep 1800  # 观察后 → 50% → 100%

# 6. 全量切换：升级 stable，下线 canary
helm upgrade aegisrag ./helm/aegisrag -n aegisrag \
  --set api.stable.image.tag=v1.3.0 \
  --set api.canary.enabled=false

# 7. 清理
kubectl delete deployment aegisrag-api-canary -n aegisrag
kubectl delete ingress aegisrag-api-canary -n aegisrag
```

---

## 6. 监控集成

**Prometheus label**（Deployment 模板中注入）：

```yaml
# helm/aegisrag/templates/api.yaml
metadata:
  labels:
    version: {{ .Values.api.version | default "stable" }}
env:
  - name: OTEL_SERVICE_NAME
    value: "aegisrag-api-{{ .Values.api.version | default \"stable\" }}"
```

**Grafana 对比面板（PromQL）**：

```promql
# 请求速率
sum(rate(http_requests_total{version="stable"}[1m]))
sum(rate(http_requests_total{version="canary"}[1m]))

# P95 延迟（ms，按 handler 分组）
histogram_quantile(0.95,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le, handler, version)
) * 1000

# 错误率（%）
sum(rate(http_requests_total{status=~"5.."}[5m])) by (version)
  / sum(rate(http_requests_total[5m])) by (version) * 100
```

**告警规则**：

```yaml
groups:
  - name: canary
    rules:
      - alert: CanaryErrorRateSpike
        expr: |
          (sum(rate(http_requests_total{version="canary",status=~"5.."}[5m]))
           / sum(rate(http_requests_total{version="canary"}[5m])))
          > 3 * (sum(rate(http_requests_total{version="stable",status=~"5.."}[5m]))
                 / sum(rate(http_requests_total{version="stable"}[5m])))
        for: 2m
        labels: { severity: warning }
        annotations:
          summary: "Canary 错误率超 stable 3 倍，建议缩容"
      - alert: CanaryLatencyDegradation
        expr: |
          histogram_quantile(0.95,
            sum(rate(http_request_duration_seconds_bucket{version="canary"}[5m])) by (le))
          > 1.5 * histogram_quantile(0.95,
            sum(rate(http_request_duration_seconds_bucket{version="stable"}[5m])) by (le))
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Canary P95 延迟超 stable 150%"
```

---

## 7. 注意事项

- **数据库 migration**：必须向后兼容（additive-only），禁止删除/重命名列。Migration 在部署 canary 前执行。
- **模型切换**：LLM/Embedding/Rerank 变更需走独立 canary 周期，不与代码变更混合。
- **Redis 缓存**：Stable 与 Canary 共用 Redis，缓存 key 应含版本前缀（如 `cache:v1.2.0:...`）。
- **Web 前端**：无状态 Next.js 应用，直接滚动更新，无需 canary 流程。
