import { NextResponse, type NextRequest } from "next/server";
import { AUTH_COOKIE_NAME } from "@/lib/constants";

/**
 * Cookie-presence route guard (Next.js 16 "proxy" convention, formerly "middleware"). The JWT itself is validated by the backend on
 * every request; this middleware only keeps unauthenticated visitors out of
 * the app shell and bounces signed-in users away from the auth screens.
 */
export default function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasCookie = request.cookies.has(AUTH_COOKIE_NAME);

  const isAuthRoute =
    pathname === "/login" ||
    pathname === "/register" ||
    pathname === "/verify-email" ||
    pathname === "/reset-password";

  if (!hasCookie && !isAuthRoute) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (hasCookie && isAuthRoute) {
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
    "/settings/:path*",
    "/login",
    "/register",
    "/verify-email",
    "/reset-password",
  ],
};
