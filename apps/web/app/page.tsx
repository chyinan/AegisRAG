"use client";

import { useState } from "react";
import { AuthGate } from "@/components/auth-gate";
import { WorkbenchShell } from "@/components/workbench-shell";
import type { AuthSession } from "@/lib/auth";

export default function Home() {
  const [auth, setAuth] = useState<AuthSession | null>(null);

  if (auth === null) {
    return <AuthGate onAuthenticated={setAuth} />;
  }

  return <WorkbenchShell auth={auth} onSignOut={() => setAuth(null)} />;
}
