import type { NextConfig } from "next";

// The FastAPI service (services/api): the REST API this app reads and writes through, the page images, and the
// technical screens that stay server-rendered. Set API_URL (.env.example): in Docker the API's service address, with
// `npm run dev` the API's published port. Rewrites are fixed when the app is built, so the image is built with it.
const API = (process.env.API_URL ?? "").replace(/\/+$/, "");
if (!API) throw new Error("API_URL is not set: where the API answers, e.g. http://localhost:8002 (see .env.example)");

const nextConfig: NextConfig = {
  output: "standalone",
  agentRules: false,          // no generated AGENTS.md / CLAUDE.md here: the project keeps its agent notes local
  // an upload passes through this app to FastAPI, and Next buffers what it passes on (10 MB by default): a day's scan
  // is far bigger (the 288-page sample is 19.8 MB)
  experimental: { proxyClientMaxBodySize: "512mb" },
  async rewrites() {
    return {
      beforeFiles: [],
      // what the browser asks FastAPI for directly: the API, page images and crops, the PDFs, the shared stylesheet
      afterFiles: ["/api/v1/:path*", "/img/:path*", "/crop/:path*", "/documents/:path*", "/static/:path*"].map(
        (source) => ({ source, destination: `${API}${source}` }),
      ),
      // every path this app has no page for (the Teknis screens: /status, /fields, /labels, /context, /knowledge,
      // /teknis/…, and what they load) is still served by FastAPI, so there is one address for everything
      fallback: [{ source: "/:path*", destination: `${API}/:path*` }],
    };
  },
};

export default nextConfig;
