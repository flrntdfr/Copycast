import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { LogOut, UserRound } from "lucide-react";
import { useState } from "react";

import { fetchClient } from "@/api/client";
import { forgetMe, useMe } from "@/api/session";
import type { MeRead } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { clearSession } from "./lifecycle";
import { LOGIN_PATH } from "./next";

/**
 * Sign out: the server forgets the session, then this tab forgets the User (playback, the event
 * stream, their namespaced keys, every cached response) and shows the sign-in page.
 */
export function useSignOut(): { signOut: () => Promise<void>; pending: boolean } {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  const signOut = async () => {
    setPending(true);
    try {
      await fetchClient.POST("/auth/logout");
    } catch {
      // The session may be gone already; this tab still signs out.
    }
    // The sign-in page asks `/api/me` again; then the shell is gone and nothing refetches.
    forgetMe(queryClient);
    await navigate({ to: LOGIN_PATH, replace: true });
    clearSession(queryClient);
    setPending(false);
  };
  return { signOut, pending };
}

/** A signed-in User; nothing in open mode, where nobody signs in. */
function signedInUser(me: MeRead | undefined): MeRead | null {
  return me && me.auth_mode !== "open" ? me : null;
}

function AdminBadge() {
  return (
    <Badge variant="secondary" className="font-normal" data-testid="admin-badge">
      Admin
    </Badge>
  );
}

/** The header's account button: who is signed in, an Admin badge, and Sign out. */
export function UserMenu({ className }: { className?: string }) {
  const user = signedInUser(useMe());
  const { signOut, pending } = useSignOut();
  if (!user) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={className}
          aria-label={`Account: ${user.username}`}
        >
          <UserRound />
          <span className="max-w-32 truncate">{user.username}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        <DropdownMenuLabel className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate">{user.username}</span>
          {user.admin ? <AdminBadge /> : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={pending} onSelect={() => void signOut()}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The same in the phone navigation Sheet: the User, then a Sign out button. */
export function UserSheetSection({ onSignOut }: { onSignOut?: () => void }) {
  const user = signedInUser(useMe());
  const { signOut, pending } = useSignOut();
  if (!user) return null;
  return (
    <div className="mt-auto space-y-2 border-t px-4 py-4" data-testid="user-sheet">
      <p className="flex items-center gap-2 text-sm font-medium">
        <UserRound className="size-4 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{user.username}</span>
        {user.admin ? <AdminBadge /> : null}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="w-full"
        disabled={pending}
        onClick={() => {
          onSignOut?.();
          void signOut();
        }}
      >
        <LogOut /> Sign out
      </Button>
    </div>
  );
}
