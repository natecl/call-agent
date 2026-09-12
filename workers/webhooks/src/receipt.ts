export type Provider = "telnyx" | "linq";

export type RejectReason =
  | "missing_public_key"
  | "bad_signature"
  | "stale_timestamp"
  | "malformed"
  | "media_not_terminated_here"
  | "linq_not_implemented";

export type Receipt =
  | { kind: "rejected"; reason: RejectReason }
  | { kind: "duplicate"; id: string; provider: Provider }
  | { kind: "accepted"; id: string; provider: Provider };

export type SeenStore = {
  claim(id: string): Promise<"first" | "duplicate">;
};

export type Env = {
  TELNYX_PUBLIC_KEY?: string;
  UPSTREAM_WEBHOOK_URL?: string;
};
