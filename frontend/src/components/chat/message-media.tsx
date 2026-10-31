"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Download, FileText, ImageIcon, Info, Loader2 } from "lucide-react";
import { cn, formatBytes } from "@/lib/utils";
import { useMediaUrl } from "@/hooks/use-media-url";
import { useMediaUrlCache } from "@/lib/media-url-cache";
import { Skeleton } from "@/components/ui/skeleton";
import { MediaInfoDialog } from "@/components/chat/media-info-dialog";
import type { MediaRead } from "@/types/api";

/**
 * Image attachment with a reserved box (prevents layout shift).
 *
 * The frame uses a DEFINITE `w-[280px]` (+ `max-w-full`) instead of the
 * old `min(280px, 100%)`: percentage widths are treated as `auto` during
 * intrinsic sizing, so the <img> width/height attributes (e.g. a 4032px
 * phone photo) used to inflate the shrink-to-fit bubble far beyond the
 * rendered frame — the "huge empty bubble" bug. An absolute width caps
 * the intrinsic contribution at 280px, and `max-w-full` still clamps it
 * on narrow screens.
 */
export function MediaImage({
  media,
  className,
  onOpen,
}: {
  media: MediaRead;
  className?: string;
  onOpen?: (url: string) => void;
}) {
  const { url, loading, error, reportError } = useMediaUrl(media.id);
  const isImage = media.content_type.startsWith("image/");
  const [loaded, setLoaded] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);

  if (!isImage) return null;

  const width = media.width ?? 480;
  const height = media.height ?? 320;
  const aspect = Math.min(Math.max(width / height, 0.6), 1.8);

  if (error || (!url && !loading)) {
    return (
      <div className="flex h-28 w-48 items-center justify-center rounded-lg border bg-muted/50 text-xs text-muted-foreground gap-2">
        <ImageIcon className="size-4" /> Unavailable
      </div>
    );
  }

  return (
    <div className="relative inline-block max-w-full">
      <button
        type="button"
        onClick={() => url && onOpen?.(url)}
        className={cn(
          "relative block w-[280px] max-w-full overflow-hidden rounded-lg border",
          className
        )}
        style={{ aspectRatio: `${aspect}` }}
        aria-label={`Open ${media.filename}`}
      >
        {!loaded && <Skeleton className="absolute inset-0" />}
        {url && (
          <img
            src={url}
            alt={media.filename}
            width={width}
            height={height}
            onLoad={() => setLoaded(true)}
            onError={() => {
              // Presigned URL unreachable (internal MinIO host, expired link,
              // CORS) — retry through the authenticated /content endpoint.
              setLoaded(false);
              reportError();
            }}
            className={cn(
              "block h-full w-full object-cover transition-opacity duration-200",
              loaded ? "opacity-100" : "opacity-0"
            )}
            draggable={false}
          />
        )}
      </button>
      <button
        type="button"
        aria-label={`File info for ${media.filename}`}
        title="File info"
        onClick={() => setInfoOpen(true)}
        className="absolute right-1.5 top-1.5 flex size-6 items-center justify-center rounded-full bg-black/45 text-white/90 opacity-60 transition-opacity duration-150 hover:bg-black/65 focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100"
      >
        <Info className="size-3.5" />
      </button>
      <MediaInfoDialog mediaId={media.id} open={infoOpen} onOpenChange={setInfoOpen} />
    </div>
  );
}

/**
 * Save a blob under its real filename via a temporary object URL. Safe to
 * call from an async click handler (anchor downloads are not popups).
 */
function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
}

/**
 * Document attachment card (pdf / txt / csv).
 *
 * The card keeps a native "open in new tab" anchor for the resolved URL,
 * but a dead presigned URL no longer kills it: the explicit download button
 * fetches the bytes (verifying reachability) and — when the presigned URL
 * is unreachable — transparently re-resolves through the authenticated
 * `GET /media/{id}/content` endpoint and retries once before failing.
 */
export function MediaFileCard({ media }: { media: MediaRead }) {
  const { url, error, reportError } = useMediaUrl(media.id);
  const isImage = media.content_type.startsWith("image/");
  const [infoOpen, setInfoOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [failed, setFailed] = useState(false);
  if (isImage) return null;

  async function download() {
    if (downloading) return;
    setDownloading(true);
    setFailed(false);
    try {
      // 1) Try the resolved URL (presigned OR an existing blob fallback).
      if (url) {
        try {
          const response = await fetch(url, {
            signal: AbortSignal.timeout(30_000),
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          saveBlob(await response.blob(), media.filename);
          return;
        } catch {
          // Presigned URL unreachable / expired — fall through to the
          // authenticated path and keep the cache consistent for the <a>.
          reportError();
        }
      }
      // 2) Authenticated fallback: resolve a blob URL through /content
      //    (cached + in-flight deduped by the media URL cache).
      const blobUrl = await useMediaUrlCache
        .getState()
        .resolve(media.id, { forceBlob: true });
      const response = await fetch(blobUrl, {
        signal: AbortSignal.timeout(30_000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      saveBlob(await response.blob(), media.filename);
    } catch {
      setFailed(true);
      toast.error(`Could not download ${media.filename}.`, {
        description: "The file may no longer be available.",
      });
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="w-60">
      <div className="flex items-center gap-2">
        <a
          href={url ?? "#"}
          target="_blank"
          rel="noreferrer noopener"
          aria-disabled={!url}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-3 rounded-lg border bg-background/60 p-3 transition-colors",
            url ? "hover:bg-accent" : "opacity-60 pointer-events-none"
          )}
        >
          <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <FileText className="size-5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{media.filename}</span>
            <span className="block text-xs text-muted-foreground">
              {formatBytes(media.size_bytes)} · {media.content_type}
            </span>
          </span>
        </a>
        <button
          type="button"
          aria-label={`Download ${media.filename}`}
          title="Download"
          disabled={downloading}
          onClick={() => void download()}
          className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          {downloading ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Download className="size-4" />
          )}
        </button>
        <button
          type="button"
          aria-label={`File info for ${media.filename}`}
          title="File info"
          onClick={() => setInfoOpen(true)}
          className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Info className="size-4" />
        </button>
      </div>
      {failed && (
        <p className="mt-1 text-[11px] text-destructive">
          Download failed — the file may be unavailable.
        </p>
      )}
      {error && !failed && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          Direct link unreachable — use Download to fetch it securely.
        </p>
      )}
      <MediaInfoDialog mediaId={media.id} open={infoOpen} onOpenChange={setInfoOpen} />
    </div>
  );
}
