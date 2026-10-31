"use client";

/**
 * Discover public groups: GET /groups/search?q=… + POST /groups/{id}/join.
 *
 * Only PUBLIC groups are ever returned by the backend. Rows show the group
 * meta (owner, member count, description); non-members can join directly
 * (idempotent — a repeat returns the existing membership), members get an
 * "Open" shortcut into the chat.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ChevronDown, Compass, Loader2, LogIn, Search } from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { GroupAvatar } from "@/components/common/user-avatar";
import { EmptyState } from "@/components/common/empty-state";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { apiClient, ApiError } from "@/lib/api-client";
import { SEARCH_MIN_QUERY_LENGTH, SEARCH_PAGE_LIMIT } from "@/lib/constants";
import { useRaceGuard } from "@/hooks/use-race-guard";
import type { GroupPage, GroupRead } from "@/types/api";

export function DiscoverGroupsDialog() {
  const router = useRouter();
  const open = useUiStore((s) => s.discoverGroupsOpen);
  const setOpen = useUiStore((s) => s.setDiscoverGroupsOpen);
  const upsertGroup = useConversationStore((s) => s.upsertGroup);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GroupRead[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searched, setSearched] = useState(false);
  const [joiningId, setJoiningId] = useState<number | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last-response-wins: a slow reply for an older query must never
  // overwrite the results of a newer one.
  const { issue, isCurrent } = useRaceGuard();

  // Reset the search state whenever the dialog closes.
  useEffect(() => {
    if (!open) {
      issue(); // invalidate in-flight searches
      setQuery("");
      setResults([]);
      setTotal(null);
      setSearched(false);
      setLoading(false);
      setLoadingMore(false);
    }
  }, [open, issue]);

  useEffect(() => {
    if (!open) return;
    const term = query.trim();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (term.length < SEARCH_MIN_QUERY_LENGTH) {
      issue(); // invalidate in-flight searches
      setResults([]);
      setTotal(null);
      setSearched(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      const ticket = issue();
      try {
        const page = await apiClient.get<GroupPage>("/api/v1/groups/search", {
          query: { q: term, limit: SEARCH_PAGE_LIMIT },
        });
        if (!isCurrent(ticket)) return; // stale — a newer query is pending
        setResults(page.items);
        setTotal(page.total);
        setSearched(true);
      } catch {
        if (!isCurrent(ticket)) return;
        setResults([]);
        setTotal(null);
        setSearched(true);
      } finally {
        if (isCurrent(ticket)) setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, open, issue, isCurrent]);

  /** Fetch the next offset page for the CURRENT query and merge it. */
  async function loadMore() {
    const term = query.trim();
    if (term.length < SEARCH_MIN_QUERY_LENGTH) return;
    if (loadingMore || loading) return;
    if (total !== null && results.length >= total) return;
    setLoadingMore(true);
    const ticket = issue();
    try {
      const page = await apiClient.get<GroupPage>("/api/v1/groups/search", {
        query: { q: term, limit: SEARCH_PAGE_LIMIT, offset: results.length },
      });
      if (!isCurrent(ticket)) return;
      setResults((prev) => {
        const known = new Set(prev.map((g) => g.id));
        return [...prev, ...page.items.filter((g) => !known.has(g.id))];
      });
      setTotal(page.total);
    } catch {
      // Keep the existing results; the button stays available for a retry.
    } finally {
      if (isCurrent(ticket)) setLoadingMore(false);
    }
  }

  async function join(group: GroupRead) {
    setJoiningId(group.id);
    try {
      const joined = await apiClient.post<GroupRead>(
        `/api/v1/groups/${group.id}/join`
      );
      upsertGroup(joined);
      setResults((prev) =>
        prev.map((g) => (g.id === joined.id ? { ...joined, last_message: g.last_message } : g))
      );
      toast.success(`You joined ${joined.name}.`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not join the group."
      );
    } finally {
      setJoiningId(null);
    }
  }

  function openGroup(group: GroupRead) {
    upsertGroup(group);
    setOpen(false);
    router.push(`/g/${group.id}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Discover public groups</DialogTitle>
          <DialogDescription>
            Search public group names and descriptions, then join without an
            invitation. Private groups never appear here.
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. design"
            className="pl-8"
            autoFocus
            aria-label="Search public groups"
          />
        </div>

        <ScrollArea className="max-h-80">
          <div className="space-y-1 pr-2">
            {loading && (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="size-5 animate-spin" />
              </div>
            )}

            {!loading &&
              !searched &&
              query.trim().length < SEARCH_MIN_QUERY_LENGTH && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  Type at least {SEARCH_MIN_QUERY_LENGTH} characters to search.
                </p>
              )}

            {!loading && searched && results.length === 0 && (
              <EmptyState
                icon={Compass}
                title="No public groups match"
                description="Try a different keyword — only public groups are searchable."
                className="py-8"
              />
            )}

            {!loading &&
              results.map((group) => (
                <div
                  key={group.id}
                  className="flex items-center gap-3 rounded-lg border p-2.5"
                >
                  <GroupAvatar
                    name={group.name}
                    memberCount={group.member_count}
                    className="size-9"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 truncate text-sm font-medium">
                      {group.name}
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        public
                      </Badge>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {group.member_count} member
                      {group.member_count === 1 ? "" : "s"} · owner @
                      {group.owner_username}
                    </p>
                    {group.description && (
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                        {group.description}
                      </p>
                    )}
                  </div>
                  {group.is_member ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="shrink-0"
                      onClick={() => openGroup(group)}
                    >
                      Open
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      className="shrink-0"
                      disabled={joiningId !== null}
                      onClick={() => void join(group)}
                      aria-label={`Join ${group.name}`}
                    >
                      {joiningId === group.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <LogIn className="size-4" />
                      )}
                      Join
                    </Button>
                  )}
                </div>
              ))}
          </div>

          {/* Search paging: more matches beyond the first 20 results. */}
          {!loading &&
            searched &&
            total !== null &&
            results.length < total && (
              <div className="flex justify-center pt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1.5 text-xs text-muted-foreground"
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
              </div>
            )}
        </ScrollArea>

        <Button variant="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </DialogContent>
    </Dialog>
  );
}
