/**
 * Base HTTP client for the Messenger API.
 *
 * - Attaches the Bearer token to every authenticated request.
 * - Unwraps the uniform `{ error: { code, message, details } }` envelope and
 *   normalizes FastAPI 422 validation payloads into ApiError.
 * - Reacts to 401 (purge auth + redirect to /login) and 429 (Retry-After).
 * - Dual-mode URL building:
 *     * NEXT_PUBLIC_API_BASE_URL set  -> absolute URLs to the real backend.
 *     * otherwise (sandbox)           -> relative path + XTransformPort query,
 *       so the built-in gateway forwards to the local mock API service.
 */
import type {
  ApiErrorDetail,
  ApiErrorEnvelope,
  PageQuery,
} from "@/types/api";

const RAW_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");
const DIRECT_MODE = RAW_BASE.length > 0;
const MOCK_API_PORT = process.env.NEXT_PUBLIC_MOCK_API_PORT ?? "3030";

const DEFAULT_TIMEOUT_MS = 20000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: ApiErrorDetail[];
  /** Seconds from the Retry-After header (429 responses only). */
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    options?: { details?: ApiErrorDetail[]; retryAfterSeconds?: number | null }
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = options?.details;
    this.retryAfterSeconds = options?.retryAfterSeconds ?? null;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** Short human message for known backend error codes. */
const CODE_HINTS: Record<string, string> = {
  unauthorized: "Please sign in to continue.",
  forbidden: "You are not allowed to perform this action.",
  not_found: "Not found.",
  conflict: "This conflicts with the current state.",
  validation_error: "The submitted values are invalid.",
  rate_limited: "Too many requests. Please slow down.",
  service_unavailable: "The service is temporarily unavailable.",
  network_error: "Cannot reach the server. Check your connection.",
};

function friendly(message: string, code: string): string {
  if (message && message !== "Internal Server Error") return message;
  return CODE_HINTS[code] ?? "Something went wrong. Please try again.";
}

/* --------------------------- Auth plumbing hooks ---------------------------- */

type TokenGetter = () => string | null;
type UnauthorizedHandler = () => void;

let tokenGetter: TokenGetter = () => null;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Called by the auth store to wire the token source and 401 behavior. */
export function configureApiClient(config: {
  getToken: TokenGetter;
  onUnauthorized: UnauthorizedHandler;
}): void {
  tokenGetter = config.getToken;
  onUnauthorized = config.onUnauthorized;
}

/* ------------------------------- URL building ------------------------------- */

export type QueryValue = string | number | boolean | null | undefined;

function appendQuery(url: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}${url.includes("?") ? "&" : "?"}${qs}` : url;
}

/** Build a URL for an /api/v1/... path, honoring direct vs gateway mode. */
export function buildApiUrl(
  path: string,
  query?: Record<string, QueryValue>
): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  if (DIRECT_MODE) {
    return appendQuery(`${RAW_BASE}${clean}`, query);
  }
  // Gateway mode: relative URL; the port selector rides in the query string.
  const withPort = appendQuery(clean, { XTransformPort: MOCK_API_PORT });
  return appendQuery(withPort, query);
}

/** Build the WebSocket URL for /api/v1/ws. */
export function buildWsUrl(): string {
  if (DIRECT_MODE) {
    const wsBase = RAW_BASE
      .replace(/^http:\/\//, "ws://")
      .replace(/^https:\/\//, "wss://");
    return appendQuery(`${wsBase}/api/v1/ws`);
  }
  if (typeof window === "undefined") return "/api/v1/ws";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return appendQuery(`${proto}//${window.location.host}/api/v1/ws`, {
    XTransformPort: MOCK_API_PORT,
  });
}

/* --------------------------------- Requests --------------------------------- */

type RawResponse = {
  status: number;
  ok: boolean;
  headers: Headers;
  data: unknown;
};

async function readResponse(response: Response): Promise<RawResponse> {
  let data: unknown = undefined;
  const contentType = response.headers.get("content-type") ?? "";
  // Standard JSON responses, plus a tolerant path for intermediaries/test
  // stubs that omit Content-Type entirely (JSON.parse failure keeps `data`
  // undefined and the error path handles it).
  const mayBeJson = contentType.includes("json") || contentType === "";
  if (mayBeJson) {
    try {
      data = await response.json();
    } catch {
      data = undefined;
    }
  }
  return {
    status: response.status,
    ok: response.ok,
    headers: response.headers,
    data,
  };
}

function parseErrorPayload(raw: RawResponse): ApiError {
  const retryAfterHeader = raw.headers.get("retry-after");
  const retryAfterSeconds =
    retryAfterHeader !== null && Number.isFinite(Number(retryAfterHeader))
      ? Number(retryAfterHeader)
      : null;

  const payload = raw.data as ApiErrorEnvelope | { detail?: unknown } | undefined;

  // Backend envelope: { error: { code, message, details? } }
  if (payload && typeof payload === "object" && "error" in payload) {
    const err = (payload as ApiErrorEnvelope).error;
    return new ApiError(raw.status, err.code ?? "error", friendly(err.message ?? "", err.code ?? ""), {
      details: err.details,
      retryAfterSeconds,
    });
  }

  // FastAPI validation shape: { detail: [{ loc, msg, type }] }
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (Array.isArray(detail)) {
      const details = detail as ApiErrorDetail[];
      const first = details[0];
      const msg = first ? `${first.msg}` : "Request validation failed.";
      return new ApiError(raw.status, "validation_error", msg, {
        details,
        retryAfterSeconds,
      });
    }
    if (typeof detail === "string") {
      return new ApiError(raw.status, "error", detail, { retryAfterSeconds });
    }
  }

  const fallbackCode =
    raw.status === 401
      ? "unauthorized"
      : raw.status === 403
        ? "forbidden"
        : raw.status === 404
          ? "not_found"
          : raw.status === 429
            ? "rate_limited"
            : "error";

  return new ApiError(raw.status, fallbackCode, CODE_HINTS[fallbackCode] ?? "Request failed.", {
    retryAfterSeconds,
  });
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Skip attaching the Authorization header (login/register). */
  anonymous?: boolean;
  timeoutMs?: number;
  /** Raw body passthrough (FormData / Blob / string). */
  rawBody?: BodyInit;
}

async function execute(path: string, options: RequestOptions): Promise<RawResponse> {
  const {
    query,
    body,
    anonymous,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    rawBody,
    headers,
    ...rest
  } = options;

  const finalHeaders = new Headers(headers);
  const token = anonymous ? null : tokenGetter();
  if (token) finalHeaders.set("Authorization", `Bearer ${token}`);

  let requestBody: BodyInit | undefined;
  if (rawBody !== undefined) {
    requestBody = rawBody;
    // Let the browser set the multipart boundary itself.
  } else if (body !== undefined) {
    requestBody = JSON.stringify(body);
    if (!finalHeaders.has("Content-Type")) {
      finalHeaders.set("Content-Type", "application/json");
    }
  }

  const url = buildApiUrl(path, query);
  let response: Response;
  try {
    response = await fetch(url, {
      ...rest,
      headers: finalHeaders,
      body: requestBody,
      credentials: "omit",
      signal: timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(0, "timeout", "The request timed out. Please try again.");
    }
    throw new ApiError(0, "network_error", friendly("", "network_error"));
  }

  const raw = await readResponse(response);

  if (!raw.ok) {
    const apiError = parseErrorPayload(raw);
    if (raw.status === 401 && onUnauthorized) {
      onUnauthorized();
    }
    throw apiError;
  }
  return raw;
}

/** Perform a request and return the parsed JSON payload (undefined for 204). */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const raw = await execute(path, options);
  return raw.data as T;
}

/** Same as apiRequest but returns the Response headers too (e.g. pagination). */
export async function apiRequestRaw(
  path: string,
  options: RequestOptions = {}
): Promise<RawResponse> {
  return execute(path, options);
}

/** Multipart upload helper (field name: `files`, per the backend contract). */
export async function apiUpload<T>(
  path: string,
  files: File[],
  options: RequestOptions = {}
): Promise<T> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiRequest<T>(path, {
    ...options,
    method: "POST",
    rawBody: form,
  });
}

/**
 * Multipart upload with real progress reporting.
 *
 * `fetch()` cannot observe upload progress, so this helper uses XHR while
 * keeping the exact same contract as `apiUpload`: repeated `files` field,
 * Bearer auth, uniform error envelope parsing (413/415/503/...), 401
 * logout handling and a timeout. `onProgress` reports 0..100 (100 fires
 * once the request body has been fully handed to the network, before the
 * response arrives).
 */
export function uploadFilesWithProgress<T>(
  path: string,
  files: File[],
  options: {
    onProgress?: (percent: number) => void;
    timeoutMs?: number;
    signal?: AbortSignal;
  } = {}
): Promise<T> {
  return new Promise<T>((resolvePromise, rejectPromise) => {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildApiUrl(path));
    xhr.responseType = "text";
    const token = tokenGetter();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.timeout = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    xhr.upload.onprogress = (event) => {
      if (!options.onProgress || !event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      options.onProgress(Math.min(99, percent));
    };

    const settleError = (status: number) => {
      let data: unknown = undefined;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
      } catch {
        data = undefined;
      }
      // Hand Retry-After (429) to the shared parser through a Headers object.
      const headers = new Headers();
      const retryAfterHeader = xhr.getResponseHeader("retry-after");
      if (retryAfterHeader !== null) headers.set("retry-after", retryAfterHeader);
      const error = parseErrorPayload({ status, ok: false, headers, data });
      if (status === 401 && onUnauthorized) onUnauthorized();
      return error;
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(100);
        let data: unknown = undefined;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
        } catch {
          data = undefined;
        }
        resolvePromise(data as T);
        return;
      }
      rejectPromise(settleError(xhr.status));
    };
    xhr.onerror = () =>
      rejectPromise(new ApiError(0, "network_error", friendly("", "network_error")));
    xhr.ontimeout = () =>
      rejectPromise(
        new ApiError(0, "timeout", "The upload timed out. Please try again.")
      );
    xhr.onabort = () =>
      rejectPromise(new ApiError(0, "network_error", "Upload canceled."));

    if (options.signal) {
      options.signal.addEventListener(
        "abort",
        () => xhr.abort(),
        { once: true }
      );
    }

    xhr.send(form);
  });
}

/**
 * Download bytes with Bearer auth and return an object URL (used as the
 * fallback when the local storage backend cannot issue presigned URLs).
 */
export async function fetchMediaObjectUrl(mediaPath: string): Promise<string> {
  const response = await fetch(buildApiUrl(mediaPath), {
    headers: { Authorization: `Bearer ${tokenGetter() ?? ""}` },
    signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new ApiError(response.status, "not_found", "This file is no longer available.");
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export const apiClient = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: RequestOptions) => apiRequest<T>(path, { ...options, method: "DELETE" }),
  upload: <T>(path: string, files: File[], options?: RequestOptions) =>
    apiUpload<T>(path, files, options),
  buildUrl: buildApiUrl,
};

export type { PageQuery };
