"use client";

import { memo, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2, Paperclip } from "lucide-react";
import { cn, formatClock } from "@/lib/utils";
import { StatusTick } from "@/components/common/status-tick";
import { MediaImage, MediaFileCard } from "@/components/chat/message-media";
import { useMessageStore } from "@/stores/use-message-store";
import type { ChatMessage } from "@/stores/use-message-store";

interface MessageItemProps {
  message: ChatMessage;
  /** Is this my own message? */
  mine: boolean;
  /** Show the sender name above the bubble (group chats). */
  showSender: boolean;
  variant: "direct" | "group";
  /**
   * Bubble grouping with the neighbouring messages from the same sender:
   * the first bubble of a run gets a tight top seam, the last one keeps
   * the small tail corner, and stacked runs use a reduced vertical gap
   * (handled by the feed).
   */
  groupedWithPrev?: boolean;
  groupedWithNext?: boolean;
}

function MessageItemImpl({
  message,
  mine,
  showSender,
  variant,
  groupedWithPrev = false,
  groupedWithNext = false,
}: MessageItemProps) {
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [attaching, setAttaching] = useState(false);
  const attachInputRef = useRef<HTMLInputElement>(null);
  const hasMedia = Boolean(message.media);
  const isImage = message.media?.content_type.startsWith("image/") ?? false;
  const textOnly = !hasMedia && Boolean(message.body);

  // POST /media/{id}/attach works on MY text-only messages that already
  // have a server id (optimistic pendings are not attachable yet).
  const canAttach = mine && !hasMedia && message.id !== null;

  async function handleAttachFile(file: File) {
    if (!message.id) return;
    setAttaching(true);
    try {
      await useMessageStore.getState().attachMediaToMessage({
        chatType: message.chatType,
        conversationId: message.conversationId,
        messageId: message.id,
        file,
      });
      toast.success("File attached to the message.");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not attach the file."
      );
    } finally {
      setAttaching(false);
    }
  }

  return (
    <div
      className={cn("message-in group flex w-full gap-2 px-4", mine ? "justify-end" : "justify-start")}
      data-message-id={message.id ?? undefined}
    >
      {canAttach && (
        <>
          <input
            ref={attachInputRef}
            type="file"
            accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleAttachFile(file);
              e.target.value = "";
            }}
            aria-label="Attach a file to this message"
          />
          <button
            type="button"
            aria-label="Attach a file to this message"
            title="Attach a file"
            disabled={attaching}
            onClick={() => attachInputRef.current?.click()}
            className="mt-1.5 size-7 shrink-0 self-start rounded-full text-muted-foreground opacity-0 transition-opacity duration-150 hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-40"
          >
            {attaching ? (
              <Loader2 className="mx-auto size-3.5 animate-spin" />
            ) : (
              <Paperclip className="mx-auto size-3.5" />
            )}
          </button>
        </>
      )}
      {/*
        * Bubble column: `w-fit` shrink-wraps the bubble around its content
        * (image + caption) instead of stretching, and the max-width caps
        * keep even huge images from pushing the bubble across the screen.
        */}
      <div
        className={cn(
          "flex w-fit min-w-0 max-w-[78%] flex-col gap-1 sm:max-w-[65%]",
          mine ? "items-end" : "items-start"
        )}
      >
        {showSender && !mine && (
          <span
            dir="auto"
            className="px-1 text-start text-xs font-medium text-muted-foreground"
          >
            {message.senderUsername}
          </span>
        )}

        <div
          className={cn(
            "relative overflow-hidden rounded-2xl px-3.5 py-2 text-sm shadow-sm",
            mine
              ? "bg-primary text-primary-foreground"
              : "border bg-background",
            textOnly || (hasMedia && message.body) ? "" : "p-2",
            groupedWithPrev && "bubble-seam-top",
            groupedWithNext && "bubble-seam-bottom",
            !groupedWithNext && (mine ? "bubble-tail-out" : "bubble-tail-in")
          )}
        >
          {message.media && (
            <div className={cn(message.body && "mb-1.5")}>
              {isImage ? (
                <MediaImage
                  media={message.media}
                  className={mine ? "border-primary-foreground/20" : ""}
                  onOpen={setLightbox}
                />
              ) : (
                <MediaFileCard media={message.media} />
              )}
            </div>
          )}
          {message.body && (
            <p
              dir="auto"
              className="bidi-plaintext whitespace-pre-wrap break-words text-start"
            >
              {message.body}
            </p>
          )}

          <div
            className={cn(
              "mt-0.5 flex items-center justify-end gap-1 text-[10px]",
              mine ? "text-primary-foreground/70" : "text-muted-foreground"
            )}
          >
            <time dateTime={message.createdAt}>{formatClock(message.createdAt)}</time>
            {mine && (
              <StatusTick
                status={message.status}
                variant={variant}
                readCount={message.readCount}
                otherMembers={Math.max(0, message.memberCount - 1)}
              />
            )}
          </div>
        </div>
      </div>

      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}

/**
 * Full-screen image preview: click anywhere, the Close button, or Escape
 * dismisses it (keyboard users are not trapped in the overlay).
 */
function ImageLightbox({ url, onClose }: { url: string; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-8"
      onClick={onClose}
    >
      <img
        src={url}
        alt="Attachment"
        className="max-h-full max-w-full rounded-lg object-contain"
      />
      <button
        type="button"
        autoFocus
        className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white"
        onClick={onClose}
      >
        Close
      </button>
    </div>
  );
}

export const MessageItem = memo(MessageItemImpl);
