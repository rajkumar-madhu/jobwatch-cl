/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  output: "standalone",          // R39: minimal runtime image (see infra/docker/Dockerfile.web)
  poweredByHeader: false,
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_INGEST_URL: process.env.NEXT_PUBLIC_INGEST_URL || "",   // R49: set in real deployments
    NEXT_PUBLIC_SALES_EMAIL: process.env.NEXT_PUBLIC_SALES_EMAIL || "", // R51: Enterprise "Talk to us"
  },
};
