"use client";

// pattern: Imperative Shell

import { LogIn, ShieldCheck } from "lucide-react";
import { useRef, useState } from "react";
import type { AuthNotice } from "@/hooks/use-auth-session";
import { loginUser, type AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { LanguageSelect } from "./language-select";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Input } from "./ui/input";

export function AuthGate({
  language,
  onLanguageChange,
  onAuthenticated,
  notice = null
}: Readonly<{
  language: Language;
  onLanguageChange: (language: Language) => void;
  onAuthenticated: (auth: AuthSession) => void;
  notice?: AuthNotice | null;
}>) {
  const pendingLogin = useRef(false);
  const [tenantId, setTenantId] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  async function handleLogin(): Promise<void> {
    if (
      pendingLogin.current ||
      tenantId.trim().length === 0 ||
      username.trim().length === 0 ||
      password.trim().length === 0
    ) return;
    pendingLogin.current = true;
    setLoginLoading(true);
    setLoginError(null);
    try {
      const session = await loginUser({
        tenantId: tenantId.trim(),
        username: username.trim(),
        password
      });
      setPassword("");
      onAuthenticated(session);
    } catch (error) {
      setLoginError(
        error instanceof Error ? error.message : text(uiText.loginError, language)
      );
    } finally {
      pendingLogin.current = false;
      setLoginLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-copy">
          <div className="auth-copy-header">
            <div className="auth-brand-row">
              <div className="brand">
                <span>Enterprise RAG Workbench</span>
              </div>
              <LanguageSelect language={language} onLanguageChange={onLanguageChange} />
            </div>
            <div className="auth-hero-copy">
              <h1 id="auth-title">{text(uiText.trustedWorkbench, language)}</h1>
              <p>{text(uiText.authIntro, language)}</p>
            </div>
          </div>
          <div className="scope-stack">
            <strong>{text(uiText.securityBoundary, language)}</strong>
            <span className="muted">{text(uiText.securityBoundaryCopy, language)}</span>
          </div>
        </div>

        <div className="auth-form">
          {notice !== null && <Card role="status">{text(sessionNoticeText[notice], language)}</Card>}
          <Card>
            <div className="actions-row">
              <LogIn aria-hidden="true" />
              <strong>{text(uiText.usernameLogin, language)}</strong>
            </div>
            <label className="scope-label" htmlFor="login-tenant-id">
              {text(uiText.tenantId, language)}
            </label>
            <Input
              id="login-tenant-id"
              disabled={loginLoading}
              value={tenantId}
              onChange={(event) => {
                setTenantId(event.target.value);
                setLoginError(null);
              }}
              placeholder={text(uiText.tenantIdPlaceholder, language)}
              type="text"
              autoComplete="organization"
            />
            <label className="scope-label" htmlFor="login-username">
              {text(uiText.username, language)}
            </label>
            <Input
              id="login-username"
              disabled={loginLoading}
              value={username}
              onChange={(event) => {
                setUsername(event.target.value);
                setLoginError(null);
              }}
              placeholder={text(uiText.usernamePlaceholder, language)}
              type="text"
              autoComplete="username"
            />
            <label className="scope-label" htmlFor="login-password">
              {text(uiText.password, language)}
            </label>
            <Input
              id="login-password"
              disabled={loginLoading}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                setLoginError(null);
              }}
              placeholder={text(uiText.passwordPlaceholder, language)}
              type="password"
              autoComplete="current-password"
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleLogin();
              }}
            />
            {loginError !== null && (
              <p className="text-sm text-red-600" role="alert">
                {loginError}
              </p>
            )}
            <Button
              type="button"
              variant="primary"
              disabled={
                tenantId.trim().length === 0 ||
                username.trim().length === 0 ||
                password.trim().length === 0 ||
                loginLoading
              }
              onClick={() => void handleLogin()}
            >
              <LogIn aria-hidden="true" />
              {loginLoading ? text(uiText.signingIn, language) : text(uiText.signIn, language)}
            </Button>
          </Card>

          <Card>
            <div className="actions-row">
              <ShieldCheck aria-hidden="true" />
              <strong>{language === "zh" ? "试点登录方式" : "Pilot sign-in"}</strong>
            </div>
            <p className="muted">
              {language === "zh"
                ? "请使用管理员初始化的租户、用户名和密码。此工作台尚未支持企业 SSO 身份配置，不接受直接粘贴 JWT。"
                : "Use the tenant, username and password provisioned by your administrator. Enterprise SSO identity setup is not supported by this workbench yet; pasted JWT sign-in is unavailable."}
            </p>
          </Card>
        </div>
      </section>
    </main>
  );
}


const sessionNoticeText = {
  expired: {
    en: "Your session expired. Sign in again to continue.",
    zh: "登录会话已过期，请重新登录后继续。"
  },
  signout_pending: {
    en: "Signed out on this device. Confirming server sign-out…",
    zh: "已在此设备退出，正在确认服务端会话撤销…"
  },
  signout_confirmed: {
    en: "Signed out. The server confirmed this session was revoked.",
    zh: "已退出，服务端已确认撤销本次会话。"
  },
  signout_local: {
    en: "Signed out on this device. Server sign-out could not be confirmed; the session may remain active until it expires.",
    zh: "已在此设备退出，但无法确认服务端已撤销会话；该会话可能在过期前仍有效。"
  }
} as const;
