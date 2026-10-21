import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const WEEK = ["一", "二", "三", "四", "五", "六", "日"];
const MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

type Parts = { y: number; m: number; d: number; h: number; min: number };

function parseLocal(value: string): Parts | null {
  const matched = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!matched) return null;
  return {
    y: Number(matched[1]),
    m: Number(matched[2]),
    d: Number(matched[3]),
    h: Number(matched[4]),
    min: Number(matched[5]),
  };
}

function stamp(parts: Parts): string {
  return `${parts.y}-${pad(parts.m)}-${pad(parts.d)}T${pad(parts.h)}:${pad(parts.min)}`;
}

function monthCells(year: number, month: number): { y: number; m: number; d: number; outside: boolean }[] {
  const first = new Date(year, month - 1, 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(year, month - 1, 1 - offset);
  const cells = [];
  for (let i = 0; i < 42; i += 1) {
    const dt = new Date(start);
    dt.setDate(start.getDate() + i);
    cells.push({
      y: dt.getFullYear(),
      m: dt.getMonth() + 1,
      d: dt.getDate(),
      outside: dt.getMonth() !== month - 1,
    });
  }
  return cells;
}

function shiftMonth(year: number, month: number, delta: number): { y: number; m: number } {
  const dt = new Date(year, month - 1 + delta, 1);
  return { y: dt.getFullYear(), m: dt.getMonth() + 1 };
}

export function DateTimeField(props: {
  portalRoot?: HTMLElement | null;
  marker: "start" | "end";
  placeholder: string;
  value: string;
  disabled: boolean;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onChange: (value: string) => void;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const parsed = parseLocal(props.value);
  const now = new Date();
  const [view, setView] = useState({ y: parsed?.y ?? now.getFullYear(), m: parsed?.m ?? now.getMonth() + 1 });
  const [clock, setClock] = useState({ h: parsed?.h ?? (props.marker === "start" ? 9 : 10), min: parsed?.min ?? 0 });
  const [box, setBox] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (!props.open) return;
    const next = parseLocal(props.value);
    const base = next ?? {
      y: now.getFullYear(),
      m: now.getMonth() + 1,
      d: now.getDate(),
      h: props.marker === "start" ? 9 : 10,
      min: 0,
    };
    setView({ y: base.y, m: base.m });
    setClock({ h: next?.h ?? base.h, min: next?.min ?? base.min });
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const height=panelRef.current?.getBoundingClientRect().height||520;
      setBox({ top: Math.max(8,Math.min(rect.bottom+6,window.innerHeight-height-8)), left: Math.max(8, Math.min(rect.left, window.innerWidth - 296)) });
    }
    function onDoc(ev: MouseEvent) {
      const target = ev.target as Node;
      if (buttonRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      props.onClose();
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") props.onClose();
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [props.open]);

  function write(day: { y: number; m: number; d: number } | null, nextClock: { h: number; min: number }) {
    setClock(nextClock);
    const base = day ?? (parsed ? { y: parsed.y, m: parsed.m, d: parsed.d } : null);
    if (!base) return;
    props.onChange(stamp({ ...base, h: nextClock.h, min: nextClock.min }));
  }

  const text = parsed ? `${parsed.m}月${parsed.d}日 ${pad(parsed.h)}:${pad(parsed.min)}` : props.placeholder;
  const today = { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        disabled={props.disabled}
        data-event-time={props.marker}
        data-value={props.value}
        className="min-w-0 flex-1 rounded-[10px] border border-border bg-background px-2 py-1 text-left text-sm text-foreground disabled:opacity-50"
        onClick={() => (props.open ? props.onClose() : props.onOpen())}
      >
        {text}
      </button>
      {props.open
        ? createPortal(
            <div
              ref={panelRef}
              data-datetime-panel={props.marker}
              className="fixed z-[80] max-h-[calc(100vh-16px)] w-[280px] overflow-y-auto border border-border bg-card p-3 text-foreground"
              style={{ top: box.top, left: box.left }}
            >
              <div className="mb-2 flex items-center justify-between">
                <button type="button" className="desk-btn" onClick={() => setView((cur) => shiftMonth(cur.y, cur.m, -1))}>
                  上个月
                </button>
                <div data-datetime-month="">{`${view.y}年${view.m}月`}</div>
                <button type="button" className="desk-btn" onClick={() => setView((cur) => shiftMonth(cur.y, cur.m, 1))}>
                  下个月
                </button>
              </div>
              <div className="grid grid-cols-7 gap-1 text-center text-xs text-muted-foreground">
                {WEEK.map((name) => (
                  <div key={name}>{name}</div>
                ))}
              </div>
              <div className="mt-1 grid grid-cols-7 gap-1">
                {monthCells(view.y, view.m).map((cell) => {
                  const selected = parsed?.y === cell.y && parsed.m === cell.m && parsed.d === cell.d;
                  const isToday = today.y === cell.y && today.m === cell.m && today.d === cell.d;
                  return (
                    <button
                      key={`${cell.y}-${cell.m}-${cell.d}`}
                      type="button"
                      data-datetime-day={`${cell.y}-${pad(cell.m)}-${pad(cell.d)}`}
                      className={[
                        "h-7 rounded-[10px] text-xs",
                        cell.outside ? "text-muted-foreground/50" : "",
                        selected ? "bg-primary text-primary-foreground" : "",
                        isToday && !selected ? "border border-primary text-primary" : "",
                      ].join(" ")}
                      onClick={() => write(cell, clock)}
                    >
                      {cell.d}
                    </button>
                  );
                })}
              </div>
              <div className="mt-3 text-xs text-muted-foreground">小时</div>
              <div className="mt-1 grid grid-cols-6 gap-1">
                {Array.from({ length: 24 }, (_, hour) => (
                  <button
                    key={hour}
                    type="button"
                    data-datetime-hour={hour}
                    className={`h-7 rounded-[10px] text-xs ${clock.h === hour ? "bg-primary text-primary-foreground" : ""}`}
                    onClick={() => write(null, { h: hour, min: clock.min })}
                  >
                    {pad(hour)}
                  </button>
                ))}
              </div>
              <div className="mt-2 text-xs text-muted-foreground">分钟</div>
              <div className="mt-1 grid grid-cols-6 gap-1">
                {MINUTES.map((minute) => (
                  <button
                    key={minute}
                    type="button"
                    data-datetime-minute={minute}
                    className={`h-7 rounded-[10px] text-xs ${clock.min === minute ? "bg-primary text-primary-foreground" : ""}`}
                    onClick={() => write(null, { h: clock.h, min: minute })}
                  >
                    {pad(minute)}
                  </button>
                ))}
              </div>
            </div>,
            props.portalRoot || document.body,
          )
        : null}
    </>
  );
}
