export type Owner = "AURA" | "FORGEX";
export interface PlatformModule { n: number; name: string; desc: string; owner: Owner }
export type PayPalState = "checking" | "not_configured" | "configuration_required" | "connected" | "error";
