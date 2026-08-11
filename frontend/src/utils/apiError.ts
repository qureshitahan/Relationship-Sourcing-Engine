import { isAxiosError } from "axios";

/** Human-readable reason from an API error, so failures name the actual problem
 *  (missing prerequisite, validation issue, server fault) instead of a generic line. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  if (isAxiosError(e)) {
    if (!e.response) return "Cannot reach the server — check your connection and try again.";
    const detail: unknown = e.response.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
      // FastAPI validation errors: [{loc: ["body", "field"], msg: "..."}]
      const first = detail[0] as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(first.loc)
        ? first.loc.filter((p) => p !== "body").join(".")
        : "";
      if (first.msg) return field ? `${field}: ${first.msg}` : first.msg;
    }
    if (e.response.status >= 500)
      return `${fallback} The server hit an internal error (${e.response.status}) — please try again.`;
  }
  return fallback;
}
