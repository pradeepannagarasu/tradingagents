/**
 * Same-origin API gateway (Cloudflare Pages Function).
 *
 * Browser → https://markets.<domain>/api/*  (Cloudflare Access: allowed email only)
 *        → this function: re-verifies the visitor's Access JWT (signature, audience, issuer, expiry, email)
 *        → https://<BACKEND_ORIGIN>/api/*  with an Access *service token* (secret, server-side only)
 *        → Cloudflare Tunnel → FastAPI on the owner's PC (which verifies the service-token JWT again).
 *
 * Env (Pages project settings):
 *   ACCESS_TEAM_DOMAIN   e.g. myteam.cloudflareaccess.com          (plain var)
 *   ACCESS_AUD           AUD tag of the dashboard Access app         (plain var)
 *   ALLOWED_EMAILS       comma-separated                             (plain var)
 *   BACKEND_ORIGIN       e.g. https://markets-api.example.com        (plain var)
 *   ACCESS_CLIENT_ID     service-token client id                     (SECRET)
 *   ACCESS_CLIENT_SECRET service-token client secret                 (SECRET)
 */
export interface Env {
  ACCESS_TEAM_DOMAIN: string; ACCESS_AUD: string; ALLOWED_EMAILS: string; BACKEND_ORIGIN: string;
  ACCESS_CLIENT_ID: string; ACCESS_CLIENT_SECRET: string;
}

let jwksCache: { keys: any[]; at: number } | null = null;

function b64urlToBytes(s: string): Uint8Array {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}

async function verifyAccessJwt(token: string, env: Env): Promise<string> {
  const [h, p, sig] = token.split(".");
  if (!h || !p || !sig) throw new Error("malformed token");
  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  const claims = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
  if (header.alg !== "RS256") throw new Error("bad alg");
  const team = env.ACCESS_TEAM_DOMAIN.replace(/^https?:\/\//, "").replace(/\/$/, "");
  if (!jwksCache || Date.now() - jwksCache.at > 3600_000) {
    const r = await fetch(`https://${team}/cdn-cgi/access/certs`);
    jwksCache = { keys: (await r.json() as any).keys, at: Date.now() };
  }
  const jwk = jwksCache.keys.find(k => k.kid === header.kid);
  if (!jwk) { jwksCache = null; throw new Error("unknown key"); }
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlToBytes(sig), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new Error("bad signature");
  const now = Math.floor(Date.now() / 1000);
  const aud: string[] = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.includes(env.ACCESS_AUD)) throw new Error("bad audience");
  if (claims.iss !== `https://${team}`) throw new Error("bad issuer");
  if (!claims.exp || claims.exp < now) throw new Error("expired");
  const email = String(claims.email || "").toLowerCase();
  const allowed = env.ALLOWED_EMAILS.split(",").map(e => e.trim().toLowerCase()).filter(Boolean);
  if (!email || !allowed.includes(email)) throw new Error("identity not allowed");
  return email;
}

const json = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

export async function proxy(request: Request, env: Env): Promise<Response> {
  for (const k of ["ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "ALLOWED_EMAILS", "BACKEND_ORIGIN", "ACCESS_CLIENT_ID", "ACCESS_CLIENT_SECRET"] as const) {
    if (!env[k]) return json(503, "Gateway not configured");
  }
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return json(401, "Not authenticated");
  let email: string;
  try { email = await verifyAccessJwt(token, env); } catch { return json(401, "Not authenticated"); }

  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, env.BACKEND_ORIGIN);
  const headers = new Headers();
  for (const h of ["content-type", "accept", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version",
                   "sec-websocket-protocol", "sec-websocket-extensions"]) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  // Never forward the visitor's cookies/JWT to the origin; authenticate as the gateway instead.
  headers.set("CF-Access-Client-Id", env.ACCESS_CLIENT_ID);
  headers.set("CF-Access-Client-Secret", env.ACCESS_CLIENT_SECRET);
  headers.set("X-Triagent-User", email);
  try {
    const init: RequestInit = { method: request.method, headers, redirect: "manual" };
    if (!["GET", "HEAD"].includes(request.method)) init.body = await request.arrayBuffer();
    const resp = await fetch(target.toString(), init);
    if (resp.status === 101) return resp;                // WebSocket upgrade passthrough
    if (resp.status >= 300 && resp.status < 400) return json(502, "Backend unreachable"); // e.g. Access login redirect
    const out = new Response(resp.body, resp);
    out.headers.delete("set-cookie");
    return out;
  } catch {
    return json(502, "Backend unreachable — the PC running the backend may be off or asleep");
  }
}
