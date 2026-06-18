"use client";

import { KeyRound, LogIn, ShieldCheck } from "lucide-react";
import { useState } from "react";
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
  onAuthenticated
}: Readonly<{
  language: Language;
  onLanguageChange: (language: Language) => void;
  onAuthenticated: (auth: AuthSession) => void;
}>) {
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  async function handleLogin() {
    if (username.trim().length === 0 || password.trim().length === 0) return;
    setLoginLoading(true);
    setLoginError(null);
    try {
      const session = await loginUser(username.trim(), password);
      onAuthenticated(session);
    } catch (error) {
      setLoginError(
        error instanceof Error ? error.message : text(uiText.loginError, language)
      );
    } finally {
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
          <Card>
            <div className="actions-row">
              <LogIn aria-hidden="true" />
              <strong>{text(uiText.usernameLogin, language)}</strong>
            </div>
            <label className="scope-label" htmlFor="login-username">
              {text(uiText.username, language)}
            </label>
            <Input
              id="login-username"
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
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                setLoginError(null);
              }}
              placeholder={text(uiText.passwordPlaceholder, language)}
              type="password"
              autoComplete="current-password"
              onKeyDown={(event) => {
                if (event.key === "Enter") handleLogin();
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
              disabled={username.trim().length === 0 || password.trim().length === 0 || loginLoading}
              onClick={handleLogin}
            >
              <LogIn aria-hidden="true" />
              {loginLoading ? text(uiText.signingIn, language) : text(uiText.signIn, language)}
            </Button>
          </Card>

          <Card>
            <div className="actions-row">
              <KeyRound aria-hidden="true" />
              <strong>{text(uiText.enterpriseJwt, language)}</strong>
            </div>
            <label className="scope-label" htmlFor="jwt-token">
              {text(uiText.bearerToken, language)}
            </label>
            <Input
              id="jwt-token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={text(uiText.jwtPlaceholder, language)}
              type="password"
            />
            <Button
              type="button"
              variant="primary"
              disabled={token.trim().length === 0}
              onClick={() =>
                onAuthenticated({
                  mode: "bearer",
                  label: text(uiText.enterpriseJwt, language),
                  roles: [],
                  permissions: [],
                  bearerToken: token.trim()
                })
              }
            >
              <ShieldCheck aria-hidden="true" />
              {text(uiText.continueWithJwt, language)}
            </Button>
          </Card>
        </div>
      </section>
    </main>
  );
}
