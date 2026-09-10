import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

/**
 * Like usePersistedState, but the stored value belongs to ONE LinkedIn account.
 *
 * Switching accounts on a page that shares one set of boxes is confusing in a
 * specific way: the filters, message and counts on screen belong to whoever was
 * selected a moment ago, and the leads underneath belong to whoever is selected
 * now — so the page reads as broken rather than as switched. Keying the state by
 * account means each one keeps its own work, and coming back to it finds it
 * where you left it.
 *
 * Scoped to the browser tab (sessionStorage) for the same reason the account pin
 * is: several tabs, one per account, is a normal way to work here.
 */
export function useAccountScopedState<T>(
  suffix: string,
  accountId: string | null | undefined,
  initialValue: T
): [T, Dispatch<SetStateAction<T>>] {
  // Before an account is known the state still has to live somewhere; it lands
  // under a placeholder rather than leaking into the first real account's slot.
  const key = `search:${accountId || "_pending"}:${suffix}`;

  // Held in a ref so an object or array default does not re-key on every render.
  const fallback = useRef(initialValue);

  const read = (storageKey: string): T => {
    try {
      const stored = sessionStorage.getItem(storageKey);
      if (stored != null) return JSON.parse(stored) as T;
    } catch {
      // Corrupt JSON or storage unavailable (private mode) — use the default.
    }
    return fallback.current;
  };

  const [value, setValue] = useState<T>(() => read(key));
  const lastKey = useRef(key);
  // Set while a switch is in flight, so the write effect below does not save the
  // OUTGOING account's value under the INCOMING account's key — that one-render
  // overlap would quietly copy one account's message onto another.
  const switching = useRef(false);

  useEffect(() => {
    if (lastKey.current === key) return;
    lastKey.current = key;
    switching.current = true;
    setValue(read(key));
  }, [key]);

  useEffect(() => {
    if (switching.current) {
      switching.current = false;
      return;
    }
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage unavailable or full — in-memory state still works.
    }
  }, [key, value]);

  return [value, setValue];
}
