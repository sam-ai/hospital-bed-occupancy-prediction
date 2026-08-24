import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow WebSocket connections to the FastAPI backend
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:",
              "style-src 'self' 'unsafe-inline'",
              "connect-src 'self' ws: wss: http: https:",
              "img-src 'self' data: blob:",
              "worker-src 'self' blob:",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
