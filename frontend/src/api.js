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

async function post(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/** Step 1: routes, checkpoints, ETAs, and the weather points to fetch. */
export const planTrip = (payload) => post("/api/plan/", payload);

/** Step 3: Django scores the plan against the weather we fetched. */
export const analyzeWithWeather = (plan, weather) => post("/api/analyze/", { plan, weather });

// ---- Step 2: weather, fetched straight from Open-Meteo by the visitor's browser ----------
// (Open-Meteo's free tier rate-limits shared server IPs, so the server doesn't call it.)

const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";
const CHUNK = 50;
const RETRY_DELAYS_MS = [2000, 5000, 10000];
const CACHE_MS = 30 * 60 * 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const forecastCache = new Map(); // key -> { at, days, times, wind, rain, snow }

async function fetchChunk(points, forecastDays) {
  const params = new URLSearchParams({
    latitude: points.map((p) => p.lat).join(","),
    longitude: points.map((p) => p.lon).join(","),
    hourly: "wind_speed_10m,rain,showers,snowfall",
    wind_speed_unit: "mph",
    precipitation_unit: "inch",
    timezone: "UTC",
    past_days: "1",
    forecast_days: String(forecastDays),
  });
  let lastError = "";
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      const res = await fetch(`${OPEN_METEO}?${params}`);
      if (res.ok) {
        const data = await res.json();
        return Array.isArray(data) ? data : [data];
      }
      lastError = `Weather service returned HTTP ${res.status}`;
      if (res.status !== 429 && res.status < 500) break; // not worth retrying
    } catch (err) {
      lastError = `Weather service unreachable (${err.message})`;
    }
    if (attempt < RETRY_DELAYS_MS.length) await sleep(RETRY_DELAYS_MS[attempt]);
  }
  throw new Error(lastError || "Weather service unavailable");
}

function parseLocation(loc) {
  const h = loc.hourly;
  const times = h.time.map((t) => Date.parse(`${t}Z`) / 1000);
  const n = (v) => (v == null ? 0 : v);
  const snowScale = loc.hourly_units?.snowfall === "cm" ? 1 / 2.54 : 1;
  return {
    times,
    wind: h.wind_speed_10m.map(n),
    rain: h.rain.map((r, i) => n(r) + n(h.showers[i])),
    snow: h.snowfall.map((s) => n(s) * snowScale),
  };
}

/** Trim to the hours the server asked for and round to keep the upload small. */
function toPayload(f, window) {
  let i0 = f.times.findIndex((t) => t >= window.start);
  if (i0 < 0) i0 = Math.max(0, f.times.length - 2);
  let i1 = f.times.length;
  while (i1 > i0 + 2 && f.times[i1 - 1] > window.end) i1--;
  const r = (arr, d) => arr.slice(i0, i1).map((v) => Number(v.toFixed(d)));
  return { start: f.times[i0], wind: r(f.wind, 1), rain: r(f.rain, 3), snow: r(f.snow, 3) };
}

/** Returns { [pointKey]: {start, wind, rain, snow} } for every point in the plan. */
export async function fetchWeather(plan, onProgress = () => {}) {
  const { window, weather_points: points } = plan;
  const todayStart = Math.floor(Date.now() / 86400000) * 86400;
  const days = Math.min(16, Math.max(2, Math.ceil((window.end - todayStart) / 86400) + 1));

  const full = {};
  const missing = [];
  for (const p of points) {
    const c = forecastCache.get(p.key);
    if (c && c.days >= days && Date.now() - c.at < CACHE_MS) full[p.key] = c;
    else missing.push(p);
  }

  for (let i = 0; i < missing.length; i += CHUNK) {
    onProgress(Math.min(i, missing.length), missing.length);
    const chunk = missing.slice(i, i + CHUNK);
    const locations = await fetchChunk(chunk, days);
    if (locations.length !== chunk.length) throw new Error("Weather service returned unexpected data");
    chunk.forEach((p, j) => {
      const entry = { ...parseLocation(locations[j]), at: Date.now(), days };
      forecastCache.set(p.key, entry);
      full[p.key] = entry;
    });
  }
  onProgress(missing.length, missing.length);

  const out = {};
  for (const p of points) out[p.key] = toPayload(full[p.key], window);
  return out;
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
