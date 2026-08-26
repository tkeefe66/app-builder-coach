export class ApiError extends Error {
  constructor(public status: number, detail: string) {
    super(detail);
  }
}

export async function get(path: string): Promise<any> {
  const resp = await fetch(path, { credentials: "same-origin" });
  if (resp.status === 401) {
    location.assign("/login");
    throw new ApiError(401, "login required");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export async function login(password: string): Promise<boolean> {
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ password }),
  });
  return resp.ok;
}

async function write(path: string, method: string, body?: unknown): Promise<any> {
  const resp = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 401) {
    location.assign("/login");
    throw new ApiError(401, "login required");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

// The browser sets Origin automatically on these; the server's
// require_same_origin dependency is what checks it.
export const post = (path: string, body: unknown) => write(path, "POST", body);
export const patch = (path: string, body: unknown) => write(path, "PATCH", body);
export const del = (path: string) => write(path, "DELETE");
