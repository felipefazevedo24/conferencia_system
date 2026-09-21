import { timingSafeEqual } from "node:crypto";

import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function secureEqual(candidate: string, expected: string): boolean {
  const candidateBuffer = Buffer.from(candidate);
  const expectedBuffer = Buffer.from(expected);
  return (
    candidateBuffer.length === expectedBuffer.length &&
    timingSafeEqual(candidateBuffer, expectedBuffer)
  );
}

function centralAccessPlugin(username: string, password: string): Plugin {
  return {
    name: "central-access-gate",
    configureServer(server) {
      if (!username || !password) return;

      server.middlewares.use((request, response, next) => {
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("X-Frame-Options", "SAMEORIGIN");
        response.setHeader("Referrer-Policy", "same-origin");
        response.setHeader("Cache-Control", "no-store");

        const authorization = request.headers.authorization ?? "";
        const [scheme, encodedCredentials] = authorization.split(" ", 2);
        let suppliedUsername = "";
        let suppliedPassword = "";

        if (scheme?.toLowerCase() === "basic" && encodedCredentials) {
          const decoded = Buffer.from(encodedCredentials, "base64").toString("utf8");
          const separator = decoded.indexOf(":");
          if (separator >= 0) {
            suppliedUsername = decoded.slice(0, separator);
            suppliedPassword = decoded.slice(separator + 1);
          }
        }

        if (
          secureEqual(suppliedUsername, username) &&
          secureEqual(suppliedPassword, password)
        ) {
          next();
          return;
        }

        response.statusCode = 401;
        response.setHeader("WWW-Authenticate", 'Basic realm="Columbia - Acesso interno"');
        response.setHeader("Content-Type", "text/plain; charset=utf-8");
        response.end("Autenticação necessária.");
      });
    }
  };
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");
  const disableCentralAuthForE2E =
    process.env.CENTRAL_AUTH_DISABLED_FOR_E2E === "1";

  return {
    plugins: [
      centralAccessPlugin(
        disableCentralAuthForE2E ? "" : (environment.CENTRAL_AUTH_USERNAME ?? ""),
        disableCentralAuthForE2E ? "" : (environment.CENTRAL_AUTH_PASSWORD ?? "")
      ),
      react()
    ],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      allowedHosts: [".trycloudflare.com", "ppcp.columbiamachine.com.br"],
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true
        }
      }
    },
    build: {
      sourcemap: true,
      target: "es2022"
    }
  };
});
