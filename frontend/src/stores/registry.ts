/**
 * Cross-store reset registry.
 *
 * Each store module registers its own reset function here, so the auth store
 * can clear the whole application on logout WITHOUT importing those modules
 * (which would create import cycles).
 */
type ResetFn = () => void;

const registry = new Map<string, ResetFn>();

export function registerStoreReset(key: string, reset: ResetFn): void {
  registry.set(key, reset);
}

export function resetApplicationStores(): void {
  for (const reset of registry.values()) {
    try {
      reset();
    } catch {
      // A failing reset must never block the logout flow.
    }
  }
}
