import React, { useMemo, useState } from "react";
import PlaceInput from "./PlaceInput.jsx";
import MapView from "./MapView.jsx";
import { LEVELS, analyze, fmtDuration, fmtTime, levelColor } from "./api.js";

// Default departure: next hour, formatted for <input type="datetime-local">.
function defaultDeparture() {
  const d = new Date(Date.now() + 3600_000);
  d.setMinutes(0, 0, 0);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function App() {
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [departure, setDeparture] = useState(defaultDeparture);
  const [load, setLoad] = useState(35000);
  const [interval, setIntervalMi] = useState(25);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [showHeat, setShowHeat] = useState(true);
  const [hour, setHour] = useState(0);

  const selected = useMemo(
    () => result?.routes.find((r) => r.id === selectedId),
    [result, selectedId]
  );
  const ranked = useMemo(
    () => (result ? [...result.routes].sort((a, b) => a.rank - b.rank) : []),
    [result]
  );

  async function submit(e) {
    e.preventDefault();
    if (!origin || !destination) {
      setError("Pick an origin and destination from the suggestions.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const data = await analyze({
        origin,
        destination,
        departure: new Date(departure).toISOString(),
        load_lb: Number(load),
        interval_mi: Number(interval),
      });
      setResult(data);
      setSelectedId(data.recommendation.route_id);
      setHour(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const heatTime = result
    ? fmtTime(new Date(new Date(result.heatmap.start).getTime() + hour * 3600_000))
    : "";

  return (
    <div className="layout">
      <aside className="panel">
        <h1>Weather-Aware Truck Routing</h1>
        <form onSubmit={submit}>
          <PlaceInput label="Origin" value={origin} onSelect={setOrigin} />
          <PlaceInput label="Destination" value={destination} onSelect={setDestination} />
          <label className="field">
            <span>Departure</span>
            <input
              type="datetime-local"
              value={departure}
              onChange={(e) => setDeparture(e.target.value)}
              required
            />
          </label>
          <div className="row">
            <label className="field">
              <span>Load (lb)</span>
              <input
                type="number"
                min="0"
                max="150000"
                step="500"
                value={load}
                onChange={(e) => setLoad(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Checkpoint every</span>
              <select value={interval} onChange={(e) => setIntervalMi(e.target.value)}>
                {[10, 25, 50].map((m) => (
                  <option key={m} value={m}>
                    {m} miles
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button disabled={loading}>{loading ? "Analyzing routes…" : "Find safest route"}</button>
        </form>

        {error && <div className="error">{error}</div>}

        {result && (
          <>
            <div className="recommend">
              <strong>★ Recommendation</strong>
              <p>{result.recommendation.summary}</p>
              <ul>
                {result.recommendation.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
            {result.notes.map((n) => (
              <div className="note" key={n}>
                {n}
              </div>
            ))}

            <h2>Routes</h2>
            {ranked.map((r) => (
              <div
                key={r.id}
                className={`card ${r.id === selectedId ? "active" : ""}`}
                onClick={() => setSelectedId(r.id)}
              >
                <div className="card-head">
                  <b>
                    {r.label} {r.recommended && <span className="badge">Recommended</span>}
                    {!r.drivable && <span className="badge bad">Not drivable</span>}
                  </b>
                  <span>
                    #{r.rank} · {fmtDuration(r.duration_s)}
                  </span>
                </div>
                <div className="muted">
                  {r.distance_mi} mi · via {r.via} · arrive {fmtTime(r.arrival)}
                </div>
                <div className="bar">
                  {LEVELS.map((l, i) => {
                    const mi = r.miles_by_level[l.name];
                    return mi > 0 ? (
                      <i
                        key={l.name}
                        style={{ flex: mi, background: l.color }}
                        title={`${l.name}: ${mi} mi`}
                      />
                    ) : null;
                  })}
                </div>
                <div className="muted small">
                  Severe {r.miles_by_level.Severe} mi · High {r.miles_by_level.High} mi · No Travel{" "}
                  {r.miles_by_level["No Travel"]} mi · avg risk {r.avg_risk}
                </div>
              </div>
            ))}

            {selected && (
              <>
                <h2>Checkpoints – {selected.label}</h2>
                <table>
                  <thead>
                    <tr>
                      <th>Mile</th>
                      <th>ETA</th>
                      <th>Wind</th>
                      <th>Rain</th>
                      <th>Snow</th>
                      <th>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.checkpoints.map((c) => (
                      <tr key={c.index} title={c.reason}>
                        <td>{c.mile}</td>
                        <td>{fmtTime(c.eta, { month: undefined, day: undefined })}</td>
                        <td>{c.weather.wind_mph}</td>
                        <td>{c.weather.rain_in_hr}</td>
                        <td>{c.weather.snow_in_hr}</td>
                        <td>
                          <span className="chip" style={{ background: levelColor(c.level) }}>
                            {c.level_name}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </>
        )}
      </aside>

      <main className="mapwrap">
        <MapView
          result={result}
          selectedId={selectedId}
          showHeat={showHeat}
          hour={hour}
          onSelectRoute={setSelectedId}
        />
        {result && (
          <div className="heatbar">
            <label className="toggle">
              <input
                type="checkbox"
                checked={showHeat}
                onChange={(e) => setShowHeat(e.target.checked)}
              />{" "}
              Weather heatmap
            </label>
            <input
              type="range"
              min="0"
              max={result.heatmap.hours}
              value={hour}
              disabled={!showHeat}
              onChange={(e) => setHour(Number(e.target.value))}
            />
            <span className="heattime">
              +{hour}h · {heatTime}
            </span>
          </div>
        )}
        <div className="legend">
          {LEVELS.map((l) => (
            <span key={l.name}>
              <i style={{ background: l.color }} /> {l.name}
            </span>
          ))}
        </div>
      </main>
    </div>
  );
}
