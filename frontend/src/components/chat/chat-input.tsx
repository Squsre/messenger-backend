"use client";

/**
 * ChatInput: auto-growing composer with
 * - a textarea that grows from 1 row up to 5 rows (Enter sends, Shift+Enter
 *   inserts a newline),
 * - an emoji picker popover beside the attachment clip button,
 * - per-conversation draft persistence (localStorage, restored on switch),
 * - typing frames throttled to one `true` per 1.5 s plus a stop on idle,
 * - media picking, drag-drop over the WHOLE chat area (drop bus) and the
 *   input strip, with client-side validation
 *   (10 MB per file, max 4 files, JPEG/PNG/GIF/WebP/PDF/TXT/CSV),
 * - an XHR-backed upload progress bar (fetch cannot report upload %),
 * - orphan prevention: removing a chip — or switching chats / unmounting —
 *   DELETEs uploaded-but-unattached media from the backend,
 * - optimistic send through the message store (socket first, REST fallback).
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Ban,
  Paperclip,
  SendHorizontal,
  ShieldCheck,
  X,
  Loader2,
  FileUp,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { EmojiPicker } from "@/components/chat/emoji-picker";
import { useMessageStore } from "@/stores/use-message-store";
import { useSocketStore } from "@/stores/use-socket-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useBlocksStore } from "@/stores/use-blocks-store";
import {
  apiClient,
  ApiError,
  uploadFilesWithProgress,
} from "@/lib/api-client";
import { subscribeToDroppedFiles } from "@/lib/media-drop-bus";
import { loadDraft, saveDraft, clearDraft } from "@/lib/draft-store";
import {
  MEDIA_MAX_FILES_PER_REQUEST,
  MEDIA_MAX_UPLOAD_BYTES,
  MEDIA_UPLOAD_TIMEOUT_MS,
  isAllowedUpload,
  TYPING_EXPIRY_MS,
} from "@/lib/constants";
import { cn, formatBytes } from "@/lib/utils";
import type { MediaRead } from "@/types/api";
import type { ChatType } from "@/stores/use-conversation-store";

interface ChatInputProps {
  chatType: ChatType;
  conversationId: number;
}

interface PendingAttachment {
  /** Local object URL for the chip preview. */
  previewUrl: string;
  file: File;
  media: MediaRead | null;
  error?: string;
}

/**
 * Turn upload-failure envelopes (413 too large, 415 wrong type, 503 storage
 * down, 429 rate limited, …) into one clear, actionable toast message.
 */
function mediaUploadErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Upload failed. Please try again.";
  switch (error.status) {
    case 413:
      return "One or more files exceed the 10 MB limit.";
    case 415:
      return "Unsupported file type. Allowed: JPEG, PNG, GIF, WebP, PDF, TXT, CSV.";
    case 503:
      return "The storage backend is temporarily unavailable. Please try again shortly.";
    case 429:
      return error.retryAfterSeconds
        ? `Too many uploads. Retry in ${error.retryAfterSeconds}s.`
        : "Too many uploads. Please slow down.";
    case 0:
      return error.code === "timeout"
        ? "The upload timed out. Please try again."
        : "Cannot reach the server. Check your connection.";
    default:
      return error.message || "Upload failed. Please try again.";
  }
}

/** 5 rows × 20px line-height + vertical padding + border. */
const TEXTAREA_MAX_HEIGHT = 120;

export function ChatInput({ chatType, conversationId }: ChatInputProps) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  /** 0..100 progress of the active upload batch (null = idle). */
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);

  /* ----------------------- Blocked-participant gate ------------------------- */

  // Snapshot-safe conversation lookup (immer keeps object identity stable).
  const conversation = useConversationStore((s) =>
    chatType === "direct"
      ? s.conversations.find((c) => c.id === conversationId)
      : undefined
  );
  const participantUsername =
    chatType === "direct" ? (conversation?.participant.username ?? null) : null;
  const participantBlocked = useBlocksStore((s) =>
    chatType === "direct" && participantUsername
      ? s.blocked.some((u) => u.username === participantUsername)
      : false
  );

  // Read by the stable addFiles/send callbacks without re-creating them.
  const participantBlockedRef = useRef(participantBlocked);
  participantBlockedRef.current = participantBlocked;
  const participantUsernameRef = useRef(participantUsername);
  participantUsernameRef.current = participantUsername;

  /** Unblock straight from the composer notice. */
  const unblockParticipant = async () => {
    const username = participantUsernameRef.current;
    if (!username) return;
    try {
      await useBlocksStore.getState().unblock(username);
      toast.info(`Unblocked @${username}.`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unblock failed.");
    }
  };

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const typingStopTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attachmentsRef = useRef<PendingAttachment[]>([]);
  attachmentsRef.current = attachments;
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;
  /** Abortable handle of the in-flight upload batch (switch/unmount cleanup). */
  const uploadAbortRef = useRef<AbortController | null>(null);
  /** Latest addFiles, read by the stable drop-bus subscription below. */
  const addFilesRef = useRef<(files: FileList | File[]) => void>(() => undefined);

  /** localStorage key of this chat's draft. */
  const draftKey = `${chatType}:${conversationId}`;
  /** Key whose attachments currently live in state (switch detection). */
  const activeChatKeyRef = useRef(draftKey);

  /* ------------------------------- Autosizing -------------------------------- */

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight + 2, TEXTAREA_MAX_HEIGHT)}px`;
  }, []);

  /* -------------------------------- Typing ---------------------------------- */

  const stopTyping = useCallback(() => {
    typingStopTimer.current = null;
    useSocketStore.getState().sendTyping(conversationId, false);
  }, [conversationId]);

  const handleInputChange = (text: string) => {
    setValue(text);
    saveDraft(draftKey, text);
    if (text.length > 0) {
      useSocketStore.getState().sendTyping(conversationId, true);
      if (typingStopTimer.current) clearTimeout(typingStopTimer.current);
      typingStopTimer.current = setTimeout(stopTyping, 3000);
    } else if (typingStopTimer.current) {
      clearTimeout(typingStopTimer.current);
      stopTyping();
    }
  };

  // Clear the stop timer on unmount / chat switch and send the stop frame.
  useEffect(() => {
    return () => {
      if (typingStopTimer.current) clearTimeout(typingStopTimer.current);
      useSocketStore.getState().sendTyping(conversationId, false);
    };
  }, [conversationId]);

  // Restore the persisted draft when switching between chats.
  useEffect(() => {
    setValue(loadDraft(draftKey));
    const frame = requestAnimationFrame(resizeTextarea);
    return () => cancelAnimationFrame(frame);
  }, [draftKey, resizeTextarea]);

  /* --------------------------------- Media ---------------------------------- */

  /**
   * Orphan prevention (DELETE /media/{id}): discard pending chips when the
   * user leaves the chat — revoking preview URLs and deleting every
   * uploaded-but-unattached media row so the storage backend stays clean.
   * An in-flight upload batch is aborted (the backend discards partial
   * multiparts, so aborted uploads leave no orphan either).
   */
  const releaseAttachments = useCallback((list: PendingAttachment[]) => {
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
    for (const attachment of list) {
      URL.revokeObjectURL(attachment.previewUrl);
      if (attachment.media && !attachment.media.message_id) {
        apiClient
          .delete(`/api/v1/media/${attachment.media.id}`)
          .catch(() => undefined);
      }
    }
  }, []);

  const uploadFiles = useCallback(
    async (batch: PendingAttachment[]) => {
      if (batch.length === 0) return;
      setUploading(true);
      setUploadProgress(0);
      const controller = new AbortController();
      uploadAbortRef.current = controller;
      try {
        const result = await uploadFilesWithProgress<{ items: MediaRead[] }>(
          `/api/v1/conversations/${conversationIdRef.current}/media`,
          batch.map((b) => b.file),
          {
            onProgress: setUploadProgress,
            timeoutMs: MEDIA_UPLOAD_TIMEOUT_MS,
            signal: controller.signal,
          }
        );
        setAttachments((prev) =>
          prev.map((a) => {
            const index = batch.indexOf(a);
            return index >= 0
              ? { ...a, media: result.items[index] ?? a.media, error: undefined }
              : a;
          })
        );
      } catch (error) {
        const canceled =
          error instanceof ApiError && error.message === "Upload canceled.";
        if (!canceled) {
          toast.error(mediaUploadErrorMessage(error));
          setAttachments((prev) =>
            prev.map((a) =>
              batch.includes(a) ? { ...a, error: "Upload failed" } : a
            )
          );
        }
      } finally {
        if (uploadAbortRef.current === controller) uploadAbortRef.current = null;
        setUploading(false);
        setUploadProgress(null);
      }
    },
    []
  );

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      // A blocked pair cannot exchange messages or media — fail fast and
      // friendly instead of round-tripping a 403 from the backend.
      if (participantBlockedRef.current) {
        toast.info("You blocked this user — unblock them to send messages.");
        return;
      }
      const incoming = Array.from(files);
      if (incoming.length === 0) return;

      let accepted: PendingAttachment[] = [];
      setAttachments((prev) => {
        const room = MEDIA_MAX_FILES_PER_REQUEST - prev.length;
        if (room <= 0) return prev;
        for (const file of incoming.slice(0, room)) {
          const check = isAllowedUpload(file);
          if (!check.ok) continue;
          if (file.size === 0) continue;
          if (file.size > MEDIA_MAX_UPLOAD_BYTES) continue;
          accepted.push({
            previewUrl: URL.createObjectURL(file),
            file,
            media: null,
          });
        }
        return [...prev, ...accepted];
      });

      // Surface rejection reasons after the state pass.
      const room = MEDIA_MAX_FILES_PER_REQUEST - attachmentsRef.current.length;
      if (room <= 0) {
        toast.error(`At most ${MEDIA_MAX_FILES_PER_REQUEST} files per message.`);
        return;
      }
      if (accepted.length === 0) {
        const first = incoming[0];
        if (first) {
          const check = isAllowedUpload(first);
          if (!check.ok) {
            toast.error(`${first.name}: ${check.reason}`);
          } else if (first.size === 0) {
            toast.error(`${first.name}: empty files are not allowed.`);
          } else if (first.size > MEDIA_MAX_UPLOAD_BYTES) {
            toast.error(
              `${first.name}: ${formatBytes(first.size)} exceeds the 10 MB limit.`
            );
          }
        }
        return;
      }
      // Upload the accepted batch right away so sending feels instant later.
      void uploadFiles(accepted);
    },
    [uploadFiles]
  );
  addFilesRef.current = addFiles;

  const removeAttachment = (target: PendingAttachment) => {
    setAttachments((prev) => prev.filter((a) => a !== target));
    URL.revokeObjectURL(target.previewUrl);
    // Uploaded-but-unattached media is deleted immediately so orphan files
    // do not clutter the storage backend (DELETE /media/{id}; attached
    // media answers 409 and is ignored).
    if (target.media && !target.media.message_id) {
      apiClient
        .delete(`/api/v1/media/${target.media.id}`)
        .catch(() => undefined);
    }
  };

  /* ------------------- Chat-switch / unmount orphan cleanup ------------------ */

  // Conversation switch: ChatInput stays mounted across /c/1 -> /c/2
  // navigations, so the pending attachments of the previous chat must be
  // released before the composer is reused for the next one (otherwise an
  // upload addressed to chat A could be sent into chat B and rejected — or
  // worse, linger unattached forever).
  useEffect(() => {
    if (activeChatKeyRef.current === draftKey) return;
    const previous = attachmentsRef.current;
    activeChatKeyRef.current = draftKey;
    setAttachments([]);
    releaseAttachments(previous);
  }, [draftKey, releaseAttachments]);

  // Leaving the chat page entirely: same cleanup for whatever is left.
  useEffect(() => {
    return () => releaseAttachments(attachmentsRef.current);
  }, [releaseAttachments]);

  /* ------------------------- Chat-area drag & drop --------------------------- */

  // The viewport publishes files dropped anywhere over the chat surface.
  useEffect(() => subscribeToDroppedFiles((files) => addFilesRef.current(files)), []);

  /* --------------------------------- Sending --------------------------------- */

  const canSend =
    (value.trim().length > 0 || attachments.some((a) => a.media)) &&
    !uploading &&
    !participantBlocked;

  const send = async () => {
    if (participantBlockedRef.current) return;
    const body = value.trim() ? value.trim() : null;
    if (!body && !attachments.length) return;
    if (body && body.length > 4000) {
      toast.error("Messages are limited to 4000 characters.");
      return;
    }

    const mediaItems = attachments
      .map((a) => a.media)
      .filter((m): m is MediaRead => Boolean(m));

    // Reset composer optimistically.
    setValue("");
    clearDraft(draftKey);
    setAttachments((prev) => {
      for (const a of prev) URL.revokeObjectURL(a.previewUrl);
      return [];
    });
    resizeTextarea();
    if (typingStopTimer.current) {
      clearTimeout(typingStopTimer.current);
      typingStopTimer.current = null;
    }

    const store = useMessageStore.getState();
    const transport = (frame: {
      conversation_id: number;
      body: string | null;
      media_id: string | null;
      client_message_id: string;
    }) => useSocketStore.getState().transportMessage(frame);

    try {
      // First message carries the text (if any) + first attachment.
      await store.sendMessage({
        chatType,
        conversationId,
        body,
        mediaId: mediaItems[0]?.id ?? null,
        transport,
      });
      // Remaining attachments go out as media-only messages (one per message).
      for (const media of mediaItems.slice(1)) {
        await store.sendMessage({
          chatType,
          conversationId,
          body: null,
          mediaId: media.id,
          transport,
        });
      }
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The message could not be sent."
      );
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) void send();
    }
  };

  /** Insert an emoji at the caret (or at the end when the field is blurred). */
  const insertEmoji = (emoji: string) => {
    const el = textareaRef.current;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const next = `${value.slice(0, start)}${emoji}${value.slice(end)}`;
    if (next.length > 4200) return;
    handleInputChange(next);
    // Re-focus and restore the caret after the controlled value commits.
    requestAnimationFrame(() => {
      const node = textareaRef.current;
      if (!node) return;
      node.focus();
      const caret = start + emoji.length;
      node.setSelectionRange(caret, caret);
      resizeTextarea();
    });
  };

  /* ------------------------------- Drag & drop ------------------------------- */

  return (
    <div
      className={cn(
        "border-t bg-background p-3",
        dragging && "ring-2 ring-inset ring-primary"
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
      }}
    >
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.map((attachment, index) => (
            <div
              key={`${attachment.file.name}-${index}`}
              className={cn(
                "group relative flex items-center gap-2 rounded-lg border bg-muted/40 p-1.5 pr-7",
                attachment.error && "border-destructive/60 bg-destructive/5"
              )}
            >
              {attachment.file.type.startsWith("image/") ? (
                <img
                  src={attachment.previewUrl}
                  alt={attachment.file.name}
                  className={cn(
                    "size-9 rounded object-cover",
                    attachment.error && "opacity-60"
                  )}
                />
              ) : (
                <span className="flex size-9 items-center justify-center rounded bg-primary/10 text-primary">
                  <FileUp className="size-4" />
                </span>
              )}
              <span className="min-w-0">
                <span className="block max-w-36 truncate text-xs">
                  {attachment.file.name}
                  {attachment.media
                    ? ""
                    : attachment.error
                      ? ""
                      : " (uploading…)"}
                </span>
                <span
                  className={cn(
                    "block text-[10px]",
                    attachment.error
                      ? "text-destructive"
                      : "text-muted-foreground"
                  )}
                >
                  {attachment.error
                    ? `${attachment.error} — tap ✕ to remove`
                    : formatBytes(attachment.file.size)}
                </span>
              </span>
              <button
                type="button"
                aria-label={`Remove ${attachment.file.name}`}
                className="absolute right-1 top-1 text-muted-foreground hover:text-foreground"
                onClick={() => removeAttachment(attachment)}
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {uploading && uploadProgress !== null && (
        <div
          className="mb-2 h-1 w-full overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label="Uploading attachments"
          aria-valuenow={uploadProgress}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-150"
            style={{ width: `${uploadProgress}%` }}
          />
        </div>
      )}

      {chatType === "direct" && participantBlocked ? (
        /* Blocked pair: messaging is disabled in both directions. History
           stays readable above; unblocking re-enables the composer. */
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2.5">
          <p className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
            <Ban className="size-4 shrink-0 text-destructive" aria-hidden />
            <span className="min-w-0">
              You blocked{" "}
              <span className="font-medium text-foreground">
                @{participantUsername}
              </span>
              . New messages are stopped in both directions until you unblock.
            </span>
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => void unblockParticipant()}
            aria-label="Unblock this user"
          >
            <ShieldCheck className="size-4" />
            Unblock
          </Button>
        </div>
      ) : (
        <div className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.csv"
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
            aria-label="Attach files"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-9 shrink-0 text-muted-foreground"
            aria-label="Attach files"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachments.length >= MEDIA_MAX_FILES_PER_REQUEST}
          >
            {uploading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Paperclip className="size-4" />
            )}
          </Button>

          <EmojiPicker onPick={insertEmoji} />

          <Textarea
            ref={textareaRef}
            value={value}
            rows={1}
            dir="auto"
            maxLength={4200}
            placeholder="Write a message…"
            aria-label="Message text"
            className="chat-scroll bidi-plaintext max-h-[120px] min-h-9 resize-none py-2 text-start"
            onChange={(e) => {
              handleInputChange(e.target.value);
              resizeTextarea();
            }}
            onKeyDown={handleKeyDown}
          />

          <Button
            type="button"
            size="icon"
            className="size-9 shrink-0"
            aria-label="Send message"
            disabled={!canSend}
            onClick={() => void send()}
          >
            <SendHorizontal className="size-4" />
          </Button>
        </div>
      )}

      {dragging && (
        <div className="mt-2 rounded-lg border border-dashed border-primary/50 bg-primary/5 py-2 text-center text-xs text-primary">
          Drop files to attach (max {MEDIA_MAX_FILES_PER_REQUEST}, 10 MB each)
        </div>
      )}
    </div>
  );
}
