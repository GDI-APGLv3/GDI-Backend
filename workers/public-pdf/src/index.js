import { AwsClient } from "aws4fetch";

const PATH_RE = /^([a-z0-9]{2,8})\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.pdf$/;

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }

    const path = decodeURIComponent(new URL(request.url).pathname.slice(1));
    const match = PATH_RE.exec(path);
    if (!match) {
      return new Response("Not found", { status: 404 });
    }

    const muni = match[1].toLowerCase();
    const key = `${match[2]}.pdf`;
    const bucket = `gdi-${muni}-publico`;

    const s3 = new AwsClient({
      accessKeyId: env.R2_ACCESS_KEY_ID,
      secretAccessKey: env.R2_SECRET_ACCESS_KEY,
      service: "s3",
      region: "auto",
    });

    const objectUrl = `https://${env.CF_ACCOUNT_ID}.r2.cloudflarestorage.com/${bucket}/${key}`;
    const upstream = await s3.fetch(objectUrl, { method: request.method });

    if (upstream.status === 404) {
      return new Response("Not found", { status: 404 });
    }
    if (!upstream.ok) {
      return new Response("Upstream error", { status: 502 });
    }

    const headers = new Headers();
    headers.set("Content-Type", "application/pdf");
    headers.set("Cache-Control", "public, max-age=3600");
    headers.set("Content-Disposition", `inline; filename="${key}"`);
    const len = upstream.headers.get("Content-Length");
    if (len) headers.set("Content-Length", len);
    const etag = upstream.headers.get("ETag");
    if (etag) headers.set("ETag", etag);

    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: 200,
      headers,
    });
  },
};
