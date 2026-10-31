"use client";

/**
 * Global command palette (Ctrl/Cmd+K): keyboard-driven search and
 * navigation across
 * - existing direct chats and joined groups (local, instant),
 * - contacts (opens the profile panel),
 * - public groups (debounced, race-guarded GET /groups/search),
 * - common actions (new chat / group, discover, invitations, settings,
 *   theme toggle).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BookUser,
  Bell,
  Compass,
  Hash,
  Loader2,
  MessagesSquare,
  Moon,
  Search,
  Settings,
  Sun,
  UserPlus,
  UsersRound,
} from "lucide-react";
import { useTheme } from "next-themes";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useContactsStore } from "@/stores/use-contacts-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { useRaceGuard } from "@/hooks/use-race-guard";
import { apiClient } from "@/lib/api-client";
import { SEARCH_MIN_QUERY_LENGTH, SEARCH_PAGE_LIMIT } from "@/lib/constants";
import { displayNameOf } from "@/lib/utils";
import type { GroupPage, GroupRead } from "@/types/api";

const PUBLIC_GROUP_DEBOUNCE_MS = 300;

export function CommandPaletteDialog() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);

  const conversations = useConversationStore((s) => s.conversations);
  const groups = useConversationStore((s) => s.groups);
  const contacts = useContactsStore((s) => s.contacts);
  const myUsername = useAuthStore((s) => s.user?.username ?? "");

  // Public group search (API-backed section of the palette).
  const [query, setQuery] = useState("");
  const [publicGroups, setPublicGroups] = useState<GroupRead[]>([]);
  const [searching, setSearching] = useState(false);
  const { issue, isCurrent } = useRaceGuard();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Global Ctrl/Cmd+K shortcut.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        useUiStore.getState().toggleCommandPalette();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Warm the contact list + reset the search when the palette opens.
  useEffect(() => {
    if (!open) return;
    void useContactsStore.getState().load();
    setQuery("");
    setPublicGroups([]);
    setSearching(false);
  }, [open]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const searchPublicGroups = useCallback(
    (term: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (term.trim().length < SEARCH_MIN_QUERY_LENGTH) {
        issue(); // invalidate any in-flight request
        setPublicGroups([]);
        setSearching(false);
        return;
      }
      setSearching(true);
      debounceRef.current = setTimeout(async () => {
        const ticket = issue();
        try {
          const page = await apiClient.get<GroupPage>("/api/v1/groups/search", {
            query: { q: term.trim(), limit: SEARCH_PAGE_LIMIT },
          });
          if (!isCurrent(ticket)) return;
          setPublicGroups(page.items);
        } catch {
          if (isCurrent(ticket)) setPublicGroups([]);
        } finally {
          if (isCurrent(ticket)) setSearching(false);
        }
      }, PUBLIC_GROUP_DEBOUNCE_MS);
    },
    [issue, isCurrent]
  );

  const close = () => setOpen(false);
  const go = (path: string) => {
    close();
    router.push(path);
  };

  const joinedGroupIds = new Set(groups.map((g) => g.id));

  return (
    <CommandDialog
      title="Command palette"
      description="Search chats, contacts, groups and actions"
      open={open}
      onOpenChange={setOpen}
      className="sm:max-w-xl"
    >
      <CommandInput
        value={query}
        onValueChange={(next) => {
          setQuery(next);
          searchPublicGroups(next);
        }}
        placeholder="Type a name, group or command…"
      />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        {conversations.length > 0 && (
          <CommandGroup heading="Chats">
            {conversations.slice(0, 8).map((conversation) => (
              <CommandItem
                key={`c-${conversation.id}`}
                value={`chat ${conversation.participant.username} ${displayNameOf(
                  conversation.participant.display_name,
                  conversation.participant.username
                )}`}
                onSelect={() => go(`/c/${conversation.id}`)}
              >
                <MessagesSquare className="text-muted-foreground" />
                <span dir="auto" className="truncate">
                  {displayNameOf(
                    conversation.participant.display_name,
                    conversation.participant.username
                  )}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  @{conversation.participant.username}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {groups.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Groups">
              {groups.slice(0, 8).map((group) => (
                <CommandItem
                  key={`g-${group.id}`}
                  value={`group ${group.name}`}
                  onSelect={() => go(`/g/${group.id}`)}
                >
                  <UsersRound className="text-muted-foreground" />
                  <span dir="auto" className="truncate">
                    {group.name}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {group.member_count} members
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        {contacts.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Contacts">
              {contacts.slice(0, 6).map((contact) => (
                <CommandItem
                  key={`u-${contact.username}`}
                  value={`contact ${contact.username} ${displayNameOf(
                    contact.display_name,
                    contact.username
                  )}`}
                  onSelect={() => {
                    close();
                    useUiStore.getState().openUserProfile(contact.username);
                  }}
                >
                  <BookUser className="text-muted-foreground" />
                  <span dir="auto" className="truncate">
                    {displayNameOf(contact.display_name, contact.username)}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    @{contact.username}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        {query.trim().length >= SEARCH_MIN_QUERY_LENGTH && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Public groups">
              {searching ? (
                <div className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Searching public groups…
                </div>
              ) : publicGroups.length === 0 ? (
                <div className="px-2 py-3 text-sm text-muted-foreground">
                  No public groups match “{query.trim()}”.
                </div>
              ) : (
                publicGroups.map((group) => (
                  <CommandItem
                    key={`p-${group.id}`}
                    value={`public group ${group.name}`}
                    onSelect={() => go(`/g/${group.id}`)}
                  >
                    <Hash
                      className={
                        joinedGroupIds.has(group.id)
                          ? "text-primary"
                          : "text-muted-foreground"
                      }
                    />
                    <span dir="auto" className="truncate">
                      {group.name}
                    </span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {joinedGroupIds.has(group.id)
                        ? "Joined — open"
                        : `${group.member_count} members · public`}
                    </span>
                  </CommandItem>
                ))
              )}
            </CommandGroup>
          </>
        )}

        <CommandSeparator />
        <CommandGroup heading="Actions">
          <CommandItem
            value="new chat direct message"
            onSelect={() => {
              close();
              useUiStore.getState().setNewChatOpen(true);
            }}
          >
            <UserPlus className="text-muted-foreground" /> New chat
          </CommandItem>
          <CommandItem
            value="new group create"
            onSelect={() => {
              close();
              useUiStore.getState().setCreateGroupOpen(true);
            }}
          >
            <UsersRound className="text-muted-foreground" /> New group
          </CommandItem>
          <CommandItem
            value="discover public groups join"
            onSelect={() => {
              close();
              useUiStore.getState().setDiscoverGroupsOpen(true);
            }}
          >
            <Compass className="text-muted-foreground" /> Discover public groups
          </CommandItem>
          <CommandItem
            value="invitations pending group invites"
            onSelect={() => {
              close();
              useUiStore.getState().setInvitationsOpen(true);
            }}
          >
            <Bell className="text-muted-foreground" /> Group invitations
          </CommandItem>
          <CommandItem
            value="contacts address book"
            onSelect={() => go("/contacts")}
          >
            <BookUser className="text-muted-foreground" /> Contacts
          </CommandItem>
          <CommandItem
            value="settings preferences account"
            onSelect={() => go("/settings")}
          >
            <Settings className="text-muted-foreground" /> Settings
          </CommandItem>
          <CommandItem
            value="toggle theme dark light appearance"
            onSelect={() => {
              setTheme(theme === "dark" ? "light" : "dark");
              close();
            }}
          >
            {theme === "dark" ? (
              <Sun className="text-muted-foreground" />
            ) : (
              <Moon className="text-muted-foreground" />
            )}
            Switch to {theme === "dark" ? "light" : "dark"} mode
          </CommandItem>
        </CommandGroup>

        {myUsername && (
          <div className="border-t px-3 py-2 text-[11px] text-muted-foreground">
            Signed in as <span className="font-medium">@{myUsername}</span> ·
            Enter to open · Esc to close
          </div>
        )}
      </CommandList>
    </CommandDialog>
  );
}
