"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, Search, MessageSquarePlus, ChevronDown } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { UserAvatar } from "@/components/common/user-avatar";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { apiClient, ApiError } from "@/lib/api-client";
import { displayNameOf } from "@/lib/utils";
import { SEARCH_MIN_QUERY_LENGTH, SEARCH_PAGE_LIMIT } from "@/lib/constants";
import { useRaceGuard } from "@/hooks/use-race-guard";
import type { UserPage, UserPublic } from "@/types/api";

export function NewChatDialog() {
  const router = useRouter();
  const open = useUiStore((s) => s.newChatOpen);
  const setOpen = useUiStore((s) => s.setNewChatOpen);
  const openDirectChat = useConversationStore((s) => s.openDirectChat);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserPublic[]>([]);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState<string | null>(null);
  /** Page metadata for "Load more" (server-side prefix search paging). */
  const [total, setTotal] = useState<number | null>(null);
  const [nextOffset, setNextOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last-response-wins guard: a slow reply for an old query must never
  // overwrite the results of a newer one.
  const { issue, isCurrent } = useRaceGuard();

  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
      setTotal(null);
      setNextOffset(0);
      return;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const term = query.trim();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (term.length < SEARCH_MIN_QUERY_LENGTH) {
      issue(); // invalidate any in-flight search for the previous query
      setResults([]);
      setTotal(null);
      setNextOffset(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      const ticket = issue();
      try {
        const page = await apiClient.get<UserPage>(
          "/api/v1/users/search",
          { query: { q: term, limit: SEARCH_PAGE_LIMIT } }
        );
        if (!isCurrent(ticket)) return; // stale — a newer query is pending
        setResults(page.items);
        setTotal(page.total);
        setNextOffset(page.offset + page.items.length);
      } catch {
        if (!isCurrent(ticket)) return;
        setResults([]);
        setTotal(null);
        setNextOffset(0);
      } finally {
        if (isCurrent(ticket)) setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, open, issue, isCurrent]);

  /** Fetch the next prefix-search page and merge (dedupe by username). */
  async function loadMore() {
    const term = query.trim();
    if (term.length < SEARCH_MIN_QUERY_LENGTH || loadingMore) return;
    if (total !== null && nextOffset >= total) return;
    setLoadingMore(true);
    try {
      const page = await apiClient.get<UserPage>(
        "/api/v1/users/search",
        { query: { q: term, limit: SEARCH_PAGE_LIMIT, offset: nextOffset } }
      );
      setResults((prev) => {
        const known = new Set(prev.map((u) => u.username));
        return [...prev, ...page.items.filter((u) => !known.has(u.username))];
      });
      setTotal(page.total);
      setNextOffset(page.offset + page.items.length);
    } catch {
      // Keep current rows; the button stays for a retry.
    } finally {
      setLoadingMore(false);
    }
  }

  async function start(username: string) {
    setStarting(username);
    try {
      const detail = await openDirectChat(username);
      setOpen(false);
      router.push(`/c/${detail.id}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not open the chat."
      );
    } finally {
      setStarting(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Start a new chat</DialogTitle>
          <DialogDescription>
            Find people by their username prefix (at least{" "}
            {SEARCH_MIN_QUERY_LENGTH} characters).
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. alice"
            className="pl-8"
            autoFocus
            aria-label="Search users"
          />
        </div>

        <ScrollArea className="max-h-80">
          <div className="space-y-1 pr-2">
            {loading && (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="size-5 animate-spin" />
              </div>
            )}
            {!loading && query.trim().length >= SEARCH_MIN_QUERY_LENGTH && results.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No users found. Usernames are prefix-matched; private profiles
                never appear in search.
              </p>
            )}
            {!loading &&
              results.map((user) => (
                <button
                  key={user.username}
                  type="button"
                  onClick={() => start(user.username)}
                  disabled={starting !== null}
                  className="flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors hover:bg-accent"
                >
                  <UserAvatar name={user.display_name} username={user.username} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {displayNameOf(user.display_name, user.username)}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      @{user.username}
                    </span>
                  </span>
                  {starting === user.username ? (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  ) : (
                    <MessageSquarePlus className="size-4 text-muted-foreground" />
                  )}
                </button>
              ))}
            {/* Prefix-search paging: when more matches exist server-side. */}
            {!loading && total !== null && nextOffset < total && (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-full gap-1.5 text-xs text-muted-foreground"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ChevronDown className="size-3.5" />
                )}
                Load more ({results.length}/{total})
              </Button>
            )}
            {!loading && query.trim().length < SEARCH_MIN_QUERY_LENGTH && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                Type at least {SEARCH_MIN_QUERY_LENGTH} characters to search.
              </p>
            )}
          </div>
        </ScrollArea>

        <Button variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </DialogContent>
    </Dialog>
  );
}
