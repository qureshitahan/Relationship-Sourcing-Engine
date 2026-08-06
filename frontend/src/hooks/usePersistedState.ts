import { useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

/**
 * Like useState, but backed by sessionStorage under `key`. React Router fully
 * unmounts the previous route's component tree on navigation, which wipes
 * plain useState — this survives that (and a page refresh), scoped to the
 * browser tab. Call the returned `clear()` after a successful submit so the
 * next fresh visit doesn't reload a stale draft.
 */
export function usePersistedState<T>(
  key: string,
  initialValue: T
): [T, Dispatch<SetStateAction<T>>, () => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(key);
      if (stored != null) return JSON.parse(stored) as T;
    } catch {
      // Corrupt JSON or storage unavailable (private mode) — fall back below.
    }
    return initialValue;
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage unavailable/quota exceeded — in-memory state still works,
      // just without persistence.
    }
  }, [key, value]);

  const clear = () => {
    try {
      sessionStorage.removeItem(key);
    } catch {
      // ignore
    }
  };

  return [value, setValue, clear];
}
