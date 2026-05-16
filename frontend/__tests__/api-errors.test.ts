import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, NetworkError } from "../src/lib/api.ts";

test("ApiError is an Error subclass with status + body fields", () => {
  const e = new ApiError(404, "session not found");
  assert.ok(e instanceof Error);
  assert.ok(e instanceof ApiError);
  assert.equal(e.status, 404);
  assert.equal(e.body, "session not found");
  assert.equal(e.name, "ApiError");
  assert.match(e.message, /404/);
  assert.match(e.message, /session not found/);
});

test("ApiError accepts a custom message", () => {
  const e = new ApiError(500, "internal", "kloc backend exploded");
  assert.equal(e.message, "kloc backend exploded");
});

test("NetworkError is an Error subclass and is not an ApiError", () => {
  const e = new NetworkError("DNS failed");
  assert.ok(e instanceof Error);
  assert.ok(e instanceof NetworkError);
  assert.equal(e instanceof ApiError, false);
  assert.equal(e.name, "NetworkError");
  assert.equal(e.message, "DNS failed");
});

test("NetworkError preserves cause", () => {
  const cause = new Error("connection refused");
  const e = new NetworkError("network unreachable", cause);
  assert.equal(e.cause, cause);
});

test("instanceof discriminates the two classes in a catch", () => {
  const errors: unknown[] = [
    new ApiError(400, "bad request"),
    new NetworkError("no route to host"),
    new Error("something else"),
  ];
  const classes = errors.map((e) => {
    if (e instanceof ApiError) return "api";
    if (e instanceof NetworkError) return "network";
    return "other";
  });
  assert.deepEqual(classes, ["api", "network", "other"]);
});
