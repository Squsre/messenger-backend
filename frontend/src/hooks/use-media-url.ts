"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useMediaUrlCache } from "@/lib/media-url-cache";

interface MediaUrlState {
  url: string | null;
  loading: boolean;
  error: boolean;
  /**
   * Call when a resolved URL fails to load (e.g. <img> onError). The cache
   * entry is dropped and the next attempt downloads the bytes through the
   * authenticated `/content` endpoint instead of the (possibly unreachable
   * or expired) presigned URL — or re-fetches a fresh blob when the blob
   * itself failed, so a broken image can never stay stuck until a reload.
   */
  reportError: () => void;
  /** Manual retry after the automatic attempts are exhausted. */
  retry: () => void;
}

const IDLE: MediaUrlState = {
  url: null,
  loading: false,
  error: false,
  reportError: () => undefined,
  retry: () => undefined,
};

/** Automatic retry delays (ms) after a failed resolve, then give up. */
const RETRY_DELAYS_MS = [1000, 3000, 8000];

/**
 * Resolve a display URL for a media attachment:
 * presigned URL (MinIO) with in-memory caching, or an authenticated blob
 * fallback when the backend uses local storage — or when the presigned
 * URL proves unreachable from this browser.
 *
 * Failures self-heal: a failed resolve is retried automatically with
 * backoff (the message may have arrived over WebSocket while the media
 * row was still committing, or the network blipped), and a URL that fails
 * to LOAD drops its cache entry so the next attempt resolves a fresh one.
 * Images that previously required a full page refresh recover on their own.
 */
export function useMediaUrl(mediaId: string | null | undefined): MediaUrlState {
  const resolve = useMediaUrlCache((s) => s.resolve);
  const [forceBlob, setForceBlob] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; url: string | null; error: boolean }>({
    key: "",
    url: null,
    error: false,
  });
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!mediaId) return;
    let cancelled = false;
    resolve(mediaId, { forceBlob })
      .then((url) => {
        if (!cancelled) setState({ key: mediaId, url, error: false });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ key: mediaId, url: null, error: true });
        // Automatic backoff retries while the component stays mounted.
        if (attempt < RETRY_DELAYS_MS.length) {
          retryTimer.current = setTimeout(
            () => setAttempt((a) => a + 1),
            RETRY_DELAYS_MS[attempt]
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mediaId, resolve, forceBlob, attempt]);

  // Clear any pending retry timer on unmount / media switch.
  useEffect(() => {
    return () => {
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, []);

  const reportError = useCallback(() => {
    if (!mediaId) return;
    // The cached URL is dead (expired presigned link, unreachable host, or
    // a revoked blob). Drop the entry so the retry resolves a FRESH one —
    // handing out the same cached URL again would loop on the same error.
    useMediaUrlCache.getState().drop(mediaId);
    setState((prev) => ({ ...prev, url: null }));
    if (forceBlob) {
      // Already on the authenticated path: re-fetch a fresh blob.
      setAttempt((a) => a + 1);
    } else {
      setForceBlob(true);
    }
  }, [mediaId, forceBlob]);

  const retry = useCallback(() => {
    if (!mediaId) return;
    useMediaUrlCache.getState().drop(mediaId);
    setAttempt((a) => a + 1);
  }, [mediaId]);

  if (!mediaId) return IDLE;
  const current = state.key === mediaId ? state : null;
  return {
    url: current?.url ?? null,
    loading: !current,
    error: current?.error ?? false,
    reportError,
    retry,
  };
}
