import { configDefaults, defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

const certPath = path.resolve(__dirname, "certs/aunic-cert.pem");
const keyPath = path.resolve(__dirname, "certs/aunic-key.pem");
const httpsConfig =
  fs.existsSync(certPath) && fs.existsSync(keyPath)
    ? {
        cert: fs.readFileSync(certPath),
        key: fs.readFileSync(keyPath),
      }
    : undefined;

function debugLogPlugin(): Plugin {
  return {
    name: "aunic-debug-log",
    configureServer(server) {
      server.middlewares.use("/debug/log", (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end();
          return;
        }
        const chunks: Buffer[] = [];
        req.on("data", (chunk) => chunks.push(chunk as Buffer));
        req.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf8");
          const ts = new Date().toISOString().slice(11, 23);
          process.stdout.write(`[mobile ${ts}] ${body}\n`);
          res.statusCode = 204;
          res.end();
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), debugLogPlugin()],
  server: {
    host: "0.0.0.0",
    port: 5174,
    strictPort: true,
    https: httpsConfig,
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8767",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    https: httpsConfig,
  },
  build: {
    target: "es2022",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
