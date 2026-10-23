// @vitest-environment node
// Node-side config test: vite.config.js loads esbuild, which needs a real Node environment.
import { afterEach, describe, expect, it } from "vitest";
import configFactory from "../../vite.config.js";

const resolve = () => configFactory({ mode: "development", command: "serve" });
const original = process.env.REPORISK_API_PROXY;

afterEach(() => {
  if (original === undefined) delete process.env.REPORISK_API_PROXY;
  else process.env.REPORISK_API_PROXY = original;
});

describe("vite dev proxy", () => {
  it("forwards /api and /health to the API on port 8000 by default", () => {
    delete process.env.REPORISK_API_PROXY;
    const { proxy } = resolve().server;
    expect(proxy["/api"].target).toBe("http://127.0.0.1:8000");
    expect(proxy["/health"].target).toBe("http://127.0.0.1:8000");
  });

  it("follows REPORISK_API_PROXY, e.g. when the API runs on another port", () => {
    process.env.REPORISK_API_PROXY = "http://127.0.0.1:8001";
    expect(resolve().server.proxy["/api"].target).toBe("http://127.0.0.1:8001");
  });

  it("keeps the browser's Host header so the API's same-origin check passes", () => {
    // Regression: Vite's string shorthand sets changeOrigin, which made login fail
    // with "Requests from this origin are not allowed".
    expect(resolve().server.proxy["/api"].changeOrigin).toBe(false);
  });
});
