import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const contentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'"
].join("; ");

const developmentContentSecurityPolicy = contentSecurityPolicy.replace(
  "script-src 'self'",
  "script-src 'self' 'unsafe-inline'"
);

export default defineConfig({
  plugins: [
    react(),
    {
      name: "agent-trace-production-csp",
      apply: "build",
      transformIndexHtml: {
        order: "post",
        handler: () => [
          {
            tag: "meta",
            attrs: {
              "http-equiv": "Content-Security-Policy",
              content: contentSecurityPolicy.replace("; frame-ancestors 'none'", "")
            },
            injectTo: "head"
          }
        ]
      }
    }
  ],
  server: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    headers: {
      "Content-Security-Policy": developmentContentSecurityPolicy
    },
    proxy: {
      "/api": process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000"
    }
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    headers: {
      "Content-Security-Policy": contentSecurityPolicy
    }
  }
});
