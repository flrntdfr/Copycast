import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { AlertTriangle, Loader2, LogIn } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { $api, ApiProblem, describeProblem } from "@/api/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { safeNext } from "./next";

/** What a failed sign-in says; one wording for every wrong credential (no oracle). */
export function loginErrorText(error: unknown): string {
  if (!ApiProblem.is(error)) return describeProblem(error).title;
  switch (error.slug) {
    case "login-failed":
    case "unauthorized":
      return "Wrong username, password or code";
    case "login-locked": {
      const seconds = error.extensions.retry_after;
      return typeof seconds === "number"
        ? `Too many attempts, try again in ${seconds} s`
        : "Too many attempts, try again later";
    }
    case "login-busy":
      return "Busy, try again in a few seconds";
    case "https-required":
      return error.detail
        ? `${error.detail.charAt(0).toUpperCase()}${error.detail.slice(1)}`
        : "Sign in over https";
    case "validation":
      return "Enter your username and password";
    case "network":
      return "Cannot reach Copycast";
    default:
      return describeProblem(error).title;
  }
}

/**
 * Username, password and, for accounts that have one, the 6-digit code of an authenticator app.
 * A success drops every cached response (they belonged to nobody, or to someone else) and goes
 * on to `next`.
 */
export function LoginPage({ next }: { next?: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const ids = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const goOn = () => {
    queryClient.clear();
    void navigate({ href: safeNext(next), replace: true });
  };
  const login = $api.useMutation("post", "/auth/login", {
    meta: { silent: true },
    onSuccess: goOn,
    onError: (problem) => {
      // Authentication is off: there is nothing to sign in to.
      if (ApiProblem.is(problem) && problem.slug === "not-found") {
        goOn();
        return;
      }
      setError(loginErrorText(problem));
    },
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const totp = code.replace(/\s+/g, "");
    login.mutate({ body: { username: username.trim(), password, totp: totp || null } });
  };

  return (
    <main
      id="main"
      className="flex min-h-dvh items-center justify-center bg-muted/30 px-4 py-10"
      tabIndex={-1}
    >
      <div className="w-full max-w-sm space-y-6 rounded-xl border bg-background p-6 shadow-sm">
        <div className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <span
            className="inline-flex size-6 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground"
            aria-hidden
          >
            C
          </span>
          Copycast
        </div>
        <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-username`}>Username</Label>
            <Input
              id={`${ids}-username`}
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-password`}>Password</Label>
            <Input
              id={`${ids}-password`}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-code`}>Code</Label>
            <Input
              id={`${ids}-code`}
              name="totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9 ]{6,8}"
              maxLength={8}
              placeholder="123 456"
              aria-describedby={`${ids}-code-hint`}
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
            <p id={`${ids}-code-hint`} className="text-xs text-muted-foreground">
              The 6 digits of your authenticator app, if your account uses one.
            </p>
          </div>
          {error ? (
            <Alert variant="destructive" data-testid="login-error">
              <AlertTriangle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? <Loader2 className="animate-spin" /> : <LogIn />} Sign in
          </Button>
        </form>
      </div>
    </main>
  );
}
