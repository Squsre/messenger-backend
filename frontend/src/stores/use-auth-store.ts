"use client";

/**
 * Auth state: token, profile, and the actions that mutate them.
 *
 * The token is mirrored into a client cookie (`auth_token`) so Next.js
 * Middleware can protect routes. Every other store is reset on logout.
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
  ACCESS_TOKEN_TTL_S,
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
      partialize: (state) => ({
        token: state.token,
        tokenExpiresAt: state.tokenExpiresAt,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
      onRehydrateStorage: () => (state) => {
        // Re-sync the middleware cookie after a reload.
        if (state?.token) {
          const remaining = state.tokenExpiresAt
            ? Math.floor((state.tokenExpiresAt - Date.now()) / 1000)
            : ACCESS_TOKEN_TTL_S;
          if (remaining > 0) {
            setAuthCookie(state.token, remaining);
          } else {
            state.clearAuthState();
          }
        }
        state?.setHydrated();
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
