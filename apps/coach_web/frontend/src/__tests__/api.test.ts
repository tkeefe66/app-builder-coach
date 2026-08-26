import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, del, get, post } from "../api";

afterEach(() => vi.restoreAllMocks());

describe("api.get", () => {
  it("returns parsed json on 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    expect(await get("/api/overview")).toEqual({ ok: 1 });
  });
  it("redirects to /login on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("{}", { status: 401 })));
    const assign = vi.fn();
    vi.stubGlobal("location", { assign, pathname: "/overview" } as any);
    await expect(get("/api/overview")).rejects.toThrow(ApiError);
    expect(assign).toHaveBeenCalledWith("/login");
  });
  it("throws ApiError with status on 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    await expect(get("/api/overview")).rejects.toMatchObject({ status: 500 });
  });
});

describe("write helpers", () => {
  it("posts JSON and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await post("/api/goals", { title: "x" })).toEqual({ id: 1 });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body as string)).toEqual({ title: "x" });
  });

  it("throws ApiError with the server detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "cross-origin write rejected" }),
        { status: 403 })));
    await expect(post("/api/goals", {})).rejects.toMatchObject({ status: 403 });
  });

  it("deletes without a body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await del("/api/goals/1");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
