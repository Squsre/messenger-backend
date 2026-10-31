"use client";

import { useCallback, useEffect, useState } from "react";
import { useMediaUrlCache } from "@/lib/media-url-cache";

interface MediaUrlState {
  url: string | null;
  loading: boolean;
  error: boolean;
  /**
   * Call when a resolved URL fails to load (e.g. <img> onError). The next
   * attempt downloads the bytes through the authenticated `/content`
   * endpoint instead of the (possibly unreachable) presigned URL.
   */
  reportError: () => void;
}

const IDLE: MediaUrlState = {
  url: null,
  loading: false,
  error: false,
  reportError: () => undefined,
};

/**
 * Resolve a display URL for a media attachment:
 * presigned URL (MinIO) with in-memory caching, or an authenticated blob
 * fallback when the backend uses local storage — or when the presigned
 * URL proves unreachable from this browser.
 */
export function useMediaUrl(mediaId: string | null | undefined): MediaUrlState {
  const resolve = useMediaUrlCache((s) => s.resolve);
  const [forceBlob, setForceBlob] = useState(false);
  const [state, setState] = useState<{ key: string; url: string | null; error: boolean }>({
    key: "",
    url: null,
    error: false,
  });

  useEffect(() => {
    if (!mediaId) return;
    let cancelled = false;
    resolve(mediaId, { forceBlob })
      .then((url) => {
        if (!cancelled) setState({ key: mediaId, url, error: false });
      })
      .catch(() => {
        if (!cancelled) setState({ key: mediaId, url: null, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [mediaId, resolve, forceBlob]);

  const reportError = useCallback(() => {
    setForceBlob((previous) => {
      if (previous) return previous;
      setState((prev) => ({ ...prev, url: null }));
      return true;
    });
  }, []);

  if (!mediaId) return IDLE;
  const current = state.key === mediaId ? state : null;
  return {
    url: current?.url ?? null,
    loading: !current,
    error: current?.error ?? false,
    reportError,
  };
}
