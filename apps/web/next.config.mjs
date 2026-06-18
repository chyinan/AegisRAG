import process from "node:process";

const apiBaseUrl = process.env.RAG_API_BASE_URL || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [
      {
        source: "/api/auth/:path*",
        destination: `${apiBaseUrl}/auth/:path*`
      },
      {
        source: "/api/backend/:path*",
        destination: `${apiBaseUrl}/:path*`
      }
    ];
  }
};

export default nextConfig;
