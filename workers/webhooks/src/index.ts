import { createHandler } from "./handler.ts";
import type { Env } from "./receipt.ts";
import { makeSeen } from "./seen.ts";

const seen = makeSeen();

export default {
  fetch(
    request: Request,
    env: Env,
    ctx: { waitUntil(promise: Promise<unknown>): void },
  ): Promise<Response> {
    return createHandler(env, seen).fetch(request, env, ctx);
  },
};
