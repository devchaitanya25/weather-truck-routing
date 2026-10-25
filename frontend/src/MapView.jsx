import React, { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { LEVELS, levelColor, fmtTime } from "./api.js";

// leaflet.heat expects a global L; load it after exposing one.
window.L = L;
const heatReady = import("leaflet.heat");

const HEAT_GRADIENT = { 0.25: "#e0b400", 0.5: "#f08a24", 0.75: "#d93025", 1.0: "#6b1f8a" };

export default function MapView({ result, selectedId, showHeat, hour, onSelectRoute }) {
  const el = useRef(null);
  const map = useRef(null);
  const routeLayer = useRef(null);
  const heatLayer = useRef(null);
  const [heatLoaded, setHeatLoaded] = useState(false);

  // One-time map setup.
  useEffect(() => {
    map.current = L.map(el.current, { preferCanvas: true }).setView([39.5, -98.35], 4);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map.current);
    routeLayer.current = L.layerGroup().addTo(map.current);
    heatReady.then(() => setHeatLoaded(true));
    return () => map.current.remove();
  }, []);

  // Routes, checkpoints and endpoints.
  useEffect(() => {
    const group = routeLayer.current;
    group.clearLayers();
    if (!result) return;

    const bounds = [];
    const ordered = [...result.routes].sort(
      (a, b) => (a.id === selectedId) - (b.id === selectedId) // selected drawn last (on top)
    );

    ordered.forEach((r) => {
      const selected = r.id === selectedId;
      r.segments.forEach((seg) => {
        seg.path.forEach((p) => bounds.push(p));
        L.polyline(seg.path, {
          color: selected ? levelColor(seg.level) : "#8a8f98",
          weight: selected ? 7 : 4,
          opacity: selected ? 0.95 : 0.55,
          dashArray: selected ? null : "6 8",
        })
          .bindTooltip(`${r.label}${r.recommended ? " ★" : ""} – ${LEVELS[seg.level].name}`, {
            sticky: true,
          })
          .on("click", () => onSelectRoute(r.id))
          .addTo(group);
      });

      if (selected) {
        r.checkpoints.forEach((c) => {
          L.circleMarker([c.lat, c.lon], {
            radius: 7,
            color: "#fff",
            weight: 2,
            fillColor: levelColor(c.level),
            fillOpacity: 1,
          })
            .bindPopup(
              `<b>Mile ${c.mile} – ${c.level_name}</b><br/>ETA ${fmtTime(c.eta)}<br/>` +
                `Wind ${c.weather.wind_mph} mph · Rain ${c.weather.rain_in_hr} in/hr · ` +
                `Snow ${c.weather.snow_in_hr} in/hr<br/><i>${c.reason}</i>`
            )
            .addTo(group);
        });
      }
    });

    const { origin, destination } = result.request;
    [
      [origin, "A"],
      [destination, "B"],
    ].forEach(([p, t]) =>
      L.marker([p.lat, p.lng], {
        icon: L.divIcon({ className: "pin", html: t, iconSize: [26, 26], iconAnchor: [13, 13] }),
      })
        .bindTooltip(p.label || t)
        .addTo(group)
    );

    if (bounds.length) map.current.fitBounds(bounds, { padding: [40, 40] });
  }, [result, selectedId]);

  // Heatmap for the chosen forecast hour (cheap update: only setLatLngs).
  useEffect(() => {
    if (!heatLoaded) return;
    if (heatLayer.current) {
      map.current.removeLayer(heatLayer.current);
      heatLayer.current = null;
    }
    if (!result || !showHeat) return;
    const pts = result.heatmap.points
      .map(([lat, lon, levels]) => [lat, lon, levels[hour] / 4])
      .filter((p) => p[2] > 0);
    // Always keep a faint "Low" marker so the corridor is visible even in calm weather.
    heatLayer.current = L.heatLayer(pts, {
      radius: 38,
      blur: 30,
      max: 1.0,
      minOpacity: 0.35,
      gradient: HEAT_GRADIENT,
    }).addTo(map.current);
  }, [result, showHeat, hour, heatLoaded]);

  return <div id="map" ref={el} />;
}
