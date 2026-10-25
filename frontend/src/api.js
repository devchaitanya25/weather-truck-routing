export const LEVELS = [
  { name: "Low", color: "#2e9e5b" },
  { name: "Moderate", color: "#e0b400" },
  { name: "High", color: "#f08a24" },
  { name: "Severe", color: "#d93025" },
  { name: "No Travel", color: "#6b1f8a" },
];

export const levelColor = (l) => LEVELS[l]?.color ?? "#888";

export async function geocode(q, signal) {
  const res = await fetch(`/api/geocode/?q=${encodeURIComponent(q)}`, { signal });
  if (!res.ok) return [];
  return (await res.json()).results;
}

export async function analyze(payload) {
  const res = await fetch("/api/analyze/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function fmtDuration(s) {
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

export function fmtTime(iso, opts = {}) {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...opts,
  });
}
