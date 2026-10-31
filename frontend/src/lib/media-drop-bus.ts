"use client";

/**
 * Tiny one-slot bus that lets the chat viewport (which owns the large
 * message-history area) forward files dropped anywhere over the chat to
 * the ChatInput composer, which owns the attachment pipeline
 * (validation, upload, chips, send).
 *
 * A module-level listener is enough because exactly one composer is
 * mounted at a time; the drop zone lives above it in the same route.
 */

type DropListener = (files: File[]) => void;

let listener: DropListener | null = null;

/** Publish files dropped over the chat area (called by the drop zone). */
export function publishDroppedFiles(files: File[]): void {
  const usable = files.filter((file) => file instanceof File);
  if (usable.length === 0) return;
  listener?.(usable);
}

/**
 * Register the composer as the drop target. Returns an unsubscribe
 * function (called on unmount / chat switch).
 */
export function subscribeToDroppedFiles(l: DropListener): () => void {
  listener = l;
  return () => {
    if (listener === l) listener = null;
  };
}
