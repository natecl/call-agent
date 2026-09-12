import type { SeenStore } from "./receipt.ts";

export class MemorySeenStore implements SeenStore {
  #ids = new Set<string>();

  async claim(id: string): Promise<"first" | "duplicate"> {
    if (this.#ids.has(id)) {
      return "duplicate";
    }
    this.#ids.add(id);
    return "first";
  }
}

export class CacheSeenStore implements SeenStore {
  #fallback = new MemorySeenStore();

  async claim(id: string): Promise<"first" | "duplicate"> {
    const cache = resolveCache();
    if (cache === null) {
      return this.#fallback.claim(id);
    }
    const key = new Request(`https://seen.invalid/webhook/${id}`);
    if (await cache.match(key)) {
      return "duplicate";
    }
    await cache.put(
      key,
      new Response("1", { headers: { "Cache-Control": "max-age=86400" } }),
    );
    return "first";
  }
}

function resolveCache(): Cache | null {
  try {
    if (typeof caches !== "undefined" && "default" in caches) {
      return caches.default;
    }
  } catch {
    // Node and wrangler local have no caches.default. Isolate memory is enough for Telnyx retries.
  }
  return null;
}

export function makeSeen(): SeenStore {
  return new CacheSeenStore();
}
