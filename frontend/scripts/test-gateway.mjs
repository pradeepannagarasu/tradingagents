import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, "..", "node_modules", ".gw-test.mjs");
execFileSync(path.join(here, "..", "node_modules", ".bin", "esbuild"), [path.join(here, "..", "functions", "_gateway.ts"), "--bundle", "--format=esm", "--platform=neutral", `--outfile=${out}`]);
const { proxy } = await import(out);
const { subtle } = crypto;
const kp = await subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1,0,1]), hash: "SHA-256" }, true, ["sign","verify"]);
const jwk = { ...(await subtle.exportKey("jwk", kp.publicKey)), kid: "k1" };
const b64 = o => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
async function tok(claims) {
  const h = b64({ alg: "RS256", kid: "k1" }), p = b64({ aud: ["AUD"], iss: "https://t.cloudflareaccess.com", exp: Math.floor(Date.now()/1000)+300, ...claims });
  const s = Buffer.from(await subtle.sign("RSASSA-PKCS1-v1_5", kp.privateKey, new TextEncoder().encode(`${h}.${p}`))).toString("base64url");
  return `${h}.${p}.${s}`;
}
let seen = null;
globalThis.fetch = async (url, init) => {
  if (String(url).includes("/cdn-cgi/access/certs")) return new Response(JSON.stringify({ keys: [jwk] }));
  seen = { url: String(url), headers: Object.fromEntries(new Headers(init.headers)) };
  return new Response('{"ok":1}', { status: 200, headers: { "set-cookie": "x=1" } });
};
const env = { ACCESS_TEAM_DOMAIN: "t.cloudflareaccess.com", ACCESS_AUD: "AUD", ALLOWED_EMAILS: "me@x.com", BACKEND_ORIGIN: "https://api.example", ACCESS_CLIENT_ID: "cid", ACCESS_CLIENT_SECRET: "sec" };
const call = async (t, e = env) => (await proxy(new Request("https://m.example/api/system?a=1", { headers: t ? { "Cf-Access-Jwt-Assertion": t, Cookie: "CF_Authorization=zzz" } : {} }), e));
const r1 = await call(await tok({ email: "me@x.com" }));
console.log("allowed:", r1.status, seen.url, "fwd user:", seen.headers["x-triagent-user"], "cookie forwarded:", "cookie" in seen.headers, "set-cookie stripped:", !r1.headers.get("set-cookie"));
console.log("no token:", (await call(null)).status);
console.log("wrong email:", (await call(await tok({ email: "evil@x.com" }))).status);
console.log("wrong aud:", (await call(await tok({ email: "me@x.com", aud: ["OTHER"] }))).status);
console.log("expired:", (await call(await tok({ email: "me@x.com", exp: 1 }))).status);
console.log("tampered:", (await call((await tok({ email: "me@x.com" })).slice(0, -4) + "AAAA")).status);
console.log("unconfigured:", (await call(await tok({ email: "me@x.com" }), { ...env, ACCESS_CLIENT_SECRET: "" })).status);
const results = [r1.status, (await call(null)).status];
if (r1.status !== 200 || seen.headers["x-triagent-user"] !== "me@x.com" || "cookie" in seen.headers) { console.error("FAIL"); process.exit(1); }
console.log("gateway tests passed");
