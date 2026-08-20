const configuredBase = (import.meta.env.VITE_API_BASE || "/api").trim();
export const API = configuredBase.replace(/\/$/, "");

export const NO_COVER = "data:image/svg+xml," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="252" viewBox="0 0 180 252">' +
  '<rect width="180" height="252" fill="#e8e2d8"/>' +
  '<path d="M55 76h30a20 20 0 0 1 20 20v80a15 15 0 0 0-15-15H55zM125 76H95a20 20 0 0 0-20 20v80a15 15 0 0 1 15-15h35z" fill="none" stroke="#736b60" stroke-width="5" stroke-linejoin="round"/>' +
  '<text x="90" y="206" font-family="Arial,sans-serif" font-size="13" fill="#514b43" text-anchor="middle">Cover unavailable</text>' +
  "</svg>",
);

let unauthorizedHandler = () => {};

export async function readJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return {};
  try {
    return await response.json();
  } catch {
    return {};
  }
}

export async function postJson(path, body) {
  try {
    const response = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: response.ok, data: await readJson(response), response };
  } catch {
    return {
      ok: false,
      data: {
        code: "server_unavailable",
        error: "BookLens could not reach the server. Check that the API is running, then try again.",
      },
      response: null,
    };
  }
}

export async function authFetch(path, token, options = {}) {
  const request = { ...options };
  request.headers = {
    ...(request.headers || {}),
    Authorization: "Bearer " + token,
  };
  const response = await fetch(API + path, request);
  if (response.status === 401) unauthorizedHandler();
  return response;
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === "function" ? handler : () => {};
}
