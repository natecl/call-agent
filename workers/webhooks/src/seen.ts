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
  cache: Cache;

  constructor(cache: Cache) {
    this.cache = cache;
  }

  async claim(id: string): Promise<"first" | "duplicate"> {
    const key = new Request(`https://seen.invalid/webhook/${id}`);
    if (await this.cache.match(key)) {
      return "duplicate";
    }
    await this.cache.put(
      key,
      new Response("1", { headers: { "Cache-Control": "max-age=86400" } }),
    );
    return "first";
  }
}

const memory = new MemorySeenStore();

export function makeSeen(): SeenStore {
  try {
    if (typeof caches !== "undefined" && "default" in caches) {
      return new CacheSeenStore(caches.default);
    }
  } catch {
    // Node and wrangler local have no caches.default. Isolate memory is enough for Telnyx retries.
  }
  return memory;
}
