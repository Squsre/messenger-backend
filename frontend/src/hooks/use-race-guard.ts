"use client";

import { useCallback, useRef } from "react";

/**
 * Race guard for debounced search inputs (users/search, groups/search, the
 * group-panel invite search): every issued request receives a sequence
 * number and only the MOST RECENTLY issued one may commit its result.
 *
 * Without it a slow response for an old query can resolve after a newer
 * query and overwrite the fresh results ("stale response wins"). Usage:
 *
 *   const { issue, isCurrent } = useRaceGuard();
 *   const ticket = issue();                 // when firing the request
 *   const data = await fetch(...);
 *   if (!isCurrent(ticket)) return;         // a newer request exists — drop
 *   setResults(data.items);
 *
 * Calling `issue()` without a request also works as an invalidation: any
 * in-flight response becomes stale immediately (e.g. the query was cleared).
 */
export function useRaceGuard() {
  const seqRef = useRef(0);

  /** Allocate the next ticket; call once per request (or to invalidate). */
  const issue = useCallback(() => {
    seqRef.current += 1;
    return seqRef.current;
  }, []);

  /** True when `ticket` still belongs to the newest issued request. */
  const isCurrent = useCallback(
    (ticket: number) => seqRef.current === ticket,
    []
  );

  return { issue, isCurrent };
}
