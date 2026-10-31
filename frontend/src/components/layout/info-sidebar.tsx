"use client";

/**
 * Right-hand drawer: participant profile for direct chats, full member
 * management (roles, invitations, ownership) for groups.
 *
 * Desktop: static panel. Mobile: overlay sheet.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Ban,
  Bell,
  CalendarDays,
  ChevronDown,
  Crown,
  Loader2,
  LogOut,
  MessageSquare,
  Search,
  Shield,
  ShieldCheck,
  ShieldX,
  Trash2,
  UserMinus,
  UserPlus,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { UserAvatar } from "@/components/common/user-avatar";
import { GroupAvatar } from "@/components/common/user-avatar";
import { useUiStore, type RightSidebarView } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { apiClient, ApiError } from "@/lib/api-client";
import {
  cn,
  displayNameOf,
  formatLastSeen,
  formatListTimestamp,
} from "@/lib/utils";
import type {
  GroupInvitationPage,
  GroupInvitationRead,
  GroupMemberPage,
  GroupMemberRead,
  GroupRead,
  InvitationStatus,
  UserPage,
  UserPublic,
} from "@/types/api";
import { useIsDesktop } from "@/hooks/use-mobile";
import { useContactsStore } from "@/stores/use-contacts-store";
import { useBlocksStore } from "@/stores/use-blocks-store";
import { useRaceGuard } from "@/hooks/use-race-guard";
import {
  GROUP_MEMBERS_MAX_PAGES,
  GROUP_MEMBERS_PAGE_LIMIT,
  INVITATIONS_PAGE_LIMIT,
  INVITE_SEARCH_PAGE_LIMIT,
  SEARCH_MIN_QUERY_LENGTH,
} from "@/lib/constants";

export function InfoSidebar() {
  const view = useUiStore((s) => s.rightSidebar);
  const profileUsername = useUiStore((s) => s.profileUsername);
  const close = useUiStore((s) => s.closeRightSidebar);
  const activeChat = useConversationStore((s) => s.activeChat);
  const isDesktop = useIsDesktop();

  const open = Boolean(view);

  /*
   * The last opened view stays in state (updated asynchronously so the
   * effect never calls setState synchronously). While the panel is closed
   * the width-0 + `inert` aside hides it; keeping the content mounted is
   * what makes both the open and close width transitions slide over real
   * content with zero flicker. Cost is one idle panel at most — equivalent
   * to a panel the user simply left open.
   */
  const [lastOpenView, setLastOpenView] = useState<RightSidebarView>(null);

  useEffect(() => {
    if (!view) return;
    const sync = setTimeout(() => setLastOpenView(view), 0);
    return () => clearTimeout(sync);
  }, [view]);

  const shownView = view ?? lastOpenView;

  const content =
    shownView === "user-profile" ? (
      profileUsername ? (
        <UserProfilePanel username={profileUsername} />
      ) : null
    ) : !activeChat ? null : activeChat.type === "direct" ? (
      <DirectInfo conversationId={activeChat.id} />
    ) : (
      <GroupInfo groupId={activeChat.id} />
    );

  const title =
    shownView === "user-profile"
      ? "Profile"
      : shownView === "group-members"
        ? "Group details"
        : "Profile";

  // Below `lg` (phones AND tablets) the panel is an overlay sheet so it is
  // never silently invisible; from `lg` up it is a static right column.
  if (!isDesktop) {
    if (!view) return null;
    return (
      <Sheet open onOpenChange={(next) => !next && close()}>
        <SheetContent
          side="right"
          aria-describedby={undefined}
          className="flex w-full max-w-full flex-col gap-0 p-0 sm:max-w-sm"
        >
          <SheetTitle className="sr-only">{title}</SheetTitle>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto chat-scroll">
            {content}
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  /*
   * Desktop: the column slides open by animating its width (0 -> 20/24rem)
   * so the chat feed is never abruptly squashed — it reflows smoothly.
   * The inner wrapper keeps a fixed width so content does not reflow
   * during the animation; `inert` keeps the hidden panel unreachable.
   */
  return (
    <aside
      aria-hidden={!open}
      inert={!open}
      className={cn(
        "relative h-full shrink-0 overflow-hidden border-l bg-background transition-[width,border-color] duration-300 ease-in-out",
        open ? "w-80 border-border lg:w-96" : "w-0 border-transparent"
      )}
    >
      <div className="flex h-full w-80 flex-col lg:w-96">
        {shownView ? content : null}
      </div>
    </aside>
  );
}

/* ------------------------------- Direct variant ----------------------------- */

function DirectInfo({ conversationId }: { conversationId: number }) {
  const close = useUiStore((s) => s.closeRightSidebar);
  const conversation = useConversationStore((s) =>
    s.conversations.find((c) => c.id === conversationId)
  );
  const [busyAction, setBusyAction] = useState<"contact" | "block" | null>(null);
  /** Destructive relationship actions confirm before their endpoint fires. */
  const [confirmAction, setConfirmAction] = useState<
    "block" | "remove-contact" | null
  >(null);
  const isContact = useContactsStore((s) =>
    s.contacts.some((c) => c.username === conversation?.participant.username)
  );
  const contactBusy = useContactsStore(
    (s) => s.busyUsername !== null && s.busyUsername === conversation?.participant.username
  );
  const isBlocked = useBlocksStore((s) =>
    s.blocked.some((u) => u.username === conversation?.participant.username)
  );
  const blockBusy = useBlocksStore(
    (s) => s.busyUsername !== null && s.busyUsername === conversation?.participant.username
  );

  // Warm both relationship lists so the Add/Remove and Block states are
  // correct on first paint.
  useEffect(() => {
    void useContactsStore.getState().load();
    void useBlocksStore.getState().load();
  }, []);

  if (!conversation) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Conversation unavailable.
      </div>
    );
  }

  const { participant } = conversation;
  const lastSeen = formatLastSeen(participant.last_seen_at);

  /** Adding is immediate; removal is destructive and goes through confirm. */
  async function addContact() {
    setBusyAction("contact");
    try {
      await useContactsStore.getState().add(participant.username);
      toast.success(`@${participant.username} added to contacts`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function removeContact() {
    setBusyAction("contact");
    try {
      await useContactsStore.getState().remove(participant.username);
      toast.info(`@${participant.username} removed from contacts`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  }

  async function blockUser() {
    setBusyAction("block");
    try {
      await useBlocksStore.getState().block(participant.username);
      toast.success(`Blocked @${participant.username}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  }

  /** Unblocking is non-destructive — no confirmation needed. */
  async function unblockUser() {
    setBusyAction("block");
    try {
      await useBlocksStore.getState().unblock(participant.username);
      toast.info(`Unblocked @${participant.username}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <PanelShell title="Profile" onClose={close}>
      <div className="flex flex-col items-center gap-3 p-6 pt-2">
        <UserAvatar
          name={participant.display_name}
          username={participant.username}
          className="size-20 text-xl"
          showPresence
          presenceOnline={Boolean(
            participant.last_seen_at &&
              Date.now() - new Date(participant.last_seen_at).getTime() < 90_000
          )}
        />
        <div className="text-center">
          <p className="text-base font-semibold">
            {displayNameOf(participant.display_name, participant.username)}
            {isBlocked && (
              <Badge variant="destructive" className="ml-2 align-middle text-[10px]">
                Blocked
              </Badge>
            )}
          </p>
          <p className="text-sm text-muted-foreground">@{participant.username}</p>
        </div>
        {participant.bio && (
          <p className="text-center text-sm text-muted-foreground">{participant.bio}</p>
        )}
        <div className="mt-2 w-full space-y-1.5 rounded-lg border p-3 text-xs text-muted-foreground">
          <p className="flex items-center gap-2">
            <CalendarDays className="size-3.5" />
            Joined {new Date(participant.created_at).toLocaleDateString()}
          </p>
          <p>{lastSeen ? `Last seen ${lastSeen.replace("active ", "")}` : "Last seen hidden"}</p>
          <p>Visibility: {participant.profile_visibility}</p>
          {isBlocked && (
            <p className="font-medium text-destructive">
              Messaging is stopped in both directions.
            </p>
          )}
          {"message_count" in conversation &&
            typeof conversation.message_count === "number" && (
              <p>Messages so far: {conversation.message_count}</p>
            )}
        </div>
      </div>
      <Separator />
      <div className="space-y-2 p-4">
        {/* Contact status is hidden while a block is active — the backend
            refuses contact operations between blocked accounts anyway. */}
        {!isBlocked &&
          (isContact ? (
            <Button
              variant="outline"
              className="w-full"
              onClick={() => setConfirmAction("remove-contact")}
              disabled={contactBusy}
            >
              {contactBusy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <UserMinus className="size-4" />
              )}
              Remove from contacts
            </Button>
          ) : (
            <Button
              variant="outline"
              className="w-full"
              onClick={addContact}
              disabled={contactBusy}
            >
              {contactBusy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <UserPlus className="size-4" />
              )}
              Add to contacts
            </Button>
          ))}
        {isBlocked ? (
          <Button
            variant="outline"
            className="w-full"
            onClick={unblockUser}
            disabled={blockBusy}
          >
            {blockBusy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ShieldCheck className="size-4" />
            )}
            Unblock user
          </Button>
        ) : (
          <Button
            variant="outline"
            className="w-full text-destructive hover:text-destructive"
            onClick={() => setConfirmAction("block")}
            disabled={blockBusy}
          >
            {blockBusy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Ban className="size-4" />
            )}
            Block user
          </Button>
        )}
        <p className="text-xs text-muted-foreground">
          Blocks stop new messages in both directions; history stays readable.
        </p>
      </div>

      {/* Confirmation for the two destructive relationship actions. */}
      <AlertDialog
        open={confirmAction !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmAction(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction === "block"
                ? `Block @${participant.username}?`
                : `Remove @${participant.username} from contacts?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction === "block"
                ? "Blocking stops new messages in both directions and hides each of you from the other's search results and contact adds. History stays readable; you can unblock at any time."
                : "They will no longer appear in your contact list. Your chat history is kept and you can add them again at any time."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busyAction !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busyAction !== null}
              onClick={(event) => {
                event.preventDefault();
                if (confirmAction === "block") void blockUser();
                else if (confirmAction === "remove-contact") void removeContact();
              }}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {busyAction && <Loader2 className="mr-1 size-4 animate-spin" />}
              {confirmAction === "block" ? "Block user" : "Remove contact"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PanelShell>
  );
}

/* ----------------------------- Public user profile --------------------------- */

/**
 * Profile drawer for ANY username (opened by clicking a group member).
 * Backed by GET /users/{username}: bio, visibility and last-seen come from
 * the public profile endpoint, not from the chat lists.
 */
function UserProfilePanel({ username }: { username: string }) {
  const close = useUiStore((s) => s.closeRightSidebar);
  const router = useRouter();
  const me = useAuthStore((s) => s.user?.username ?? "");
  const isContact = useContactsStore((s) => s.contacts.some((c) => c.username === username));
  const contactBusy = useContactsStore((s) => s.busyUsername === username);
  const isBlocked = useBlocksStore((s) => s.blocked.some((u) => u.username === username));
  const blockBusy = useBlocksStore((s) => s.busyUsername === username);

  const [profile, setProfile] = useState<UserPublic | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<"contact" | "message" | "block" | null>(null);
  /** Destructive relationship actions confirm before their endpoint fires. */
  const [confirmAction, setConfirmAction] = useState<
    "block" | "remove-contact" | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setProfile(null);
    apiClient
      .get<UserPublic>(`/api/v1/users/${encodeURIComponent(username)}`)
      .then((user) => {
        if (cancelled) return;
        setProfile(user);
        setPhase("ready");
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 404) {
          setPhase("missing");
        } else {
          setErrorMsg(
            error instanceof ApiError ? error.message : "Could not load the profile."
          );
          setPhase("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [username]);

  // Warm both relationship lists so the Add/Remove and Block states are correct.
  useEffect(() => {
    void useContactsStore.getState().load();
    void useBlocksStore.getState().load();
  }, []);

  /** Adding is immediate; removal is destructive and goes through confirm. */
  async function addContact() {
    setBusyAction("contact");
    try {
      await useContactsStore.getState().add(username);
      toast.success(`@${username} added to contacts`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "The action failed.");
    } finally {
      setBusyAction(null);
    }
  }

  async function removeContact() {
    setBusyAction("contact");
    try {
      await useContactsStore.getState().remove(username);
      toast.info(`@${username} removed from contacts`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "The action failed.");
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  }

  async function startChat() {
    setBusyAction("message");
    try {
      const detail = await useConversationStore.getState().openDirectChat(username);
      close();
      router.push(`/c/${detail.id}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not open the chat."
      );
      setBusyAction(null);
    }
  }

  async function blockUser() {
    setBusyAction("block");
    try {
      await useBlocksStore.getState().block(username);
      toast.success(`Blocked @${username}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "The action failed.");
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  }

  /** Unblocking is non-destructive — no confirmation needed. */
  async function unblockUser() {
    setBusyAction("block");
    try {
      await useBlocksStore.getState().unblock(username);
      toast.info(`Unblocked @${username}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "The action failed.");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <PanelShell title="Profile" onClose={close}>
      {phase === "loading" && (
        <div className="flex flex-1 items-center justify-center py-12">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {phase === "missing" && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <ShieldX className="size-8 text-muted-foreground" />
          <p className="text-sm font-medium">Profile unavailable</p>
          <p className="text-xs text-muted-foreground">
            This profile is private, blocked, or does not exist.
          </p>
        </div>
      )}
      {phase === "error" && (
        <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-destructive">
          {errorMsg ?? "Could not load the profile."}
        </div>
      )}
      {phase === "ready" && profile && (
        <>
          <div className="flex flex-col items-center gap-3 p-6 pt-2">
            <UserAvatar
              name={profile.display_name}
              username={profile.username}
              className="size-20 text-xl"
            />
            <div className="text-center">
              <p className="text-base font-semibold">
                {displayNameOf(profile.display_name, profile.username)}
                {profile.username === me && (
                  <span className="ml-1 text-xs font-normal text-muted-foreground">(you)</span>
                )}
                {isBlocked && (
                  <Badge variant="destructive" className="ml-2 align-middle text-[10px]">
                    Blocked
                  </Badge>
                )}
              </p>
              <p className="text-sm text-muted-foreground">@{profile.username}</p>
            </div>
            {profile.bio && (
              <p className="text-center text-sm text-muted-foreground">{profile.bio}</p>
            )}
            <div className="mt-2 w-full space-y-1.5 rounded-lg border p-3 text-xs text-muted-foreground">
              <p className="flex items-center gap-2">
                <CalendarDays className="size-3.5" />
                Joined {new Date(profile.created_at).toLocaleDateString()}
              </p>
              <p>
                {profile.last_seen_at
                  ? `Last seen ${formatLastSeen(profile.last_seen_at)?.replace("active ", "") ?? ""}`
                  : "Last seen hidden"}
              </p>
              <p>Visibility: {profile.profile_visibility}</p>
              {isBlocked && (
                <p className="font-medium text-destructive">
                  Messaging is stopped in both directions.
                </p>
              )}
            </div>
          </div>
          <Separator />
          {profile.username !== me && (
            <div className="space-y-2 p-4">
              {/* Contact status and messaging are hidden while a block is
                  active — the backend refuses both between blocked pairs. */}
              {!isBlocked && (
                <>
                  {isContact ? (
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => setConfirmAction("remove-contact")}
                      disabled={contactBusy}
                    >
                      {contactBusy ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <UserMinus className="size-4" />
                      )}
                      Remove from contacts
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={addContact}
                      disabled={contactBusy}
                    >
                      {contactBusy ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <UserPlus className="size-4" />
                      )}
                      Add to contacts
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={startChat}
                    disabled={busyAction === "message"}
                    aria-label="Message this person"
                  >
                    {busyAction === "message" ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <MessageSquare className="size-4" />
                    )}
                    Message
                  </Button>
                </>
              )}
              {isBlocked ? (
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={unblockUser}
                  disabled={blockBusy}
                >
                  {blockBusy ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <ShieldCheck className="size-4" />
                  )}
                  Unblock user
                </Button>
              ) : (
                <Button
                  variant="outline"
                  className="w-full text-destructive hover:text-destructive"
                  onClick={() => setConfirmAction("block")}
                  disabled={busyAction === "block"}
                >
                  {busyAction === "block" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Ban className="size-4" />
                  )}
                  Block user
                </Button>
              )}
            </div>
          )}
        </>
      )}

      {/* Confirmation for the two destructive relationship actions. */}
      <AlertDialog
        open={confirmAction !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmAction(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction === "block"
                ? `Block @${username}?`
                : `Remove @${username} from contacts?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction === "block"
                ? "Blocking stops new messages in both directions and hides each of you from the other's search results and contact adds. History stays readable; you can unblock at any time."
                : "They will no longer appear in your contact list. Your chat history is kept and you can add them again at any time."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busyAction !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busyAction !== null}
              onClick={(event) => {
                event.preventDefault();
                if (confirmAction === "block") void blockUser();
                else if (confirmAction === "remove-contact") void removeContact();
              }}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {busyAction && <Loader2 className="mr-1 size-4 animate-spin" />}
              {confirmAction === "block" ? "Block user" : "Remove contact"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PanelShell>
  );
}

/* -------------------------------- Group variant ------------------------------ */

/**
 * Destructive / irreversible group actions always route through a typed
 * confirmation dialog before their endpoint call fires.
 */
type ConfirmAction =
  | { kind: "leave" }
  | { kind: "remove"; member: GroupMemberRead }
  | { kind: "transfer"; member: GroupMemberRead };

function GroupInfo({ groupId }: { groupId: number }) {
  const close = useUiStore((s) => s.closeRightSidebar);
  const router = useRouter();
  const openUserProfile = useUiStore((s) => s.openUserProfile);
  const me = useAuthStore((s) => s.user?.username ?? "");
  const group = useConversationStore((s) => s.groups.find((g) => g.id === groupId));

  const [members, setMembers] = useState<GroupMemberRead[]>([]);
  const [memberTotal, setMemberTotal] = useState<number | null>(null);
  const [loadingMembers, setLoadingMembers] = useState(true);
  const [loadingMoreMembers, setLoadingMoreMembers] = useState(false);
  const [inviteQuery, setInviteQuery] = useState("");
  const [inviteResults, setInviteResults] = useState<UserPublic[]>([]);
  const [inviteTotal, setInviteTotal] = useState<number | null>(null);
  /** Raw rows fetched for the current query (before member filtering). */
  const [inviteLoaded, setInviteLoaded] = useState(0);
  const [inviteNextOffset, setInviteNextOffset] = useState(0);
  const [inviteSearchLoadingMore, setInviteSearchLoadingMore] = useState(false);
  const [inviting, setInviting] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editVisibility, setEditVisibility] = useState<"private" | "public">("private");
  // Destructive actions (leave / kick / transfer) confirm before firing.
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  // Outgoing invitation history: status filter + offset paging (managers).
  const [outgoingStatus, setOutgoingStatus] = useState<InvitationStatus>("pending");
  const [outgoingInvites, setOutgoingInvites] = useState<GroupInvitationRead[]>([]);
  const [outgoingTotal, setOutgoingTotal] = useState<number | null>(null);
  const [outgoingNextOffset, setOutgoingNextOffset] = useState(0);
  const [invitesLoading, setInvitesLoading] = useState(false);
  const [loadingMoreInvites, setLoadingMoreInvites] = useState(false);
  const [revokingId, setRevokingId] = useState<number | null>(null);
  const [invitesRefresh, setInvitesRefresh] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Race guard for the invite search (stale slow replies must not win).
  const { issue, isCurrent } = useRaceGuard();
  // Bumped when a WS event (join / leave / kick / role change / ownership
  // transfer) says the member list is stale while this panel is open.
  const [membersRefresh, setMembersRefresh] = useState(0);

  // Hoisted above the early return so hooks keep a stable order; a role of
  // null (group not in the list yet / loading) simply disables admin areas.
  const myRole = group?.role ?? null;
  const isOwner = myRole === "owner";
  const isAdmin = myRole === "owner" || myRole === "admin";

  // Group members — page 1 renders immediately, then the remaining pages
  // are pulled in the background so groups larger than one request
  // (limit = 100) are no longer silently truncated. Bounded by
  // GROUP_MEMBERS_MAX_PAGES (2 000 rows) to keep the burst sane.
  useEffect(() => {
    let cancelled = false;
    setLoadingMembers(true);
    setLoadingMoreMembers(false);
    setMembers([]);
    setMemberTotal(null);
    (async () => {
      try {
        const first = await apiClient.get<GroupMemberPage>(
          `/api/v1/groups/${groupId}/members`,
          { query: { limit: GROUP_MEMBERS_PAGE_LIMIT } }
        );
        if (cancelled) return;
        setMembers(first.items);
        setMemberTotal(first.total);
        setLoadingMembers(false);

        let offset = first.offset + first.items.length;
        let total = first.total;
        for (let page = 1; page < GROUP_MEMBERS_MAX_PAGES && offset < total; page += 1) {
          if (cancelled) return;
          setLoadingMoreMembers(true);
          const next = await apiClient.get<GroupMemberPage>(
            `/api/v1/groups/${groupId}/members`,
            { query: { limit: GROUP_MEMBERS_PAGE_LIMIT, offset } }
          );
          if (cancelled) return;
          setMembers((prev) => {
            const known = new Set(prev.map((m) => m.user_id));
            return [...prev, ...next.items.filter((m) => !known.has(m.user_id))];
          });
          offset = next.offset + next.items.length;
          total = next.total;
          setMemberTotal(total);
        }
      } catch {
        if (!cancelled) setMembers([]);
      } finally {
        if (!cancelled) {
          setLoadingMembers(false);
          setLoadingMoreMembers(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, membersRefresh]);

  // Live member panel: the socket store announces membership / role /
  // ownership changes for this group; re-pull the member list when seen.
  useEffect(() => {
    function onMembersChanged(event: Event) {
      const detail = (event as CustomEvent<{ groupId: number }>).detail;
      if (detail?.groupId === groupId) setMembersRefresh((n) => n + 1);
    }
    window.addEventListener("messenger:group-members-changed", onMembersChanged);
    return () => {
      window.removeEventListener("messenger:group-members-changed", onMembersChanged);
    };
  }, [groupId]);

  // Outgoing invitation history for THIS group (owners and admins only):
  // status-filtered and offset-paged, so accepted / declined / revoked
  // records and pages beyond the first 50 rows stay visible.
  useEffect(() => {
    if (!isAdmin) {
      setOutgoingInvites([]);
      setOutgoingTotal(null);
      setOutgoingNextOffset(0);
      return;
    }
    let cancelled = false;
    setInvitesLoading(true);
    apiClient
      .get<GroupInvitationPage>(
        `/api/v1/groups/${groupId}/invitations`,
        { query: { status: outgoingStatus, limit: INVITATIONS_PAGE_LIMIT } }
      )
      .then((page) => {
        if (cancelled) return;
        setOutgoingInvites(page.items);
        setOutgoingTotal(page.total);
        setOutgoingNextOffset(page.offset + page.items.length);
      })
      .catch(() => {
        if (cancelled) return;
        setOutgoingInvites([]);
        setOutgoingTotal(null);
        setOutgoingNextOffset(0);
      })
      .finally(() => {
        if (!cancelled) setInvitesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [groupId, isAdmin, outgoingStatus, invitesRefresh]);

  async function loadMoreOutgoing() {
    if (!isAdmin || invitesLoading || loadingMoreInvites) return;
    if (outgoingTotal !== null && outgoingNextOffset >= outgoingTotal) return;
    setLoadingMoreInvites(true);
    try {
      const page = await apiClient.get<GroupInvitationPage>(
        `/api/v1/groups/${group!.id}/invitations`,
        {
          query: {
            status: outgoingStatus,
            limit: INVITATIONS_PAGE_LIMIT,
            offset: outgoingNextOffset,
          },
        }
      );
      setOutgoingInvites((prev) => {
        const known = new Set(prev.map((i) => i.id));
        return [...prev, ...page.items.filter((i) => !known.has(i.id))];
      });
      setOutgoingTotal(page.total);
      setOutgoingNextOffset(page.offset + page.items.length);
    } catch {
      // Keep current rows; the button stays for a retry.
    } finally {
      setLoadingMoreInvites(false);
    }
  }

  useEffect(() => {
    const term = inviteQuery.trim();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (term.length < SEARCH_MIN_QUERY_LENGTH) {
      issue(); // invalidate any in-flight search for the previous query
      setInviteResults([]);
      setInviteTotal(null);
      setInviteLoaded(0);
      setInviteNextOffset(0);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      const ticket = issue();
      try {
        const page = await apiClient.get<UserPage>(
          "/api/v1/users/search",
          { query: { q: term, limit: INVITE_SEARCH_PAGE_LIMIT } }
        );
        if (!isCurrent(ticket)) return; // stale — a newer query is pending
        setInviteResults(
          page.items.filter((u) => !members.some((m) => m.username === u.username))
        );
        setInviteTotal(page.total);
        setInviteLoaded(page.items.length);
        setInviteNextOffset(page.offset + page.items.length);
      } catch {
        if (!isCurrent(ticket)) return;
        setInviteResults([]);
        setInviteTotal(null);
        setInviteLoaded(0);
        setInviteNextOffset(0);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [inviteQuery, members, issue, isCurrent]);

  if (!group) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Group unavailable.
      </div>
    );
  }

  async function invite(username: string) {
    setInviting(username);
    try {
      await apiClient.post(`/api/v1/groups/${group!.id}/invitations`, { username });
      toast.success(`Invitation sent to @${username}`);
      setInviteResults((prev) => prev.filter((u) => u.username !== username));
      // Show the new invitation in the outgoing list right away.
      setInvitesRefresh((n) => n + 1);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : `Could not invite @${username}.`
      );
    } finally {
      setInviting(null);
    }
  }

  /** Offset paging for the invite search results (Load more). */
  async function loadMoreInviteResults() {
    const term = inviteQuery.trim();
    if (term.length < SEARCH_MIN_QUERY_LENGTH || inviteSearchLoadingMore) return;
    if (inviteTotal !== null && inviteNextOffset >= inviteTotal) return;
    setInviteSearchLoadingMore(true);
    try {
      const page = await apiClient.get<UserPage>(
        "/api/v1/users/search",
        {
          query: {
            q: term,
            limit: INVITE_SEARCH_PAGE_LIMIT,
            offset: inviteNextOffset,
          },
        }
      );
      setInviteResults((prev) => {
        const known = new Set(prev.map((u) => u.username));
        return [
          ...prev,
          ...page.items.filter(
            (u) =>
              !known.has(u.username) &&
              !members.some((m) => m.username === u.username)
          ),
        ];
      });
      setInviteTotal(page.total);
      setInviteLoaded((prev) => prev + page.items.length);
      setInviteNextOffset(page.offset + page.items.length);
    } catch {
      // Keep current rows; the button stays for a retry.
    } finally {
      setInviteSearchLoadingMore(false);
    }
  }

  async function revokeInvitation(invitation: GroupInvitationRead) {
    setRevokingId(invitation.id);
    try {
      try {
        await apiClient.post(
          `/api/v1/groups/${group!.id}/invitations/${invitation.id}/revoke`
        );
      } catch (postError) {
        // DELETE-form fallback: proxies that mangle POSTs on moderation
        // routes still accept the DELETE alias. Definitive statuses (the
        // invitation is gone / not ours to revoke) are not retried.
        const status = postError instanceof ApiError ? postError.status : 0;
        if ([403, 404, 409].includes(status)) throw postError;
        await apiClient.delete(
          `/api/v1/groups/${group!.id}/invitations/${invitation.id}`
        );
      }
      setOutgoingInvites((prev) => prev.filter((i) => i.id !== invitation.id));
      toast.info(`Invitation for @${invitation.invitee_username} revoked`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Revoke failed."
      );
    } finally {
      setRevokingId(null);
    }
  }

  async function changeRole(member: GroupMemberRead, role: "admin" | "member") {
    try {
      await apiClient.patch(`/api/v1/groups/${group!.id}/members/${member.user_id}`, {
        role,
      });
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role } : m))
      );
      toast.success(`@${member.username} is now ${role}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Role change failed.");
    }
  }

  async function removeMember(member: GroupMemberRead) {
    try {
      await apiClient.delete(`/api/v1/groups/${group!.id}/members/${member.user_id}`);
      setMembers((prev) => prev.filter((m) => m.user_id !== member.user_id));
      toast.info(`@${member.username} was removed`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Remove failed.");
    }
  }

  async function transferOwnership(member: GroupMemberRead) {
    try {
      await apiClient.post(`/api/v1/groups/${group!.id}/transfer-owner`, {
        user_id: member.user_id,
      });
      toast.success(`@${member.username} is now the owner`);
      setMembers((prev) =>
        prev.map((m) =>
          m.user_id === member.user_id
            ? { ...m, role: "owner" }
            : m.username === me
              ? { ...m, role: "admin" }
              : m
        )
      );
      // Keep the sidebar header consistent before the WS frame arrives.
      if (group) {
        useConversationStore.getState().upsertGroup({
          ...group,
          owner_username: member.username,
          role: me === member.username ? "owner" : "admin",
        });
      }
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Ownership transfer failed."
      );
    }
  }

  async function leaveGroup() {
    setLeaving(true);
    try {
      await apiClient.post(`/api/v1/groups/${group!.id}/leave`);
      useConversationStore.getState().handleGroupRemoved(group!.id);
      toast.info(`You left ${group!.name}`);
      close();
      router.push("/");
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not leave the group. The owner must transfer ownership first."
      );
    } finally {
      setLeaving(false);
    }
  }

  async function saveEdits() {
    try {
      // visibility is owner-only: admins must not send the field or the
      // backend rejects the whole PATCH with 403.
      const updated = await apiClient.patch<GroupRead>(`/api/v1/groups/${group!.id}`, {
        name: editName.trim(),
        description: editDescription.trim(),
        ...(isOwner ? { visibility: editVisibility } : {}),
      });
      useConversationStore.getState().upsertGroup(updated);
      setEditing(false);
      toast.success("Group updated.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Update failed.");
    }
  }

  /** Confirm-dialog executor for the three destructive actions. */
  async function runConfirmAction() {
    if (!confirmAction || !group) return;
    setConfirmBusy(true);
    try {
      if (confirmAction.kind === "leave") {
        await leaveGroup();
      } else if (confirmAction.kind === "remove") {
        await removeMember(confirmAction.member);
      } else {
        await transferOwnership(confirmAction.member);
      }
      setConfirmAction(null);
    } finally {
      setConfirmBusy(false);
    }
  }

  return (
    <PanelShell title="Group details" onClose={close}>
      <div className="flex flex-col items-center gap-3 p-4">
        <GroupAvatar name={group.name} className="size-16" memberCount={group.member_count} />
        {editing ? (
          <div className="w-full space-y-2">
            <div className="space-y-1">
              <Label htmlFor="edit-name" className="text-xs">Name</Label>
              <Input
                id="edit-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                maxLength={64}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="edit-desc" className="text-xs">Description</Label>
              <Input
                id="edit-desc"
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                maxLength={280}
              />
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={saveEdits} className="flex-1">Save</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
            {isOwner && (
              <div className="flex items-center justify-between rounded-lg border p-2.5">
                <div className="min-w-0 pr-2">
                  <Label htmlFor="edit-visibility" className="text-xs font-medium">
                    Public group
                  </Label>
                  <p className="text-[11px] text-muted-foreground">
                    Owner only — public groups are searchable and joinable.
                  </p>
                </div>
                <Switch
                  id="edit-visibility"
                  checked={editVisibility === "public"}
                  onCheckedChange={(checked) =>
                    setEditVisibility(checked ? "public" : "private")
                  }
                />
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="text-center">
              <p className="text-base font-semibold">{group.name}</p>
              <p className="text-xs text-muted-foreground">
                {group.member_count} member{group.member_count === 1 ? "" : "s"} · {group.visibility} · owner{" "}
                <span className="font-medium">@{group.owner_username}</span>
              </p>
              {group.description && (
                <p className="mt-1 text-sm text-muted-foreground">{group.description}</p>
              )}
            </div>
            {isAdmin && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setEditName(group.name);
                  setEditDescription(group.description ?? "");
                  setEditVisibility(group.visibility);
                  setEditing(true);
                }}
              >
                Edit details
              </Button>
            )}
          </>
        )}
      </div>

      <Separator />

      {isAdmin && (
        <div className="space-y-2 p-4">
          <Label className="text-xs text-muted-foreground">Invite a member</Label>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={inviteQuery}
              onChange={(e) => setInviteQuery(e.target.value)}
              placeholder="Search by username…"
              className="pl-8 h-8 text-sm"
            />
          </div>
          {inviteResults.length > 0 && (
            <div className="space-y-1 rounded-lg border p-1">
              {inviteResults.map((user) => (
                <div key={user.username} className="flex items-center gap-2 rounded-md p-1.5">
                  <UserAvatar name={user.display_name} username={user.username} className="size-6" />
                  <span className="min-w-0 flex-1 truncate text-xs">@{user.username}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={inviting !== null}
                    onClick={() => invite(user.username)}
                  >
                    {inviting === user.username ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <UserPlus className="size-3" />
                    )}
                    Invite
                  </Button>
                </div>
              ))}
            </div>
          )}
          {/* Invite search paging: beyond the first page of matches. */}
          {inviteResults.length > 0 && inviteTotal !== null && inviteLoaded < inviteTotal && (
            <div className="flex justify-center">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-[11px] text-muted-foreground"
                disabled={inviteSearchLoadingMore}
                onClick={() => void loadMoreInviteResults()}
              >
                {inviteSearchLoadingMore ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <ChevronDown className="size-3" />
                )}
                Load more ({inviteLoaded}/{inviteTotal})
              </Button>
            </div>
          )}
        </div>
      )}

      <Separator />

      {isAdmin && (
        <div className="space-y-2 px-4 pb-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Bell className="size-3.5" />
            Invitations
            {outgoingTotal !== null ? ` (${outgoingTotal})` : ""}
          </p>

          {/* Status filter: full invitation history, not just pending. */}
          <div className="flex flex-wrap gap-1">
            {(["pending", "accepted", "declined", "revoked"] as const).map(
              (status) => (
                <Button
                  key={status}
                  variant={outgoingStatus === status ? "secondary" : "ghost"}
                  size="sm"
                  className="h-6 px-2 text-[11px] capitalize"
                  onClick={() => setOutgoingStatus(status)}
                >
                  {status}
                </Button>
              )
            )}
          </div>

          {invitesLoading ? (
            <div className="flex justify-center py-3">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          ) : outgoingInvites.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {outgoingStatus === "pending"
                ? "No pending invitations — invite someone above."
                : `No ${outgoingStatus} invitations yet.`}
            </p>
          ) : (
            <div className="space-y-1 rounded-lg border p-1">
              {outgoingInvites.map((invitation) => (
                <div
                  key={invitation.id}
                  className="flex items-center gap-2 rounded-md p-1.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 truncate text-xs font-medium">
                      @{invitation.invitee_username}
                      {invitation.status !== "pending" && (
                        <Badge
                          variant="outline"
                          className="shrink-0 text-[10px] capitalize"
                        >
                          {invitation.status}
                        </Badge>
                      )}
                    </p>
                    <p className="truncate text-[11px] text-muted-foreground">
                      invited by @{invitation.inviter_username} ·{" "}
                      {formatListTimestamp(invitation.created_at)}
                      {invitation.responded_at &&
                        ` · responded ${formatListTimestamp(invitation.responded_at)}`}
                    </p>
                  </div>
                  {invitation.status === "pending" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 shrink-0 px-2 text-xs text-destructive hover:text-destructive"
                      disabled={revokingId !== null}
                      onClick={() => revokeInvitation(invitation)}
                      aria-label={`Revoke invitation for ${invitation.invitee_username}`}
                    >
                      {revokingId === invitation.id ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <X className="size-3" />
                      )}
                      Revoke
                    </Button>
                  )}
                </div>
              ))}

              {/* Offset paging across the invitation history. */}
              {outgoingTotal !== null && outgoingNextOffset < outgoingTotal && (
                <div className="flex justify-center pt-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 text-[11px] text-muted-foreground"
                    disabled={loadingMoreInvites}
                    onClick={() => void loadMoreOutgoing()}
                  >
                    {loadingMoreInvites ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <ChevronDown className="size-3" />
                    )}
                    Load more ({outgoingInvites.length}/{outgoingTotal})
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <ScrollArea className="chat-scroll max-h-[calc(100dvh-26rem)] min-h-40">
        <div className="space-y-0.5 p-2">
          <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
            Members{memberTotal !== null ? ` (${memberTotal})` : ""}
          </p>
          {loadingMembers ? (
            <div className="flex justify-center py-6">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          ) : (
            members.map((member) => (
              <div
                key={member.user_id}
                className="flex items-center gap-1 rounded-lg px-2 py-1.5 transition-colors"
              >
                <button
                  type="button"
                  onClick={() => openUserProfile(member.username)}
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 rounded-lg px-1.5 py-0.5 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Open profile of ${member.username}`}
                  title={`Open profile of @${member.username}`}
                >
                  <UserAvatar
                    name={member.display_name}
                    username={member.username}
                    className="size-8"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {displayNameOf(member.display_name, member.username)}
                      {member.username === me && (
                        <span className="ml-1 text-xs text-muted-foreground">(you)</span>
                      )}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      @{member.username}
                    </p>
                  </div>
                </button>
                <RoleBadge role={member.role} />
                {isOwner && member.username !== me && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 text-muted-foreground"
                        aria-label={`Manage ${member.username}`}
                      >
                        <Shield className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-52">
                      {member.role === "member" && (
                        <DropdownMenuItem onClick={() => changeRole(member, "admin")}>
                          <ShieldCheck className="size-4" /> Promote to admin
                        </DropdownMenuItem>
                      )}
                      {member.role === "admin" && (
                        <DropdownMenuItem onClick={() => changeRole(member, "member")}>
                          <Shield className="size-4" /> Demote to member
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem
                        onClick={() => setConfirmAction({ kind: "transfer", member })}
                      >
                        <Crown className="size-4" /> Transfer ownership…
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        variant="destructive"
                        onClick={() => setConfirmAction({ kind: "remove", member })}
                      >
                        <UserMinus className="size-4" /> Remove from group…
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                {isAdmin && !isOwner && member.role === "member" && member.username !== me && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground"
                    aria-label={`Remove ${member.username}`}
                    onClick={() => setConfirmAction({ kind: "remove", member })}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                )}
              </div>
            ))
          )}

          {/* Background auto-pagination indicator for large groups. */}
          {!loadingMembers &&
            loadingMoreMembers &&
            memberTotal !== null &&
            members.length < memberTotal && (
              <div className="flex items-center justify-center gap-1.5 py-2 text-[11px] text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                Loading members… ({members.length}/{memberTotal})
              </div>
            )}
        </div>
      </ScrollArea>

      <div className="mt-auto border-t p-4">
        <Button
          variant="outline"
          className={cn("w-full", isOwner && "opacity-60")}
          disabled={isOwner || leaving}
          onClick={() => setConfirmAction({ kind: "leave" })}
        >
          {leaving ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <LogOut className="size-4" />
          )}
          Leave group
        </Button>
        {isOwner && (
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Owners cannot leave — transfer ownership first.
          </p>
        )}
      </div>

      {/* Confirmation for leave / kick / transfer-ownership. */}
      <AlertDialog
        open={confirmAction !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmAction(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction?.kind === "leave" &&
                `Leave ${group?.name ?? "this group"}?`}
              {confirmAction?.kind === "remove" &&
                `Remove @${confirmAction.member.username}?`}
              {confirmAction?.kind === "transfer" &&
                `Transfer ownership to @${confirmAction.member.username}?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction?.kind === "leave" &&
                "You will stop receiving messages from this group. To rejoin, an owner or admin has to invite you again."}
              {confirmAction?.kind === "remove" &&
                "They will immediately lose access to this group and its messages. You can invite them again later."}
              {confirmAction?.kind === "transfer" &&
                "You will become an admin. Only the new owner can transfer ownership back — this cannot be undone from your side."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={confirmBusy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={confirmBusy}
              onClick={(event) => {
                event.preventDefault();
                void runConfirmAction();
              }}
              className={
                confirmAction?.kind === "transfer"
                  ? "bg-amber-600 text-white hover:bg-amber-700"
                  : "bg-destructive text-white hover:bg-destructive/90"
              }
            >
              {confirmBusy && <Loader2 className="mr-1 size-4 animate-spin" />}
              {confirmAction?.kind === "leave" && "Leave group"}
              {confirmAction?.kind === "remove" && "Remove member"}
              {confirmAction?.kind === "transfer" && "Transfer ownership"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PanelShell>
  );
}

/* ---------------------------------- Helpers --------------------------------- */

function PanelShell({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between border-b px-4">
        <h3 className="text-sm font-semibold">{title}</h3>
        {onClose && (
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="Close panel"
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        )}
      </div>
      {children}
    </div>
  );
}

function RoleBadge({ role }: { role: GroupRead["role"] }) {
  if (!role) return null;
  const variant =
    role === "owner" ? "default" : role === "admin" ? "secondary" : "outline";
  return (
    <Badge variant={variant} className="shrink-0 text-[10px] capitalize">
      {role}
    </Badge>
  );
}
