"use client";

/**
 * Group invitations center.
 *
 * - "Pending" tab: live invitations awaiting my answer (store-fed, accept /
 *   decline actions).
 * - "History" tab: the full invitation record from GET /groups/invitations
 *   — accepted, declined, revoked (and pending) rows with a status filter,
 *   respond dates and offset paging, so nothing beyond page 1 is invisible.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Bell, Check, ChevronDown, Clock, Loader2, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/common/empty-state";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { apiClient, ApiError } from "@/lib/api-client";
import { formatListTimestamp } from "@/lib/utils";
import { INVITATIONS_PAGE_LIMIT } from "@/lib/constants";
import type { GroupInvitationPage, GroupInvitationRead, InvitationStatus } from "@/types/api";

type HistoryFilter = "all" | InvitationStatus;

const HISTORY_FILTERS: { id: HistoryFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "accepted", label: "Accepted" },
  { id: "declined", label: "Declined" },
  { id: "revoked", label: "Revoked" },
];

/** Visual style + label for a invitation status badge. */
function StatusBadge({ status }: { status: InvitationStatus }) {
  const variant =
    status === "accepted"
      ? "default"
      : status === "declined"
        ? "secondary"
        : status === "revoked"
          ? "outline"
          : "default";
  return (
    <Badge
      variant={variant}
      className={
        status === "pending"
          ? "shrink-0 bg-amber-500/15 text-amber-700 dark:text-amber-400"
          : "shrink-0 capitalize"
      }
    >
      {status}
    </Badge>
  );
}

export function InvitationsDialog() {
  const open = useUiStore((s) => s.invitationsOpen);
  const setOpen = useUiStore((s) => s.setInvitationsOpen);
  const invitations = useConversationStore((s) => s.pendingInvitations);
  const loadGroups = useConversationStore((s) => s.loadGroups);
  const removeInvitation = useConversationStore((s) => s.removeInvitation);
  const [busy, setBusy] = useState<number | null>(null);
  const [tab, setTab] = useState<"pending" | "history">("pending");

  // History state (dialog-local — the store only needs pending rows).
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("all");
  const [history, setHistory] = useState<GroupInvitationRead[]>([]);
  const [historyTotal, setHistoryTotal] = useState<number | null>(null);
  const [historyNextOffset, setHistoryNextOffset] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  /** Bumped after accept/decline so the history list re-reads fresh states. */
  const [historyRefresh, setHistoryRefresh] = useState(0);

  const fetchHistory = useCallback(
    async (filter: HistoryFilter, offset: number) => {
      const page = await apiClient.get<GroupInvitationPage>(
        "/api/v1/groups/invitations",
        {
          query: {
            status: filter === "all" ? undefined : filter,
            limit: INVITATIONS_PAGE_LIMIT,
            offset,
          },
        }
      );
      return page;
    },
    []
  );

  // (Re)load page 1 whenever the History tab opens or the filter changes.
  useEffect(() => {
    if (!open || tab !== "history") return;
    let cancelled = false;
    setHistoryLoading(true);
    fetchHistory(historyFilter, 0)
      .then((page) => {
        if (cancelled) return;
        setHistory(page.items);
        setHistoryTotal(page.total);
        setHistoryNextOffset(page.offset + page.items.length);
      })
      .catch(() => {
        if (cancelled) return;
        setHistory([]);
        setHistoryTotal(null);
        setHistoryNextOffset(0);
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, tab, historyFilter, historyRefresh, fetchHistory]);

  async function loadMoreHistory() {
    if (loadingMore || historyLoading) return;
    if (historyTotal !== null && historyNextOffset >= historyTotal) return;
    setLoadingMore(true);
    try {
      const page = await fetchHistory(historyFilter, historyNextOffset);
      setHistory((prev) => {
        const known = new Set(prev.map((i) => i.id));
        return [...prev, ...page.items.filter((i) => !known.has(i.id))];
      });
      setHistoryTotal(page.total);
      setHistoryNextOffset(page.offset + page.items.length);
    } catch {
      // Keep the current rows; the button stays for a retry.
    } finally {
      setLoadingMore(false);
    }
  }

  async function respond(
    invitationId: number,
    groupId: number,
    action: "accept" | "decline"
  ) {
    setBusy(invitationId);
    try {
      await apiClient.post(
        `/api/v1/groups/${groupId}/invitations/${invitationId}/${action}`
      );
      removeInvitation(invitationId);
      if (action === "accept") {
        toast.success("Invitation accepted — the group is in your list.");
        await loadGroups(true);
      } else {
        toast.info("Invitation declined.");
      }
      // The history list now holds a stale status for this invitation.
      setHistoryRefresh((n) => n + 1);
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not respond to the invitation."
      );
      // Refresh to clear stale entries.
      void useConversationStore.getState().loadPendingInvitations(true);
    } finally {
      setBusy(null);
    }
  }

  function openGroup(groupId: number) {
    setOpen(false);
    window.location.assign(`/g/${groupId}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Group invitations</DialogTitle>
          <DialogDescription>
            Respond to pending invites, or browse the full history — accepted,
            declined and revoked invitations stay visible here.
          </DialogDescription>
        </DialogHeader>

        <div className="grid w-full grid-cols-2 gap-1 rounded-lg bg-muted p-1">
          <button
            type="button"
            onClick={() => setTab("pending")}
            className={
              tab === "pending"
                ? "rounded-md bg-background px-3 py-1.5 text-sm font-medium shadow-sm"
                : "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            }
          >
            Pending{invitations.length > 0 ? ` (${invitations.length})` : ""}
          </button>
          <button
            type="button"
            onClick={() => setTab("history")}
            className={
              tab === "history"
                ? "rounded-md bg-background px-3 py-1.5 text-sm font-medium shadow-sm"
                : "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            }
          >
            History
          </button>
        </div>

        {tab === "pending" ? (
          invitations.length === 0 ? (
            <EmptyState
              icon={Bell}
              title="No pending invitations"
              description="When someone invites you to a group, it shows up here."
              className="py-6"
            />
          ) : (
            <ScrollArea className="max-h-80">
              <div className="space-y-2 pr-2">
                {invitations.map((invitation) => (
                  <div
                    key={invitation.id}
                    className="flex items-center gap-3 rounded-lg border p-3"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Bell className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {invitation.group_name}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {invitation.inviter_username} invited you
                      </p>
                    </div>
                    <div className="flex gap-1.5">
                      <Button
                        size="icon"
                        className="size-8"
                        aria-label={`Accept invitation to ${invitation.group_name}`}
                        disabled={busy !== null}
                        onClick={() =>
                          respond(invitation.id, invitation.group_id, "accept")
                        }
                      >
                        {busy === invitation.id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Check className="size-4" />
                        )}
                      </Button>
                      <Button
                        size="icon"
                        variant="outline"
                        className="size-8"
                        aria-label={`Decline invitation to ${invitation.group_name}`}
                        disabled={busy !== null}
                        onClick={() =>
                          respond(invitation.id, invitation.group_id, "decline")
                        }
                      >
                        <X className="size-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )
        ) : (
          <>
            {/* Status filter chips */}
            <div className="flex flex-wrap gap-1.5">
              {HISTORY_FILTERS.map((filter) => (
                <Button
                  key={filter.id}
                  variant={historyFilter === filter.id ? "secondary" : "ghost"}
                  size="sm"
                  className="h-7 px-2.5 text-xs"
                  onClick={() => setHistoryFilter(filter.id)}
                >
                  {filter.label}
                </Button>
              ))}
            </div>

            <ScrollArea className="max-h-80">
              <div className="space-y-2 pr-2">
                {historyLoading ? (
                  <div className="flex items-center justify-center py-8 text-muted-foreground">
                    <Loader2 className="size-5 animate-spin" />
                  </div>
                ) : history.length === 0 ? (
                  <EmptyState
                    icon={Clock}
                    title="No invitation history"
                    description="Accepted, declined and revoked invitations appear here."
                    className="py-6"
                  />
                ) : (
                  history.map((invitation) => (
                    <div
                      key={invitation.id}
                      className="flex items-center gap-3 rounded-lg border p-3"
                    >
                      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                        <Bell className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-1.5 truncate text-sm font-medium">
                          <span className="truncate">{invitation.group_name}</span>
                          <StatusBadge status={invitation.status} />
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {invitation.inviter_username} invited{" "}
                          {invitation.invitee_username} ·{" "}
                          {formatListTimestamp(invitation.created_at)}
                        </p>
                        {invitation.responded_at && (
                          <p className="truncate text-[11px] text-muted-foreground">
                            responded {formatListTimestamp(invitation.responded_at)}
                          </p>
                        )}
                      </div>
                      {invitation.status === "pending" ? (
                        <div className="flex gap-1.5">
                          <Button
                            size="icon"
                            className="size-8"
                            aria-label={`Accept invitation to ${invitation.group_name}`}
                            disabled={busy !== null}
                            onClick={() =>
                              respond(invitation.id, invitation.group_id, "accept")
                            }
                          >
                            {busy === invitation.id ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Check className="size-4" />
                            )}
                          </Button>
                          <Button
                            size="icon"
                            variant="outline"
                            className="size-8"
                            aria-label={`Decline invitation to ${invitation.group_name}`}
                            disabled={busy !== null}
                            onClick={() =>
                              respond(invitation.id, invitation.group_id, "decline")
                            }
                          >
                            <X className="size-4" />
                          </Button>
                        </div>
                      ) : invitation.status === "accepted" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="shrink-0"
                          onClick={() => openGroup(invitation.group_id)}
                        >
                          Open
                        </Button>
                      ) : null}
                    </div>
                  ))
                )}

                {/* Offset paging across the history. */}
                {!historyLoading &&
                  historyTotal !== null &&
                  historyNextOffset < historyTotal && (
                    <div className="flex justify-center pt-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 gap-1.5 text-xs text-muted-foreground"
                        disabled={loadingMore}
                        onClick={() => void loadMoreHistory()}
                      >
                        {loadingMore ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <ChevronDown className="size-3.5" />
                        )}
                        Load more ({history.length}/{historyTotal})
                      </Button>
                    </div>
                  )}
              </div>
            </ScrollArea>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
