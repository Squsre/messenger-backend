"use client";

/**
 * Settings → About: backend service banner (GET /), liveness (GET
 * /api/v1/health) and readiness with per-dependency checks (GET
 * /api/v1/health/ready). All three are anonymous calls so the panel still
 * reports the truth when the token is expired. The banner doubles as a
 * sanity check that NEXT_PUBLIC_API_BASE_URL really points at a Messenger
 * backend.
 */
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDrive,
  Loader2,
  RefreshCw,
  Server,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { apiRequestRaw } from "@/lib/api-client";
import { APP_VERSION } from "@/lib/constants";
import type { HealthResponse, ReadinessResponse, RootBanner } from "@/types/api";

type Phase = "loading" | "ok" | "down";

const MOCK_API_PORT = process.env.NEXT_PUBLIC_MOCK_API_PORT ?? "3030";

function apiBaseLabel(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  return base ? base : `Sandbox gateway (relative /api/v1 via :${MOCK_API_PORT})`;
}

/** Readiness chip per dependency (database / redis / storage …). */
function CheckChip({ name, value }: { name: string; value: string }) {
  const ok = value === "ok";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
        ok ? "text-muted-foreground" : "border-destructive/40 text-destructive"
      }`}
    >
      {name === "database" ? (
        <Database className="size-3.5" />
      ) : name === "storage" ? (
        <HardDrive className="size-3.5" />
      ) : (
        <Server className="size-3.5" />
      )}
      {name}: {value}
    </span>
  );
}

export function AboutPanel() {
  const [banner, setBanner] = useState<RootBanner | null>(null);
  const [bannerPhase, setBannerPhase] = useState<Phase>("loading");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthPhase, setHealthPhase] = useState<Phase>("loading");
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [readinessPhase, setReadinessPhase] = useState<Phase>("loading");
  const [checkedAt, setCheckedAt] = useState<string | null>(null);

  /** Pure fetch pass — no state writes, so effects may call it freely. */
  const fetchAll = useCallback(async () => {
    const result: {
      banner: RootBanner | null;
      health: HealthResponse | null;
      readiness: ReadinessResponse | null;
    } = { banner: null, health: null, readiness: null };

    // GET / — the service banner. Anonymous; an unexpected body means the
    // configured base URL does not point at a Messenger API.
    try {
      result.banner = await fetchRootBanner();
    } catch {
      result.banner = null;
    }

    // GET /api/v1/health — liveness.
    try {
      result.health = await fetchAnonymousJson<HealthResponse>("/api/v1/health");
    } catch {
      result.health = null;
    }

    // GET /api/v1/health/ready — 200 ok OR 503 degraded, both carry checks.
    try {
      result.readiness = await fetchAnonymousJson<ReadinessResponse>(
        "/api/v1/health/ready"
      );
    } catch {
      result.readiness = null;
    }

    return result;
  }, []);

  // Initial check: the phase states already start at "loading", so the
  // effect only applies results from async callbacks.
  useEffect(() => {
    let cancelled = false;
    void fetchAll().then((result) => {
      if (cancelled) return;
      setBanner(result.banner);
      setBannerPhase(result.banner ? "ok" : "down");
      setHealth(result.health);
      setHealthPhase(result.health ? "ok" : "down");
      setReadiness(result.readiness);
      setReadinessPhase(result.readiness ? "ok" : "down");
      setCheckedAt(new Date().toLocaleTimeString());
    });
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  /** Manual refresh from the button (event handler — sync resets are fine). */
  function refresh() {
    setBannerPhase("loading");
    setHealthPhase("loading");
    setReadinessPhase("loading");
    void fetchAll().then((result) => {
      setBanner(result.banner);
      setBannerPhase(result.banner ? "ok" : "down");
      setHealth(result.health);
      setHealthPhase(result.health ? "ok" : "down");
      setReadiness(result.readiness);
      setReadinessPhase(result.readiness ? "ok" : "down");
      setCheckedAt(new Date().toLocaleTimeString());
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Backend status</p>
          <p className="text-xs text-muted-foreground">
            {checkedAt ? `Checked at ${checkedAt}` : "Checking…"}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCw className="size-4" /> Refresh
        </Button>
      </div>

      {/* API base URL sanity + service banner (GET /) */}
      <section className="space-y-2 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-sm font-medium">
            <Server className="size-4" /> Service
          </p>
          {bannerPhase === "loading" ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : bannerPhase === "ok" ? (
            <Badge variant="secondary">
              <CheckCircle2 className="size-3" /> reachable
            </Badge>
          ) : (
            <Badge variant="destructive">
              <XCircle className="size-3" /> unreachable
            </Badge>
          )}
        </div>
        <p className="break-all text-xs text-muted-foreground">
          API base: {apiBaseLabel()}
        </p>
        {banner ? (
          <div className="space-y-1 text-sm">
            <p>
              <span className="text-muted-foreground">Name: </span>
              {banner.name}
            </p>
            <p>
              <span className="text-muted-foreground">Version: </span>
              {banner.version}
            </p>
            <p className="text-xs text-muted-foreground">
              Docs: {banner.docs_url} · Health: {banner.health_url}
            </p>
          </div>
        ) : bannerPhase === "down" ? (
          <p className="text-xs text-destructive">
            The root banner did not answer — the app cannot confirm the configured
            base URL is a Messenger API.
          </p>
        ) : null}
      </section>

      {/* Liveness (GET /api/v1/health) */}
      <section className="space-y-2 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-sm font-medium">
            <span
              className={`inline-block size-2 rounded-full ${
                healthPhase === "ok"
                  ? "bg-emerald-500"
                  : healthPhase === "down"
                    ? "bg-destructive"
                    : "bg-muted-foreground/50 animate-pulse"
              }`}
              aria-hidden
            />
            API liveness
          </p>
          <span className="text-xs text-muted-foreground">GET /api/v1/health</span>
        </div>
        {healthPhase === "loading" && (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        )}
        {healthPhase === "ok" && health && (
          <div className="space-y-1 text-sm">
            <p>
              {health.service} v{health.version} · environment{" "}
              <span className="font-medium">{health.environment}</span>
            </p>
            <p className="text-xs text-muted-foreground">
              Status “{health.status}” — the API process is up.
            </p>
          </div>
        )}
        {healthPhase === "down" && (
          <p className="text-xs text-destructive">
            The API did not answer the liveness check. Separate a backend outage
            from your own connection when history or realtime calls fail.
          </p>
        )}
      </section>

      {/* Readiness (GET /api/v1/health/ready) */}
      <section className="space-y-2 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-sm font-medium">
            <AlertTriangle
              className={`size-4 ${
                readinessPhase === "ok" && readiness?.status === "degraded"
                  ? "text-amber-500"
                  : "text-muted-foreground"
              }`}
            />
            Readiness
          </p>
          <span className="text-xs text-muted-foreground">
            GET /api/v1/health/ready
          </span>
        </div>
        {readinessPhase === "loading" && (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        )}
        {readinessPhase === "ok" && readiness && (
          <>
            <div className="flex flex-wrap gap-2">
              {Object.entries(readiness.checks).map(([name, value]) => (
                <CheckChip key={name} name={name} value={value} />
              ))}
            </div>
            {readiness.status === "degraded" ? (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                A dependency is degraded — media downloads, history and realtime
                features can fail until it recovers.
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                All dependencies (database, cache, storage) are healthy.
              </p>
            )}
          </>
        )}
        {readinessPhase === "down" && (
          <p className="text-xs text-destructive">
            Readiness did not answer at all.
          </p>
        )}
      </section>

      <Separator />
      <p className="text-xs text-muted-foreground">
        Messenger Web · frontend v{APP_VERSION}
      </p>
    </div>
  );
}

/* ------------------------- Anonymous raw fetches ------------------------- */

/**
 * Health/banner calls must skip the Bearer token, and readiness answers 503
 * with a full checks body when degraded — apiRequest would throw on !ok and
 * lose the dependency names, so the raw variant is used and only network
 * failures / timeouts count as "unreachable".
 */
async function fetchAnonymousJson<T>(path: string): Promise<T> {
  const raw = await apiRequestRaw(path, { anonymous: true, method: "GET" });
  return raw.data as T;
}

async function fetchRootBanner(): Promise<RootBanner> {
  const banner = await fetchAnonymousJson<RootBanner>("/");
  if (
    !banner ||
    typeof banner !== "object" ||
    typeof banner.name !== "string" ||
    typeof banner.version !== "string"
  ) {
    throw new Error("Not a Messenger API banner");
  }
  return banner;
}
