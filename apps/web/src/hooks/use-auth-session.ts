"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { logoutUser, type AuthSession } from "@/lib/auth";

export type AuthNotice = "expired" | "signout_pending" | "signout_confirmed" | "signout_local";
const MAX_TIMER_DELAY = 2_147_483_647;

export function useAuthSession() {
  const client = useQueryClient();
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [notice, setNotice] = useState<AuthNotice | null>(null);
  const authRef = useRef<AuthSession | null>(null);
  const generation = useRef(0);
  const logoutController = useRef<AbortController | null>(null);

  const clearLocalSession = useCallback(() => {
    authRef.current = null;
    setAuth(null);
    // This QueryClient belongs only to this workbench; clear its authenticated data and mutations.
    // Query destruction cancels in-flight reads. Workbench unmount cancels streams and uploads.
    client.clear();
  }, [client]);

  const expire = useCallback((session: AuthSession) => {
    if (authRef.current !== session) return;
    generation.current += 1;
    clearLocalSession();
    setNotice("expired");
    // An expired bearer cannot reliably revoke a session. Expiry never mutates the server.
  }, [clearLocalSession]);

  const authenticate = useCallback((session: AuthSession) => {
    generation.current += 1;
    logoutController.current?.abort();
    logoutController.current = null;
    client.clear();
    if (session.expiresAt !== undefined && session.expiresAt <= Date.now()) {
      clearLocalSession();
      setNotice("expired");
      return;
    }
    authRef.current = session;
    setAuth(session);
    setNotice(null);
  }, [client, clearLocalSession]);

  const signOut = useCallback(() => {
    const session = authRef.current;
    if (session === null) return; // Synchronous guard also covers same-frame repeated clicks.
    const owner = ++generation.current;
    clearLocalSession();
    if (session.mode !== "bearer" || !session.bearerToken) {
      setNotice("signout_local");
      return;
    }
    setNotice("signout_pending");
    const controller = new AbortController();
    logoutController.current = controller;
    void logoutUser(session, controller.signal).then((result) => {
      if (generation.current !== owner || authRef.current !== null || controller.signal.aborted) return;
      setNotice(result === "confirmed" ? "signout_confirmed" : "signout_local");
    }).finally(() => {
      if (logoutController.current === controller) logoutController.current = null;
    });
  }, [clearLocalSession]);

  useEffect(() => {
    if (auth?.expiresAt === undefined) return;
    const deadline = auth.expiresAt;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = () => {
      clearTimeout(timer);
      if (authRef.current !== auth) return;
      const remaining = deadline - Date.now();
      if (remaining <= 0) expire(auth);
      else timer = setTimeout(check, Math.min(remaining, MAX_TIMER_DELAY));
    };
    const onVisible = () => { if (document.visibilityState !== "hidden") check(); };
    check();
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", check);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", check);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [auth, expire]);

  useEffect(() => () => {
    generation.current += 1;
    logoutController.current?.abort();
    logoutController.current = null;
  }, []);

  return { auth, notice, authenticate, signOut };
}
