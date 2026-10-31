"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, Plus, X, UsersRound, ChevronDown } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UserAvatar } from "@/components/common/user-avatar";
import { useUiStore } from "@/stores/use-ui-store";
import { apiClient, ApiError } from "@/lib/api-client";
import {
  SEARCH_MIN_QUERY_LENGTH,
  SEARCH_PAGE_LIMIT,
} from "@/lib/constants";
import { useRaceGuard } from "@/hooks/use-race-guard";
import { displayNameOf } from "@/lib/utils";
import type { GroupRead, UserPage, UserPublic } from "@/types/api";

interface MemberDraft {
  user: UserPublic;
}

export function CreateGroupDialog() {
  const router = useRouter();
  const open = useUiStore((s) => s.createGroupOpen);
  const setOpen = useUiStore((s) => s.setCreateGroupOpen);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [members, setMembers] = useState<MemberDraft[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserPublic[]>([]);
  const [searching, setSearching] = useState(false);
  /** Page metadata for "Load more" (server-side prefix search paging). */
  const [total, setTotal] = useState<number | null>(null);
  const [nextOffset, setNextOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last-response-wins guard: a slow reply for an old query must never
  // overwrite the results of a newer one.
  const { issue, isCurrent } = useRaceGuard();
  // The member-picker filter reads members without re-triggering the search
  // effect on every pick/unpick (which would also reset the paging state).
  const membersRef = useRef(members);
  membersRef.current = members;

  useEffect(() => {
    if (!open) {
      setName("");
      setDescription("");
      setIsPublic(false);
      setMembers([]);
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
      setSearching(false);
      return;
    }
    setSearching(true);
    debounceRef.current = setTimeout(async () => {
      const ticket = issue();
      try {
        const page = await apiClient.get<UserPage>(
          "/api/v1/users/search",
          { query: { q: term, limit: SEARCH_PAGE_LIMIT } }
        );
        if (!isCurrent(ticket)) return; // stale — a newer query is pending
        setResults(
          page.items.filter(
            (u) => !membersRef.current.some((m) => m.user.username === u.username)
          )
        );
        setTotal(page.total);
        setNextOffset(page.offset + page.items.length);
      } catch {
        if (!isCurrent(ticket)) return;
        setResults([]);
        setTotal(null);
        setNextOffset(0);
      } finally {
        if (isCurrent(ticket)) setSearching(false);
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
        return [
          ...prev,
          ...page.items.filter(
            (u) =>
              !known.has(u.username) &&
              !membersRef.current.some((m) => m.user.username === u.username)
          ),
        ];
      });
      setTotal(page.total);
      setNextOffset(page.offset + page.items.length);
    } catch {
      // Keep current rows; the button stays for a retry.
    } finally {
      setLoadingMore(false);
    }
  }

  async function submit() {
    const trimmed = name.trim();
    if (trimmed.length === 0 || trimmed.length > 64) {
      toast.error("Group names are 1-64 characters.");
      return;
    }
    if (description.trim().length > 280) {
      toast.error("Descriptions are limited to 280 characters.");
      return;
    }
    setSubmitting(true);
    try {
      const group = await apiClient.post<GroupRead>("/api/v1/groups", {
        name: trimmed,
        description: description.trim() || null,
        visibility: isPublic ? "public" : "private",
      });
      // Invite the picked members (they must accept their invitations).
      for (const member of members) {
        try {
          await apiClient.post(`/api/v1/groups/${group.id}/invitations`, {
            username: member.user.username,
          });
        } catch {
          toast.warning(`Could not invite ${member.user.username}`);
        }
      }
      toast.success(
        members.length > 0
          ? `Group created. ${members.length} invitation(s) sent.`
          : "Group created."
      );
      setOpen(false);
      useUiStore.getState();
      router.push(`/g/${group.id}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not create the group."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create a group</DialogTitle>
          <DialogDescription>
            You become the owner. Invitees must accept before they join.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="group-name">Name</Label>
            <Input
              id="group-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Weekend Hikers"
              maxLength={64}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="group-description">Description (optional)</Label>
            <Textarea
              id="group-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Plans for Saturday morning hikes."
              maxLength={280}
              rows={2}
            />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <Label htmlFor="group-public" className="text-sm font-medium">
                Public group
              </Label>
              <p className="text-xs text-muted-foreground">
                Public groups appear in search and can be joined directly.
              </p>
            </div>
            <Switch
              id="group-public"
              checked={isPublic}
              onCheckedChange={setIsPublic}
            />
          </div>

          <div className="space-y-2">
            <Label>Invite members ({members.length})</Label>
            {members.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {members.map((m) => (
                  <span
                    key={m.user.username}
                    className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs"
                  >
                    @{m.user.username}
                    <button
                      type="button"
                      aria-label={`Remove ${m.user.username}`}
                      onClick={() =>
                        setMembers((prev) =>
                          prev.filter((x) => x.user.username !== m.user.username)
                        )
                      }
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="relative">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search users to invite…"
                aria-label="Search users to invite"
              />
              {searching && (
                <Loader2 className="absolute right-2.5 top-1/2 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
              )}
            </div>
            {results.length > 0 && (
              <ScrollArea className="max-h-40 rounded-lg border">
                <div className="p-1">
                  {results.map((user) => (
                    <button
                      key={user.username}
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md p-2 text-left hover:bg-accent"
                      onClick={() => {
                        setMembers((prev) => [
                          ...prev,
                          { user },
                        ]);
                        setResults((prev) =>
                          prev.filter((u) => u.username !== user.username)
                        );
                        setQuery("");
                      }}
                    >
                      <UserAvatar
                        name={user.display_name}
                        username={user.username}
                        className="size-7"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">
                          {displayNameOf(user.display_name, user.username)}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          @{user.username}
                        </span>
                      </span>
                      <Plus className="size-4 text-muted-foreground" />
                    </button>
                  ))}
                  {/* Prefix-search paging: when more matches exist server-side. */}
                  {total !== null && nextOffset < total && (
                    <button
                      type="button"
                      className="flex w-full items-center justify-center gap-1.5 rounded-md p-2 text-xs text-muted-foreground transition-colors hover:bg-accent disabled:opacity-60"
                      disabled={loadingMore}
                      onClick={() => void loadMore()}
                    >
                      {loadingMore ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <ChevronDown className="size-3.5" />
                      )}
                      Load more
                    </button>
                  )}
                </div>
              </ScrollArea>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || name.trim().length === 0}>
            {submitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <UsersRound className="size-4" />
            )}
            Create group
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
