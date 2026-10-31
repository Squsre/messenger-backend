"use client";

/**
 * Contacts page (GET/POST /contacts, DELETE /contacts/{username}):
 * searchable address book with add / remove, and one-click jump into the
 * direct chat with a contact.
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  ArrowLeft,
  ChevronDown,
  Loader2,
  MessageSquare,
  Search,
  UserPlus,
  UserRoundX,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
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
import { UserAvatar } from "@/components/common/user-avatar";
import { EmptyState } from "@/components/common/empty-state";
import { useContactsStore } from "@/stores/use-contacts-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { ApiError } from "@/lib/api-client";
import { USERNAME_REGEX } from "@/lib/constants";
import { displayNameOf } from "@/lib/utils";
import type { UserPublic } from "@/types/api";

export default function ContactsPage() {
  const router = useRouter();
  const me = useAuthStore((s) => s.user?.username ?? "");
  const contacts = useContactsStore((s) => s.contacts);
  const loading = useContactsStore((s) => s.loading);
  const loaded = useContactsStore((s) => s.loaded);
  const total = useContactsStore((s) => s.total);
  const nextOffset = useContactsStore((s) => s.nextOffset);
  const loadingMore = useContactsStore((s) => s.loadingMore);
  const busyUsername = useContactsStore((s) => s.busyUsername);

  const [query, setQuery] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [adding, setAdding] = useState(false);
  const [messaging, setMessaging] = useState<string | null>(null);
  /** Contact pending removal — shown by the confirmation dialog. */
  const [confirmRemove, setConfirmRemove] = useState<UserPublic | null>(null);

  useEffect(() => {
    void useContactsStore.getState().load(true);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return contacts;
    return contacts.filter(
      (c) =>
        c.username.includes(q) ||
        (c.display_name ?? "").toLowerCase().includes(q)
    );
  }, [contacts, query]);

  async function addContact() {
    const username = newUsername.trim().toLowerCase();
    if (!username) return;
    if (!USERNAME_REGEX.test(username)) {
      toast.error("Usernames are 3–32 lowercase letters, digits or underscores.");
      return;
    }
    if (username === me) {
      toast.error("You cannot add yourself as a contact.");
      return;
    }
    setAdding(true);
    try {
      const user = await useContactsStore.getState().add(username);
      setNewUsername("");
      toast.success(`@${user.username} added to contacts.`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not add the contact."
      );
    } finally {
      setAdding(false);
    }
  }

  async function removeContact(contact: UserPublic) {
    try {
      await useContactsStore.getState().remove(contact.username);
      toast.info(`@${contact.username} removed from contacts.`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not remove the contact."
      );
    } finally {
      setConfirmRemove(null);
    }
  }

  async function openChat(username: string) {
    setMessaging(username);
    try {
      const detail = await useConversationStore.getState().openDirectChat(username);
      router.push(`/c/${detail.id}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not open the chat."
      );
      setMessaging(null);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-4">
        <Button asChild variant="ghost" size="icon" className="md:hidden" aria-label="Back">
          <Link href="/">
            <ArrowLeft className="size-5" />
          </Link>
        </Button>
        <div>
          <h1 className="text-base font-semibold leading-tight">Contacts</h1>
          <p className="text-xs text-muted-foreground">
            {total !== null && total !== contacts.length
              ? `${contacts.length} of ${total} contacts loaded`
              : `${contacts.length} contact${contacts.length === 1 ? "" : "s"}`} · people you added
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        {/* Add by username */}
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void addContact();
          }}
        >
          <Input
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            placeholder="Add by username (e.g. bob)"
            aria-label="Username to add"
            maxLength={32}
            className="h-9"
          />
          <Button type="submit" className="h-9 shrink-0" disabled={adding} aria-label="Add contact">
            {adding ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <UserPlus className="size-4" />
            )}
            Add
          </Button>
        </form>

        {/* Filter */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search contacts"
            aria-label="Search contacts"
            className="pl-8 h-9"
          />
        </div>

        <ScrollArea className="chat-scroll min-h-0 flex-1">
          <div className="space-y-1 pr-2">
            {loading && !loaded
              ? Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-3 rounded-lg border p-3">
                    <Skeleton className="size-10 rounded-full" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3.5 w-1/3" />
                      <Skeleton className="h-3 w-1/4" />
                    </div>
                  </div>
                ))
              : filtered.length === 0
                ? contacts.length === 0 ? (
                    <EmptyState
                      icon={Users}
                      title="No contacts yet"
                      description="Add people by their username above, or from any profile panel."
                      className="py-16"
                    />
                  ) : (
                    <EmptyState
                      icon={UserRoundX}
                      title="No contacts match"
                      description="Try a different name."
                      className="py-16"
                    />
                  )
                : filtered.map((contact) => (
                    <div
                      key={contact.username}
                      className="flex items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-accent"
                    >
                      <button
                        type="button"
                        onClick={() => void openChat(contact.username)}
                        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-lg text-left"
                        aria-label={`Open chat with ${contact.username}`}
                      >
                        <UserAvatar
                          name={contact.display_name}
                          username={contact.username}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {displayNameOf(contact.display_name, contact.username)}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            @{contact.username}
                            {contact.bio ? ` · ${contact.bio}` : ""}
                          </p>
                        </div>
                      </button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0 text-muted-foreground"
                        aria-label={`Open chat with ${contact.username}`}
                        disabled={messaging !== null}
                        onClick={() => void openChat(contact.username)}
                      >
                        {messaging === contact.username ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <MessageSquare className="size-4" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0 text-destructive hover:text-destructive"
                        aria-label={`Remove ${contact.username} from contacts`}
                        disabled={busyUsername !== null}
                        onClick={() => setConfirmRemove(contact)}
                      >
                        {busyUsername === contact.username ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <UserRoundX className="size-4" />
                        )}
                      </Button>
                    </div>
                  ))}

            {/* Address-book paging: beyond the first 100 contacts. */}
            {!loading && total !== null && nextOffset < total && (
              <div className="flex justify-center pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1.5 text-xs text-muted-foreground"
                  disabled={loadingMore}
                  onClick={() =>
                    void useContactsStore.getState().loadMore()
                  }
                >
                  {loadingMore ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <ChevronDown className="size-3.5" />
                  )}
                  Load more contacts ({contacts.length}/{total})
                </Button>
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Removing a contact is destructive-ish — confirm before the DELETE. */}
      <AlertDialog
        open={confirmRemove !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmRemove(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Remove @{confirmRemove?.username} from contacts?
            </AlertDialogTitle>
            <AlertDialogDescription>
              They will no longer appear in your contact list. Your chat
              history is kept and you can add them again at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busyUsername !== null}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={busyUsername !== null}
              onClick={(event) => {
                event.preventDefault();
                if (confirmRemove) void removeContact(confirmRemove);
              }}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {busyUsername && <Loader2 className="mr-1 size-4 animate-spin" />}
              Remove contact
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
