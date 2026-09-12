export type AppBase =
  | { kind: "missing" }
  | { kind: "invalid" }
  | { kind: "origin"; origin: string };

export type LinqPostResult =
  | { kind: "missing_app_base_url" }
  | { kind: "invalid_app_base_url" }
  | { kind: "app_unreachable" }
  | { kind: "upstream"; response: Response };

const FORWARDED_HEADERS = [
  "webhook-id",
  "webhook-timestamp",
  "webhook-signature",
  "content-type",
] as const;

export function isLinqPath(pathname: string): boolean {
  return pathname === "/webhooks/linq" || pathname === "/linq/webhook";
}

export function parseAppBaseUrl(raw: string | undefined): AppBase {
  const trimmed = raw?.trim() ?? "";
  if (!trimmed) {
    return { kind: "missing" };
  }
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return { kind: "invalid" };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { kind: "invalid" };
  }
  if (url.pathname !== "" && url.pathname !== "/") {
    return { kind: "invalid" };
  }
  if (url.search !== "" || url.hash !== "") {
    return { kind: "invalid" };
  }
  return { kind: "origin", origin: url.origin };
}

async function forwardLinqPost(
  raw: Uint8Array,
  headers: Headers,
  origin: string,
): Promise<LinqPostResult> {
  const outbound = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = headers.get(name);
    if (value !== null) {
      outbound.set(name, value);
    }
  }
  if (!outbound.has("content-type")) {
    outbound.set("content-type", "application/json");
  }
  try {
    const response = await fetch(`${origin}/linq/webhook`, {
      method: "POST",
      headers: outbound,
      body: raw,
    });
    return { kind: "upstream", response };
  } catch {
    return { kind: "app_unreachable" };
  }
}

export async function postLinq(
  raw: Uint8Array,
  headers: Headers,
  rawBase: string | undefined,
): Promise<LinqPostResult> {
  const target = parseAppBaseUrl(rawBase);
  if (target.kind === "missing") {
    return { kind: "missing_app_base_url" };
  }
  if (target.kind === "invalid") {
    return { kind: "invalid_app_base_url" };
  }
  return forwardLinqPost(raw, headers, target.origin);
}
