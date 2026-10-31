"use client";

/**
 * Emoji popover for the composer: a curated, dependency-free grid grouped
 * by category. Clicking an emoji calls `onPick` and closes the popover so
 * the user can keep typing immediately.
 */
import { useEffect, useRef, useState } from "react";
import { Smile } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const CATEGORIES: { label: string; emojis: string[] }[] = [
  {
    label: "Smileys & people",
    emojis: [
      "😀", "😃", "😄", "😁", "😆", "😅", "😂", "🤣", "🥲", "😊",
      "😇", "🙂", "🙃", "😉", "😌", "😍", "🥰", "😘", "😗", "😙",
      "😚", "😋", "😛", "😝", "😜", "🤪", "🤨", "🧐", "🤓", "😎",
      "🥸", "🤩", "🥳", "😏", "😒", "😞", "😔", "😟", "😕", "🙁",
      "😣", "😖", "😫", "😩", "🥺", "😢", "😭", "😤", "😠", "😡",
      "🤬", "🤯", "😳", "🥵", "🥶", "😱", "😨", "😰", "😥", "😓",
      "🤗", "🤔", "🤭", "🤫", "🤥", "😶", "😐", "😑", "😬", "🙄",
      "😯", "😦", "😧", "😮", "😲", "🥱", "😴", "🤤", "😪", "😵",
      "🤐", "🥴", "🤢", "🤮", "🤧", "😷", "🤒", "🤕", "🤑", "🤠",
      "👍", "👎", "👌", "🤌", "🤏", "✌️", "🤞", "🤟", "🤘", "🤙",
      "👈", "👉", "👆", "👇", "☝️", "👋", "🤚", "🖐️", "✋", "🖖",
      "👏", "🙌", "🤝", "🙏", "💪", "🫡", "🤌", "🫶", "👀", "🧠",
    ],
  },
  {
    label: "Hearts & symbols",
    emojis: [
      "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💔",
      "❣️", "💕", "💞", "💓", "💗", "💖", "💘", "💝", "💟", "✨",
      "🔥", "💥", "💯", "⭐", "🌟", "💫", "⚡", "✔️", "❗", "❓",
    ],
  },
  {
    label: "Objects & nature",
    emojis: [
      "🎉", "🎊", "🎁", "🎈", "🎂", "🍰", "☕", "🍵", "🍕", "🍔",
      "🍟", "🌮", "🍎", "🍉", "🍇", "🍓", "⚽", "🏀", "🎾", "🎮",
      "🎧", "🎵", "🎶", "📱", "💻", "📷", "🚗", "✈️", "🚀", "🌙",
      "🌞", "☔", "❄️", "🌈", "🌸", "🍀", "🐱", "🐶", "🦋", "🐝",
    ],
  },
];

export function EmojiPicker({
  onPick,
  disabled,
}: {
  onPick: (emoji: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);

  // Reset the scroll position each time the popover opens.
  useEffect(() => {
    if (open) gridRef.current?.scrollTo({ top: 0 });
  }, [open]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-9 shrink-0 text-muted-foreground"
          aria-label="Insert emoji"
          title="Emoji"
          disabled={disabled}
        >
          <Smile className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        sideOffset={10}
        className="w-72 p-0"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <div className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">
          Emoji
        </div>
        <div
          ref={gridRef}
          className="chat-scroll max-h-56 overflow-y-auto p-2"
          role="listbox"
          aria-label="Emoji picker"
        >
          {CATEGORIES.map((category) => (
            <div key={category.label} className="mb-1">
              <p className="px-1 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {category.label}
              </p>
              <div className="grid grid-cols-8 gap-0.5">
                {category.emojis.map((emoji, index) => (
                  <button
                    key={`${emoji}-${index}`}
                    type="button"
                    className={cn(
                      "flex size-8 items-center justify-center rounded-md text-lg leading-none",
                      "transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                    )}
                    aria-label={`Insert ${emoji}`}
                    onClick={() => {
                      onPick(emoji);
                      setOpen(false);
                    }}
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
