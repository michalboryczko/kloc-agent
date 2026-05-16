import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ensureMessageIds,
  resolveSessionId,
  validateIncomingBody,
  type IncomingBody,
} from "../src/app/api/agent-proxy/validation.ts";

test("rejects non-object input", () => {
  for (const v of [null, undefined, 42, "x", true, []]) {
    const r = validateIncomingBody(v);
    assert.equal(r.ok, false);
  }
});

test("accepts empty object (all fields optional)", () => {
  const r = validateIncomingBody({});
  assert.equal(r.ok, true);
});

test("rejects messages not array", () => {
  const r = validateIncomingBody({ messages: "not-array" });
  assert.equal(r.ok, false);
});

test("rejects message without role", () => {
  const r = validateIncomingBody({ messages: [{ content: "hi" }] });
  assert.equal(r.ok, false);
});

test("rejects message with non-string role", () => {
  const r = validateIncomingBody({ messages: [{ role: 42 }] });
  assert.equal(r.ok, false);
});

test("rejects message with non-string id", () => {
  const r = validateIncomingBody({ messages: [{ role: "user", id: 42 }] });
  assert.equal(r.ok, false);
});

test("rejects state as array", () => {
  const r = validateIncomingBody({ state: ["foo"] });
  assert.equal(r.ok, false);
});

test("rejects forwardedProps as string", () => {
  const r = validateIncomingBody({ forwardedProps: "x" });
  assert.equal(r.ok, false);
});

test("rejects threadId as number", () => {
  const r = validateIncomingBody({ threadId: 42 });
  assert.equal(r.ok, false);
});

test("rejects runId as object", () => {
  const r = validateIncomingBody({ runId: { x: 1 } });
  assert.equal(r.ok, false);
});

test("rejects tools as object", () => {
  const r = validateIncomingBody({ tools: { x: 1 } });
  assert.equal(r.ok, false);
});

test("rejects context as string", () => {
  const r = validateIncomingBody({ context: "no" });
  assert.equal(r.ok, false);
});

test("accepts a well-formed body", () => {
  const body = {
    threadId: "t1",
    runId: "r1",
    messages: [
      { role: "user", id: "m1", content: "hello" },
      { role: "assistant", content: "hi back" },
    ],
    tools: [],
    context: [],
    state: { session_id: "abc" },
    forwardedProps: { session_id: "abc" },
    properties: {},
    lastEventId: "evt-1",
  };
  const r = validateIncomingBody(body);
  assert.equal(r.ok, true);
});

test("resolveSessionId picks from properties first", () => {
  const body: IncomingBody = {
    properties: { session_id: "from-props" },
    forwardedProps: { session_id: "from-fwd" },
    state: { session_id: "from-state" },
  };
  assert.equal(resolveSessionId(body), "from-props");
});

test("resolveSessionId falls back to forwardedProps", () => {
  const body: IncomingBody = {
    forwardedProps: { session_id: "from-fwd" },
    state: { session_id: "from-state" },
  };
  assert.equal(resolveSessionId(body), "from-fwd");
});

test("resolveSessionId accepts camelCase sessionId in forwardedProps", () => {
  const body: IncomingBody = {
    forwardedProps: { sessionId: "from-camel" },
  };
  assert.equal(resolveSessionId(body), "from-camel");
});

test("resolveSessionId returns null when absent everywhere", () => {
  assert.equal(resolveSessionId({}), null);
});

test("ensureMessageIds synthesises missing ids", () => {
  const msgs = ensureMessageIds([
    { role: "user", content: "hi" },
    { role: "user", id: "", content: "empty id" },
    { role: "user", id: "keep-me", content: "preserved" },
  ]);
  assert.equal(msgs.length, 3);
  assert.ok(msgs[0].id && msgs[0].id.length > 0);
  assert.ok(msgs[1].id && msgs[1].id.length > 0);
  assert.equal(msgs[2].id, "keep-me");
});

test("ensureMessageIds handles undefined input", () => {
  assert.deepEqual(ensureMessageIds(undefined), []);
});

test("regression: malformed body must not reach upstream — full reject path", () => {
  // Simulate the route's gate: if validate fails, return 400, do not call fetch.
  const malformed = { messages: [{ no_role: true }] };
  const r = validateIncomingBody(malformed);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.match(r.reason, /role must be a string/);
  }
});
