"use client";

import { KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { PERSONAS, type AuthSession, type PersonaKey } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { personaText, text, uiText } from "@/lib/i18n";
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
          <div>
            <h2 className="surface-title">{text(uiText.localPersonas, language)}</h2>
            <p className="muted">{text(uiText.localPersonasHelp, language)}</p>
          </div>
          <div className="persona-grid">
            {(Object.keys(PERSONAS) as PersonaKey[]).map((key) => {
              const persona = PERSONAS[key];
              const Icon = persona.icon;
              return (
                <Button
                  key={key}
                  type="button"
                  variant="ghost"
                  className="grid min-h-[118px] content-start justify-items-start gap-2 whitespace-normal rounded-md bg-[var(--panel-raised)] p-3.5 text-left text-[var(--ink-primary)] shadow-[inset_0_0_0_1px_var(--line-soft)] hover:bg-white hover:shadow-[inset_0_0_0_1px_rgb(37_99_235_/_0.26),var(--shadow-soft)] [&_svg]:text-[var(--brand)]"
                  onClick={() => onAuthenticated(persona)}
                >
                  <Icon aria-hidden="true" />
                  <strong>{text(personaText[key].label, language)}</strong>
                  <span>{text(personaText[key].summary, language)}</span>
                </Button>
              );
            })}
          </div>

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
