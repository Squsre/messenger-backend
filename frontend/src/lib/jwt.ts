/**
 * Client-safe JWT utilities: decoding, validation, and expiry calculation.
 * Does not require external crypto libraries; operates strictly on the
 * standard base64url encoded payload.
 */

export interface JwtPayload {
  sub?: string;
  jti?: string;
  iat?: number;
  nbf?: number;
  exp?: number;
  iss?: string;
  aud?: string;
  type?: string;
  ver?: number;
  [key: string]: unknown;
}

/**
 * Decode a base64url-encoded string safely in modern browsers and Edge runtime.
 */
function decodeBase64Url(str: string): string | null {
  try {
    let base64 = str.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4 !== 0) {
      base64 += "=";
    }
    if (typeof atob === "function") {
      const binary = atob(base64);
      const bytes = Uint8Array.from(binary, (m) => m.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    }
    if (typeof Buffer !== "undefined") {
      return Buffer.from(base64, "base64").toString("utf-8");
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Safely parse JWT claims from a token string without verifying the cryptographic signature
 * (signature verification is performed server-side by FastAPI).
 */
export function parseJwt(token: string | null | undefined): JwtPayload | null {
  if (!token || typeof token !== "string") return null;
  const parts = token.trim().split(".");
  if (parts.length !== 3) return null;

  const decoded = decodeBase64Url(parts[1]);
  if (!decoded) return null;

  try {
    const payload = JSON.parse(decoded);
    if (typeof payload === "object" && payload !== null) {
      return payload as JwtPayload;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Check whether a JWT is expired.
 * @param leewaySeconds Optional safety margin in seconds (default 5s) to account for clock skew.
 */
export function isJwtExpired(token: string | null | undefined, leewaySeconds = 5): boolean {
  const payload = parseJwt(token);
  if (!payload || typeof payload.exp !== "number") return true;
  const nowSeconds = Math.floor(Date.now() / 1000);
  return nowSeconds >= payload.exp - leewaySeconds;
}

/**
 * Get remaining lifetime of the token in milliseconds.
 * Returns 0 if expired, invalid, or null.
 */
export function getJwtRemainingMs(token: string | null | undefined): number {
  const payload = parseJwt(token);
  if (!payload || typeof payload.exp !== "number") return 0;
  const remaining = payload.exp * 1000 - Date.now();
  return remaining > 0 ? remaining : 0;
}
