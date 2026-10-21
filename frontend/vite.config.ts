import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// The artifact runtime: libraries an artifact (a model-made page) may use,
// served from the Hub itself so artifacts work without internet access.
const RUNTIME: Record<string, string> = {
  "react.js": "react/umd/react.production.min.js",
  "react-dom.js": "react-dom/umd/react-dom.production.min.js",
  "babel.js": "@babel/standalone/babel.min.js",
  "tailwind.js": "@tailwindcss/browser/dist/index.global.js",
  "mermaid.js": "mermaid/dist/mermaid.min.js",
};

function artifactRuntime(): Plugin {
  // Read straight from node_modules: these files are not in the packages' "exports".
  const load = (name: string) => readFileSync(fileURLToPath(new URL(`./node_modules/${RUNTIME[name]}`, import.meta.url)));
  return {
    name: "slq-artifact-runtime",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const m = req.url?.match(/^\/artifact-runtime\/([\w.-]+)$/);
        if (!m || !RUNTIME[m[1]]) return next();
        res.setHeader("Content-Type", "text/javascript; charset=utf-8");
        res.end(load(m[1]));
      });
    },
    generateBundle() {
      for (const name of Object.keys(RUNTIME)) {
        this.emitFile({ type: "asset", fileName: `artifact-runtime/${name}`, source: load(name) });
      }
    },
  };
}

const backend = process.env.HUB_DEV_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), artifactRuntime()],
  server: {
    port: 5173,
    proxy: {
      "/hub": { target: backend, changeOrigin: false },
      "/api": { target: backend, changeOrigin: false },
      "/v1": { target: backend, changeOrigin: false },
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 1500,
  },
});
