"use client";

import { KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { PERSONAS, type AuthSession, type PersonaKey } from "@/lib/auth";

export function AuthGate({ onAuthenticated }: Readonly<{ onAuthenticated: (auth: AuthSession) => void }>) {
  const [token, setToken] = useState("");

  return (
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-copy">
          <div className="brand">
            <span className="brand-mark">R</span>
            <span>Enterprise RAG Workbench</span>
          </div>
          <h1 id="auth-title" style={{ marginTop: 28 }}>
            可信企业知识工作台
          </h1>
          <p>
            选择本地演示身份或输入企业 JWT。前端只透传身份上下文；tenant、权限、ACL、
            citation 和工具授权仍由后端决定。
          </p>
          <div className="scope-stack">
            <strong>安全边界</strong>
            <span className="muted">不在浏览器保存 token，不在前端扩大 roles 或 permissions。</span>
          </div>
        </div>

        <div className="auth-form">
          <div>
            <h2 className="surface-title">本地角色</h2>
            <p className="muted">仅用于 dev/test auth headers，生产环境应接入企业 SSO/JWT。</p>
          </div>
          <div className="persona-grid">
            {(Object.keys(PERSONAS) as PersonaKey[]).map((key) => {
              const persona = PERSONAS[key];
              const Icon = persona.icon;
              return (
                <button
                  key={key}
                  type="button"
                  className="persona-button"
                  onClick={() => onAuthenticated(persona)}
                >
                  <Icon aria-hidden="true" />
                  <strong>{persona.label}</strong>
                  <span>{persona.summary}</span>
                </button>
              );
            })}
          </div>

          <div className="surface">
            <div className="actions-row">
              <KeyRound aria-hidden="true" />
              <strong>企业 JWT</strong>
            </div>
            <label className="scope-label" htmlFor="jwt-token">
              Bearer token
            </label>
            <input
              id="jwt-token"
              className="field"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Paste JWT for backend AuthContext"
              type="password"
            />
            <button
              type="button"
              className="primary-button"
              disabled={token.trim().length === 0}
              onClick={() =>
                onAuthenticated({
                  mode: "bearer",
                  label: "企业 JWT",
                  roles: [],
                  permissions: [],
                  bearerToken: token.trim()
                })
              }
            >
              <ShieldCheck aria-hidden="true" />
              Continue with JWT
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
