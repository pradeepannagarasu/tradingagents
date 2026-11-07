import { proxy, type Env } from "../_gateway";
export const onRequest = (ctx: { request: Request; env: Env }) => proxy(ctx.request, ctx.env);
