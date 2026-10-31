import Link from "next/link";
import { MessagesSquare } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh flex flex-col bg-muted/30 dark:bg-background">
      <header className="h-14 flex items-center px-6 border-b bg-background/60 backdrop-blur">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <MessagesSquare className="size-4" />
          </span>
          <span className="font-brand text-base tracking-wide">Messenger</span>
        </Link>
      </header>
      <main className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">{children}</div>
      </main>
      <footer className="py-4 text-center text-xs text-muted-foreground">
        End-to-end encrypted messaging is on the roadmap.
      </footer>
    </div>
  );
}
