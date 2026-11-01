"use client";

/**
 * Auth state: token, profile, and the actions that mutate them.
 *
 * Token storage policy (hardened):
 * - The access token NEVER touches localStorage. A persisted, XSS-readable
 *   JWT copy was the biggest theft surface; the persisted store now keeps
 *   only the non-sensitive profile and expiry metadata.
 * - The token lives in ONE place per tab (memory) plus the `auth_token`
 *   cookie that the Next.js middleware needs for route guarding. The cookie
 *   is `SameSite=Lax` (top-level navigations — e.g. opening a verify-email
 *   link from a mail client — still carry it) and `Secure` under https.
 * - On boot the token is rehydrated FROM THE COOKIE, so a refresh restores
 *   the session without any localStorage copy of the JWT.
 * - A truly httpOnly cookie requires the backend to set it (Set-Cookie on
 *   /auth/login); the frontend cannot create one. This is documented for
 *   the backend team — until then this is the strongest client-only
 *   posture: no persistent JS-readable token store besides the cookie.
 *
 * Every other store is reset on logout.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  LoginRequest,
  MessageResponse,
  TokenResponse,
  UserCreate,
  UserPrivate,
  UserRead,
} from "@/types/api";
import { apiClient, configureApiClient, ApiError } from "@/lib/api-client";
import {
  AUTH_COOKIE_NAME,
  AUTH_STORAGE_KEY,
} from "@/lib/constants";
import { resetApplicationStores } from "@/stores/registry";
import { clearAllDrafts } from "@/lib/draft-store";

interface AuthState {
  token: string | null;
  /** Epoch ms when the access token expires (login time + expires_in). */
  tokenExpiresAt: number | null;
  user: UserPrivate | null;
  isAuthenticated: boolean;
  /** True once the persisted store has been rehydrated on the client. */
  hydrated: boolean;

  login: (input: LoginRequest) => Promise<UserPrivate>;
  register: (input: UserCreate) => Promise<UserRead>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  /**
   * Cheap token-liveness probe against GET /auth/me, run once on boot.
   * Detects sessions revoked elsewhere (logout-all, another tab) and
   * multi-tab account drift; 401 flows through the API client's global
   * handler, network errors are tolerated (offline start).
   */
  probeSession: () => Promise<void>;
  /** Used by the API client on 401 and by the socket on close 4401. */
  forceLogout: (reason?: "expired" | "revoked") => void;
  /** Adopt a fresh token (e.g. after a password change returns a new one). */
  adoptToken: (tokenResponse: TokenResponse) => void;
  setHydrated: () => void;
  clearAuthState: () => void;
  /**
   * Boot-only: restore the in-memory token from the auth cookie. Called
   * from the persist rehydration callback — which runs while the store
   * singleton is still initializing (TDZ), so it must be invoked through
   * the rehydrated state's bound actions, never via `useAuthStore`.
   */
  rehydrateSession: () => void;
}

/* ------------------------------ Cookie plumbing ----------------------------- */

function setAuthCookie(token: string, maxAgeSeconds: number): void {
  if (typeof document === "undefined") return;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${AUTH_COOKIE_NAME}=${encodeURIComponent(token)}` +
    `; Path=/; Max-Age=${Math.max(60, Math.floor(maxAgeSeconds))}; SameSite=Lax${secure}`;
}

function clearAuthCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/** Read the session token back out of the auth cookie (boot rehydration). */
function readAuthCookie(): string | null {
  if (typeof document === "undefined") return null;
  const entry = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${AUTH_COOKIE_NAME}=`));
  return entry ? decodeURIComponent(entry.slice(AUTH_COOKIE_NAME.length + 1)) : null;
}

/* --------------------------------- The store -------------------------------- */

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      tokenExpiresAt: null,
      user: null,
      isAuthenticated: false,
      hydrated: false,

      setHydrated: () => set({ hydrated: true }),

      login: async (input) => {
        const tokenResponse = await apiClient.post<TokenResponse>(
          "/api/v1/auth/login",
          input,
          { anonymous: true }
        );
        // The login response carries no profile; fetch it right away.
        configureApiClient({
          getToken: () => useAuthStore.getState().token,
          onUnauthorized: () => useAuthStore.getState().forceLogout("revoked"),
        });
        useAuthStore.setState({
          token: tokenResponse.access_token,
          tokenExpiresAt: Date.now() + tokenResponse.expires_in * 1000,
          isAuthenticated: true,
        });
        setAuthCookie(tokenResponse.access_token, tokenResponse.expires_in);
        try {
          const user = await apiClient.get<UserPrivate>("/api/v1/users/me");
          set({ user });
          return user;
        } catch (error) {
          // Roll back the session if the profile cannot be loaded.
          get().clearAuthState();
          throw error;
        }
      },

      register: async (input) => {
        const user = await apiClient.post<UserRead>(
          "/api/v1/auth/register",
          input,
          { anonymous: true }
        );
        return user;
      },

      logout: async () => {
        try {
          await apiClient.post<MessageResponse>("/api/v1/auth/logout");
        } catch {
          // The token may already be revoked; local purge happens regardless.
        }
        get().clearAuthState();
        resetApplicationStores();
        clearAllDrafts();
        if (typeof window !== "undefined") {
          window.location.assign("/login");
        }
      },

      forceLogout: (_reason) => {
        get().clearAuthState();
        resetApplicationStores();
        clearAllDrafts();
        if (typeof window !== "undefined") {
          window.location.assign("/login?expired=1");
        }
      },

      adoptToken: (tokenResponse) => {
        set({
          token: tokenResponse.access_token,
          tokenExpiresAt: Date.now() + tokenResponse.expires_in * 1000,
          isAuthenticated: true,
        });
        setAuthCookie(tokenResponse.access_token, tokenResponse.expires_in);
      },

      clearAuthState: () => {
        set({
          token: null,
          tokenExpiresAt: null,
          user: null,
          isAuthenticated: false,
        });
        clearAuthCookie();
      },

      rehydrateSession: () => {
        const cookieToken = readAuthCookie();
        const expiresAt = get().tokenExpiresAt;
        const remainingS = expiresAt
          ? Math.floor((expiresAt - Date.now()) / 1000)
          : 0;
        if (cookieToken && remainingS > 0) {
          // Cookie token still within its lifetime — restore the session.
          set({ token: cookieToken, isAuthenticated: true });
        } else {
          // No cookie / expired token: drop any stale persisted profile.
          get().clearAuthState();
        }
        set({ hydrated: true });
      },

      refreshProfile: async () => {
        const user = await apiClient.get<UserPrivate>("/api/v1/users/me");
        set({ user });
      },

      probeSession: async () => {
        try {
          // GET /auth/me — the lightweight identity read (id/email/username/
          // is_active). A 401 never reaches this line: the API client's global
          // handler purges the stale session and redirects to /login.
          const me = await apiClient.get<UserRead>("/api/v1/auth/me");
          if (!me.is_active) {
            get().forceLogout("revoked");
            return;
          }
          const local = get().user;
          if (!local || local.username !== me.username || local.email !== me.email) {
            // Persisted profile is missing or another account signed in from a
            // second tab — refetch the authoritative private profile.
            await get().refreshProfile();
          }
        } catch (error) {
          // Offline start: keep the cached session; the socket handshake will
          // also surface dead tokens via close 4401.
          if (error instanceof ApiError && error.status === 401) {
            get().forceLogout("revoked");
          }
        }
      },
    }),
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      // SECURITY: only non-sensitive metadata is persisted. The JWT itself
      // is NOT written to localStorage — it lives in memory + the cookie.
      partialize: (state) => ({
        user: state.user,
        tokenExpiresAt: state.tokenExpiresAt,
      }),
      onRehydrateStorage: () => (state) => {
        // Restore the session from the cookie (the only token store that
        // survives a reload). IMPORTANT: this callback fires while the
        // store singleton is still being created — `useAuthStore` is in
        // its temporal dead zone here, so the action MUST be invoked on
        // the rehydrated state object (its closures are already bound).
        state?.rehydrateSession();
      },
    }
  )
);

/** Selector: is the persisted session still valid enough to try? */
export function selectHasSession(state: AuthState): boolean {
  return Boolean(state.token);
}

// Wire the HTTP client to this store as soon as the module loads on the client.
if (typeof window !== "undefined") {
  configureApiClient({
    getToken: () => useAuthStore.getState().token,
    onUnauthorized: () => useAuthStore.getState().forceLogout("revoked"),
  });
}
