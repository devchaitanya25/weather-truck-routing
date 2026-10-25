import React, { useEffect, useRef, useState } from "react";
import { geocode } from "./api.js";

/** Location input with debounced typeahead. Calls onSelect({label, lat, lng}). */
export default function PlaceInput({ label, value, onSelect }) {
  const [text, setText] = useState(value?.label ?? "");
  const [options, setOptions] = useState([]);
  const [open, setOpen] = useState(false);
  const skip = useRef(false);

  useEffect(() => {
    if (skip.current) {
      skip.current = false;
      return;
    }
    if (text.trim().length < 3) {
      setOptions([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        setOptions(await geocode(text.trim(), ctrl.signal));
        setOpen(true);
      } catch {
        /* aborted or offline */
      }
    }, 350);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [text]);

  return (
    <label className="field place">
      <span>{label}</span>
      <input
        value={text}
        placeholder="City or address"
        onChange={(e) => {
          setText(e.target.value);
          onSelect(null); // typing invalidates the previous selection
        }}
        onFocus={() => options.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && options.length > 0 && (
        <ul className="suggestions">
          {options.map((o, i) => (
            <li
              key={i}
              onMouseDown={() => {
                skip.current = true;
                setText(o.label);
                setOpen(false);
                onSelect(o);
              }}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
      {value && <small className="ok">✓ location set</small>}
    </label>
  );
}
