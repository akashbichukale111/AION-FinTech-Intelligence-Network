import { useCallback, useEffect, useState } from "react";
import type { PayPalState } from "./types";

export const API_BASE: string = (import.meta.env.VITE_AION_API_BASE ?? "").replace(/\/$/, "");

export const PAYPAL_LABEL: Record<PayPalState, string> = {
  checking: "Checking",
  not_configured: "Not configured",
  configuration_required: "Configuration required",
  connected: "Sandbox connected",
  error: "Connection error",
};

/** Reads PayPal Sandbox status from a real backend. Never assumes a connection. */
export function usePayPalStatus() {
  const [state, setState] = useState<PayPalState>(API_BASE ? "checking" : "not_configured");

  const check = useCallback(async () => {
    if (!API_BASE) { setState("not_configured"); return; }
    setState("checking");
    try {
      const res = await fetch(`${API_BASE}/paypal/status`, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as { configured?: boolean; verified?: boolean };
      setState(body.configured && body.verified ? "connected" : "configuration_required");
    } catch {
      setState("error");
    }
  }, []);

  useEffect(() => { void check(); }, [check]);
  return { state, recheck: check };
}

/** Asks the backend to create a sandbox order and returns its approval URL. */
export async function createSandboxOrder(): Promise<string> {
  const res = await fetch(`${API_BASE}/paypal/create-order`, { method: "POST", headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`Backend returned ${res.status}`);
  const body = (await res.json()) as { approveUrl?: string };
  if (!body.approveUrl) throw new Error("Backend did not return an approval URL");
  return body.approveUrl;
}
