#!/usr/bin/env python3
"""Generate AegisRAG extended eval dataset (200+ queries) via DeepSeek API."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# ── DeepSeek API config ──────────────────────────────────────────────────────
DS_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = "deepseek-chat"  # deepseek-v4-flash equivalent


def _load_deepseek_key() -> str:
    """Load DEEPSEEK_API_KEY from hermes .env or environment."""
    env_val = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_val:
        return env_val
    env_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if "DEEPSEEK_API_KEY" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""


API_KEY = _load_deepseek_key()
if not API_KEY:
    print("ERROR: DEEPSEEK_API_KEY not found in env or hermes .env", file=sys.stderr)
    sys.exit(1)

# ── Domain definitions ───────────────────────────────────────────────────────
DOMAINS = {
    "tech_doc": {
        "name": "技术文档",
        "description": "技术文档（API 参考、架构说明、配置指南）",
        "count": 40,
        "category": "tech_doc",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-engineer",
        "department": "engineering",
        "roles": ["engineer"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {"category": "tech_doc"},
        "topics": [
            "API authentication", "rate limiting", "webhook payloads", "SDK initialization",
            "error codes", "batch operations", "pagination", "versioning",
            "service mesh", "event bus", "database migrations", "caching strategy",
            "config hot-reload", "feature flags", "logging format", "health checks",
            "TLS configuration", "gRPC setup", "message queues", "circuit breaker",
            "dependency injection", "middleware chain", "request tracing", "API gateway",
            "schema evolution", "connection pooling", "async patterns", "idempotency",
            "backpressure handling", "canary deployments", "blue-green strategy",
            "secrets management", "container orchestration", "service discovery",
            "load balancing", "auto-scaling", "distributed locking", "saga patterns",
            "event sourcing", "CQRS patterns",
        ],
    },
    "policy_compliance": {
        "name": "政策合规",
        "description": "政策合规（HR 政策、安全规范、合规要求）",
        "count": 40,
        "category": "policy_compliance",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-hr",
        "department": "people",
        "roles": ["employee"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {"category": "policy_compliance"},
        "topics": [
            "remote work policy", "overtime compensation", "data protection", "GDPR compliance",
            "onboarding process", "offboarding checklist", "code of conduct", "whistleblower",
            "anti-harassment", "diversity policy", "travel reimbursement", "expense approval",
            "performance review", "promotion criteria", "disciplinary procedures",
            "intellectual property", "social media policy", "conflict of interest",
            "gift acceptance", "export control", "trade compliance", "SOX requirements",
            "ISO certification", "SOC2 audit", "HIPAA compliance", "PCI-DSS scope",
            "vendor risk assessment", "business continuity", "disaster recovery",
            "incident response", "data retention", "privacy impact assessment",
            "third-party audits", "regulatory reporting", "internal audit procedure",
            "non-disclosure agreement", "background checks", "drug testing policy",
            "workplace safety", "emergency procedures",
        ],
    },
    "ops_manual": {
        "name": "运维手册",
        "description": "运维手册（部署、监控、故障排查）",
        "count": 40,
        "category": "ops_manual",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-ops",
        "department": "engineering",
        "roles": ["engineer", "admin"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {"category": "ops_manual"},
        "topics": [
            "deployment rollback", "database backup", "log aggregation", "alert configuration",
            "incident escalation", "runbook execution", "capacity planning", "performance tuning",
            "SSL certificate renewal", "DNS configuration", "firewall rules", "VPN setup",
            "container registry", "image scanning", "pod scheduling", "resource quotas",
            "network policies", "storage provisioning", "secret rotation", "patch management",
            "monitoring dashboards", "SLO definition", "error budget", "on-call rotation",
            "postmortem process", "chaos engineering", "traffic splitting", "A/B testing",
            "data migration", "zero-downtime deploy", "canary analysis", "auto-remediation",
            "cost optimization", "compliance scanning", "vulnerability management",
            "threat detection", "access review", "audit trail", "session recording",
            "break-glass access",
        ],
    },
    "product_knowledge": {
        "name": "产品知识",
        "description": "产品知识（功能说明、版本记录、FAQ）",
        "count": 40,
        "category": "product_knowledge",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-support",
        "department": "support",
        "roles": ["support"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {"category": "product_knowledge"},
        "topics": [
            "search syntax", "filter operators", "bulk import", "export formats",
            "dashboard customization", "alert rules", "notification channels", "API tokens",
            "SSO integration", "role mapping", "audit log querying", "data export",
            "report scheduling", "template variables", "plugin installation", "workspace sharing",
            "version history", "rollback feature", "dark mode", "mobile support",
            "offline mode", "sync frequency", "storage limits", "user provisioning",
            "SCIM support", "webhook testing", "sandbox environment", "API rate cards",
            "enterprise tier", "feature comparison", "migration guide", "deprecation notice",
            "beta program", "early access", "feedback submission", "support SLAs",
            "training materials", "certification path", "community forum", "release notes",
        ],
    },
    "security_audit": {
        "name": "安全审计",
        "description": "安全审计（权限模型、认证流程、审计日志）",
        "count": 40,
        "category": "security_audit",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-security",
        "department": "security",
        "roles": ["security_admin"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {"category": "security_audit"},
        "topics": [
            "RBAC model", "ACL inheritance", "permission escalation", "role hierarchy",
            "MFA enrollment", "session timeout", "token revocation", "OAuth2 flows",
            "SAML assertion", "LDAP sync", "password policy", "account lockout",
            "audit event schema", "log retention", "SIEM integration", "anomaly detection",
            "privileged access", "just-in-time access", "approval workflow", "access certification",
            "data classification", "encryption at rest", "encryption in transit",
            "key management", "HSM integration", "certificate pinning", "CSRF protection",
            "XSS prevention", "SQL injection guard", "content security policy",
            "CORS configuration", "rate limiting", "IP allowlisting", "geo-blocking",
            "threat modeling", "penetration testing", "vulnerability disclosure",
            "bug bounty program", "security champions", "tabletop exercises",
        ],
    },
    "multi_hop": {
        "name": "混合多跳",
        "description": "混合多跳（跨领域综合问题）",
        "count": 20,
        "category": "multi_hop",
        "tenant_id": "tenant-alpha",
        "user_prefix": "user-analyst",
        "department": "engineering",
        "roles": ["engineer"],
        "permissions": ["document:read", "retrieval:query"],
        "metadata_filter": {},
        "topics": [
            "cross-domain integration", "multi-service workflow", "end-to-end trace",
            "system dependency", "impact analysis", "compliance + architecture",
            "security + deployment", "monitoring + policy", "cost + performance",
            "migration + security", "API + audit", "deployment + compliance",
            "scaling + security", "backup + policy", "incident + architecture",
            "config + compliance", "tracing + security", "rollback + policy",
            "access + deployment", "audit + operations",
        ],
    },
}

# ── Prompt template ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert QA dataset generator for an enterprise RAG system called AegisRAG. 
Generate realistic user queries that an employee would ask about internal documentation.

Output MUST be valid JSON: a JSON array of objects, each with:
- "query": a realistic natural-language question (1-2 sentences, English)
- "topic": the sub-topic this query relates to
- "case_id_suffix": a short kebab-case identifier derived from the query

Rules:
- Questions must sound like real enterprise users asking about their system
- Mix of: factual lookup, how-to guides, troubleshooting, policy clarification, configuration questions
- Include some multi-hop questions that span multiple documents
- Include some edge cases: "what if" scenarios, comparison questions
- Include some unanswerable questions (asking about features/data not in the system)
- No questions about the model itself, only about enterprise documentation
- Questions should be concise (10-30 words typical)

Return ONLY the JSON array, no explanation."""


async def generate_queries(
    client: httpx.AsyncClient,
    domain_key: str,
    domain: dict[str, Any],
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """Generate queries for a domain in batches."""
    topics = domain["topics"]
    all_queries: list[dict[str, Any]] = []

    for batch_start in range(0, len(topics), batch_size):
        batch_topics = topics[batch_start : batch_start + batch_size]

        user_prompt = f"""Domain: {domain['name']} ({domain['description']})
Generate {len(batch_topics)} realistic enterprise RAG queries, one for each topic below.
Make queries diverse - mix factual, procedural, troubleshooting, and comparison styles.

Topics (one query per topic):
{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(batch_topics))}

Include about 15% unanswerable queries (questions whose answers are NOT in the system docs).
Output JSON array only."""

        try:
            response = await client.post(
                DS_URL,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DS_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 4096,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"].strip()

            # Extract JSON array
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            batch_queries = json.loads(content)
            all_queries.extend(batch_queries)
            print(f"  [{domain_key}] batch {batch_start//batch_size + 1}: {len(batch_queries)} queries generated")

        except Exception as e:
            print(f"  [{domain_key}] batch {batch_start//batch_size + 1} ERROR: {e}", file=sys.stderr)
            # Generate fallback queries from topics
            for topic in batch_topics:
                all_queries.append({
                    "query": f"What is the policy for {topic}?",
                    "topic": topic,
                    "case_id_suffix": topic.lower().replace(" ", "-").replace("/", "-")[:60],
                })

    return all_queries


def _sanitize_id(s: str) -> str:
    """Replace forbidden substrings with safe alternatives."""
    import re
    replacements = {
        'api_key': 'api-key',
        'access_token': 'access-token',
        'bearer ': 'bearer-',
        'sk-': 's-k-',
        '-----begin': '-begin',
    }
    result = s
    for marker, safe in replacements.items():
        result = re.sub(re.escape(marker), safe, result, flags=re.IGNORECASE)
    return result


def build_dataset(all_queries: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build the full dataset JSON from generated queries."""
    cases: list[dict[str, Any]] = []
    corpus: list[dict[str, Any]] = []
    case_index = 0

    for domain_key, domain in DOMAINS.items():
        queries = all_queries.get(domain_key, [])
        for q in queries:
            case_index += 1
            is_unanswerable = case_index % 7 == 0  # ~14% unanswerable

            topic_slug = q.get("topic", domain_key).lower().replace(" ", "-").replace("/", "-")[:40]
            case_id = _sanitize_id(f"ext-{domain_key}-{case_index:03d}-{q.get('case_id_suffix', topic_slug)}")

            if is_unanswerable:
                case = {
                    "case_id": case_id,
                    "category": domain["category"],
                    "query": q["query"],
                    "tenant_id": domain["tenant_id"],
                    "user_id": f"{domain['user_prefix']}-ext-{case_index:03d}",
                    "roles": list(domain["roles"]),
                    "department": domain["department"],
                    "permissions": list(domain["permissions"]),
                    "metadata_filter": domain["metadata_filter"],
                    "expected_documents": [],
                    "expected_chunks": [],
                    "expected_citations": [],
                    "answerable": False,
                    "expected_no_answer": True,
                    "expected_answer": {
                        "must_include_terms": ["cannot confirm", "not available"],
                        "must_not_include_terms": [],
                    },
                    "attack_type": "none",
                    "top_k": 5,
                }
            else:
                doc_id = f"doc-ext-{domain_key}-{case_index:03d}"
                chunk_id = f"chunk-ext-{domain_key}-{case_index:03d}"
                case = {
                    "case_id": case_id,
                    "category": domain["category"],
                    "query": q["query"],
                    "tenant_id": domain["tenant_id"],
                    "user_id": f"{domain['user_prefix']}-ext-{case_index:03d}",
                    "roles": list(domain["roles"]),
                    "department": domain["department"],
                    "permissions": list(domain["permissions"]),
                    "metadata_filter": domain["metadata_filter"],
                    "expected_documents": [doc_id],
                    "expected_chunks": [chunk_id],
                    "expected_citations": [
                        {
                            "document_id": doc_id,
                            "version_id": "v1",
                            "chunk_id": chunk_id,
                            "page_start": (case_index % 10) + 1,
                            "page_end": (case_index % 10) + 1,
                            "required": True,
                        }
                    ],
                    "answerable": True,
                    "expected_no_answer": False,
                    "expected_answer": {
                        "must_include_terms": [],
                        "must_not_include_terms": [],
                    },
                    "attack_type": "none",
                    "top_k": 5,
                }

                # Add corpus record
                corpus.append({
                    "document_id": doc_id,
                    "version_id": "v1",
                    "chunk_id": chunk_id,
                    "tenant_id": domain["tenant_id"],
                    "content": f"NEED_SEED_DATA: Synthetic {domain['name']} content for topic '{q.get('topic', domain_key)}'. "
                               f"Generated via DeepSeek for eval dataset extension. "
                               f"Replace with actual seed data before CI gate.",
                    "token_count": 35,
                    "source": "synthetic",
                    "source_uri": f"synthetic://rag-eval/{domain_key}/{case_index:03d}",
                    "source_type": "markdown",
                    "page_start": (case_index % 10) + 1,
                    "page_end": (case_index % 10) + 1,
                    "title_path": [domain["name"], q.get("topic", domain_key)],
                    "score": 0.90 + (case_index % 10) * 0.01,
                    "retrieval_method": "hybrid",
                    "acl": {"visibility": "tenant"},
                    "metadata": domain["metadata_filter"],
                    "relevant_case_ids": [case_id],
                })

            cases.append(case)

    return {
        "dataset_version": "rag-extended-v2",
        "cases": cases,
        "corpus": corpus,
    }


async def main() -> None:
    print("=" * 70)
    print("AegisRAG Extended Eval Dataset Generator")
    print(f"Model: {DS_MODEL}")
    print(f"Target: 220 queries across 6 domains")
    print("=" * 70)

    async with httpx.AsyncClient() as client:
        all_queries: dict[str, list[dict[str, Any]]] = {}
        for domain_key, domain in DOMAINS.items():
            print(f"\nGenerating {domain['count']} queries for: {domain['name']}")
            queries = await generate_queries(client, domain_key, domain)
            # Trim to exact count
            all_queries[domain_key] = queries[: domain["count"]]
            print(f"  Total: {len(all_queries[domain_key])} queries")

    dataset = build_dataset(all_queries)

    total_cases = len(dataset["cases"])
    total_corpus = len(dataset["corpus"])
    answerable = sum(1 for c in dataset["cases"] if c["answerable"])
    unanswerable = total_cases - answerable

    print(f"\n{'=' * 70}")
    print(f"Dataset generated:")
    print(f"  Total cases: {total_cases}")
    print(f"  Answerable: {answerable}")
    print(f"  Unanswerable: {unanswerable}")
    print(f"  Corpus records: {total_corpus}")

    output_path = Path(__file__).parent / "rag_extended.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to: {output_path}")
    print(f"File size: {output_path.stat().st_size:,} bytes")
    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
