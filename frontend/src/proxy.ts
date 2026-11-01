import { NextResponse, type NextRequest } from "next/server";
import { AUTH_COOKIE_NAME } from "@/lib/constants";
import { isJwtExpired } from "@/lib/jwt";

/**
 * Cookie and JWT route guard (Next.js 16 "proxy" convention, formerly "middleware").
 * Validates token presence, structural integrity, and expiration before permitting
 * access to protected routes.
 */
export default function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const cookie = request.cookies.get(AUTH_COOKIE_NAME);
  const token = cookie?.value ? decodeURIComponent(cookie.value) : null;
  const hasValidToken = Boolean(token && !isJwtExpired(token));

  const isAuthRoute =
    pathname === "/login" ||
    pathname === "/register" ||
    pathname === "/verify-email" ||
    pathname === "/reset-password";

  if (!hasValidToken && !isAuthRoute) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = token ? "?expired=1" : "";
    const response = NextResponse.redirect(url);
    if (token) {
      response.cookies.delete(AUTH_COOKIE_NAME);
    }
    return response;
  }

  if (hasValidToken && isAuthRoute) {
    // Token-consuming deep links (?token=...) must render even when signed
    // in: email-verification and password-reset links arrive by email while
    // the user is typically still authenticated. Anything else bounces to
    // the app shell.
    const hasToken = request.nextUrl.searchParams.has("token");
    if (!hasToken) {
      const url = request.nextUrl.clone();
      url.pathname = "/";
      url.search = "";
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/c/:path*",
    "/g/:path*",
    "/contacts",
    "/contacts/:path*",
    "/settings",
    "/settings/:path*",
    "/login",
    "/register",
    "/verify-email",
    "/reset-password",
  ],
};
