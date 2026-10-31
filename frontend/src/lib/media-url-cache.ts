/**
 * In-memory cache for short-lived presigned media URLs.
 *
 * The backend (MinIO mode) issues URLs valid for `expires_in_seconds`; we
 * refresh them PRESIGNED_URL_EARLY_EXPIRY_S before expiry so <img> swaps are
 * invisible. The local-disk backend answers 409 for /url — the hook falls
 * back to an authenticated blob fetch in that case.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import { apiClient, ApiError, fetchMediaObjectUrl } from "@/lib/api-client";
import { PRESIGNED_URL_EARLY_EXPIRY_S } from "@/lib/constants";
import type { MediaAccessUrlRead } from "@/types/api";

interface CacheEntry {
  url: string;
  /** Epoch ms after which the URL must be refreshed. */
  refreshAt: number;
  /** Blob object URL fallback (local storage backend). */
  blob?: boolean;
}

interface MediaUrlCacheState {
  entries: Record<string, CacheEntry>;
  /** Keyed by `presigned:<id>` / `blob:<id>` so the two resolution modes
   * never hand each other's promise across (a forceBlob caller must not
   * receive a presigned URL that may be unreachable from this browser). */
  inflight: Record<string, Promise<string>>;
  /**
   * Resolve a display URL. `forceBlob` skips the presigned endpoint and
   * downloads through the authenticated `/content` route — used when a
   * presigned URL turned out to be unreachable from the browser.
   */
  resolve: (mediaId: string, options?: { forceBlob?: boolean }) => Promise<string>;
  drop: (mediaId: string) => void;
}

export const useMediaUrlCache = create<MediaUrlCacheState>()(
  immer((set, get) => ({
  entries: {},
  inflight: {},

  resolve: async (mediaId, options) => {
    const forceBlob = options?.forceBlob ?? false;
    const cached = get().entries[mediaId];
    if (!forceBlob && cached && cached.refreshAt > Date.now()) {
      return cached.url;
    }
    if (forceBlob && cached?.blob && cached.refreshAt > Date.now()) {
      return cached.url;
    }

    // Deduplicate per resolution mode: presigned and blob fetches are
    // different network calls and must not share an inflight promise.
    const inflightKey = forceBlob ? `blob:${mediaId}` : `presigned:${mediaId}`;
    const existing = get().inflight[inflightKey];
    if (existing) return existing;

    const promise = (async () => {
      try {
        if (forceBlob) throw new ApiError(409, "conflict", "Blob requested");
        const result = await apiClient.get<MediaAccessUrlRead>(
          `/api/v1/media/${mediaId}/url`
        );
        const refreshAt =
          Date.now() +
          Math.max(10, result.expires_in_seconds - PRESIGNED_URL_EARLY_EXPIRY_S) *
            1000;
        set((state) => {
          state.entries[mediaId] = { url: result.url, refreshAt };
          delete state.inflight[inflightKey];
        });
        return result.url;
      } catch (error) {
        set((state) => {
          delete state.inflight[inflightKey];
        });
        // Either the caller forced the authenticated path, or the backend
        // cannot issue presigned URLs at all (local storage backend answers
        // 409). Download the bytes with Bearer auth instead.
        const useBlob =
          forceBlob ||
          (error instanceof ApiError && (error.status === 409 || error.status === 501));
        if (useBlob) {
          const blobUrl = await fetchMediaObjectUrl(`/api/v1/media/${mediaId}/content`);
          set((state) => {
            // Blob URLs live for the page session; refresh after 10 minutes.
            state.entries[mediaId] = {
              url: blobUrl,
              refreshAt: Date.now() + 10 * 60 * 1000,
              blob: true,
            };
          });
          return blobUrl;
        }
        throw error;
      }
    })();

    set((state) => {
      state.inflight[inflightKey] = promise;
    });
    return promise;
  },

  drop: (mediaId) =>
    set((state) => {
      const entry = state.entries[mediaId];
      if (entry?.blob) URL.revokeObjectURL(entry.url);
      delete state.entries[mediaId];
    }),
  }))
);
