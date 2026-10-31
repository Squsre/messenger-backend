"use client";

/**
 * File-info dialog backed by GET /media/{media_id}: standalone metadata for
 * one stored file (dimensions, size, uploader, processing status, attached
 * message, checksum) fetched fresh from the API instead of the message
 * payload, so it also works for media seen only as an id.
 */
import { useEffect, useState } from "react";
import { CalendarDays, Fingerprint, Hash, Image as ImageIcon, UserRound } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Loader2 } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import { formatBytes } from "@/lib/utils";
import type { MediaRead } from "@/types/api";

function Row({ label, value }: { label: React.ReactNode; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all text-end font-medium">{value}</span>
    </div>
  );
}

export function MediaInfoDialog({
  mediaId,
  open,
  onOpenChange,
}: {
  mediaId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [meta, setMeta] = useState<MediaRead | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  // Reset synchronously while rendering when the target changes (the
  // React-endorsed "adjust state when a prop changes" pattern) so the
  // effect below never sets state synchronously.
  const [lastKey, setLastKey] = useState<string | null>(null);
  const key = open ? mediaId : null;
  if (key !== lastKey) {
    setLastKey(key);
    setMeta(null);
    setState("loading");
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    apiClient
      .get<MediaRead>(`/api/v1/media/${encodeURIComponent(mediaId)}`)
      .then((data) => {
        if (cancelled) return;
        setMeta(data);
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [open, mediaId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ImageIcon className="size-4" /> File info
          </DialogTitle>
          <DialogDescription className="truncate">
            {meta?.filename ?? mediaId}
          </DialogDescription>
        </DialogHeader>

        {state === "loading" && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {state === "error" && (
          <p className="py-6 text-center text-sm text-destructive">
            This file is no longer available.
          </p>
        )}

        {state === "ready" && meta && (
          <div className="space-y-2.5">
            <Row label="Name" value={meta.filename} />
            <Row label="Type" value={meta.content_type} />
            <Row label="Size" value={formatBytes(meta.size_bytes)} />
            {(meta.width !== null || meta.height !== null) && (
              <Row
                label="Dimensions"
                value={
                  meta.width && meta.height
                    ? `${meta.width} × ${meta.height} px`
                    : "—"
                }
              />
            )}
            <Row
              label={
                <span className="inline-flex items-center gap-1">
                  <UserRound className="size-3" /> Uploaded by
                </span>
              }
              value={`@${meta.uploader_username}`}
            />
            <Row
              label={
                <span className="inline-flex items-center gap-1">
                  <CalendarDays className="size-3" /> Uploaded
                </span>
              }
              value={new Date(meta.created_at).toLocaleString()}
            />
            <Row
              label={
                <span className="inline-flex items-center gap-1">
                  <Hash className="size-3" /> Message
                </span>
              }
              value={meta.message_id !== null ? `#${meta.message_id}` : "unattached"}
            />
            {meta.checksum_sha256 && (
              <Row
                label={
                  <span className="inline-flex items-center gap-1">
                    <Fingerprint className="size-3" /> SHA-256
                  </span>
                }
                value={`${meta.checksum_sha256.slice(0, 12)}…`}
              />
            )}
            <Separator />
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
                Status
              </span>
              <Badge
                variant={
                  meta.status === "ready"
                    ? "secondary"
                    : meta.status === "failed"
                      ? "destructive"
                      : "outline"
                }
              >
                {meta.status}
              </Badge>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
