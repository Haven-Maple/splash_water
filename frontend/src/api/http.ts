const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as {
        detail?: { message?: string; payload?: unknown } | string;
        message?: string;
        payload?: unknown;
      };
      message =
        typeof payload.detail === "string"
          ? payload.detail
          : payload.detail?.message ?? payload.message ?? message;
      const extraPayload = typeof payload.detail === "string" ? payload.payload : payload.detail?.payload ?? payload.payload;
      if (extraPayload) {
        message = `${message} | payload=${JSON.stringify(extraPayload)}`;
      }
    } catch {
      // Ignore parsing failures and use default message.
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  return handleResponse<T>(response);
}

export { API_BASE_URL };
