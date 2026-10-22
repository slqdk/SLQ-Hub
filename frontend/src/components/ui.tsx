// Small building blocks: lamps, menus, modals, switches, toasts.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { useApp } from "../state";

export type LampState = "run" | "wait" | "act" | "fault" | "off";

export function Lamp({ state, size, pulse, title }: { state: LampState; size?: "sm" | "lg"; pulse?: boolean;
  title?: string }) {
  return <span className={`lamp ${size ?? ""} ${pulse ? "pulse" : ""}`} data-state={state} title={title}
    role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true} />;
}

export function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#3a4145" />
      <circle cx="32" cy="32" r="19" fill="#262b2e" />
      <circle cx="32" cy="32" r="17" fill="none" stroke="#9aa3a6" strokeWidth="2.5" />
      <circle cx="32" cy="32" r="12" fill="#3fb950" />
      <circle cx="28" cy="28" r="4" fill="#b8f0c2" opacity=".75" />
    </svg>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void;
  children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** A button with a drop-down menu; the menu closes on any click outside or on an item. */
export function MenuButton({ label, children, up, right, className, title, disabled, anchorClassName }: { label: ReactNode;
  children: (close: () => void) => ReactNode; up?: boolean; right?: boolean; className?: string; title?: string;
  disabled?: boolean; anchorClassName?: string }) {
  const [open, setOpen] = useState(false);
  const [shift, setShift] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  // A menu that would run past the edge of the screen (a chip near the right on a phone) moves back in.
  useLayoutEffect(() => {
    if (!open || !menu.current) {
      setShift(0);
      return;
    }
    const r = menu.current.getBoundingClientRect();
    const margin = 8;
    // The visible width: on a phone an overflowing menu widens the layout viewport (innerWidth) itself.
    const width = Math.min(window.innerWidth, document.documentElement.clientWidth || Infinity,
      window.visualViewport?.width ?? Infinity);
    let dx = 0;
    if (r.right > width - margin) dx = width - margin - r.right;
    if (r.left + dx < margin) dx = margin - r.left;
    setShift(dx);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", on);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", on);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  return (
    <div className={`menu-anchor${anchorClassName ? ` ${anchorClassName}` : ""}`} ref={ref}>
      <button type="button" className={className ?? "picker"} onClick={() => setOpen((o) => !o)} aria-haspopup="menu"
        aria-expanded={open} title={title} disabled={disabled}>
        {label}
      </button>
      {open && <div ref={menu} className={`menu ${up ? "up" : "down"} ${right ? "right" : ""}`} role="menu"
        style={shift ? { transform: `translateX(${shift}px)` } : undefined}>
        {children(() => setOpen(false))}
      </div>}
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void;
  label: string; disabled?: boolean }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} aria-label={label}
        onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function SwitchRow({ checked, onChange, title, children, disabled }: { checked: boolean;
  onChange: (v: boolean) => void; title: string; children?: ReactNode; disabled?: boolean }) {
  return (
    <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
      <Switch checked={checked} onChange={onChange} label={title} disabled={disabled} />
      <div className="grow">
        <div style={{ fontWeight: 600 }}>{title}</div>
        {children && <div className="muted small">{children}</div>}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T;
  onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} className="tab" role="tab" aria-selected={t.id === value} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Toasts() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast" role={t.kind === "fault" ? "alert" : "status"}>
          <Lamp state={t.kind === "fault" ? "fault" : t.kind === "run" ? "run" : "off"} />
          <div className="grow">{t.text}</div>
          <button className="icon-btn small" style={{ color: "inherit" }} onClick={() => dismiss(t.id)}
            aria-label="Dismiss"><X size={14} /></button>
        </div>
      ))}
    </div>
  );
}

/** Two clicks to do something that cannot be undone. */
/** A button that asks once more: the first click shows confirmText (an icon
 *  button widens for it, see .armed), the second does it. */
export function ConfirmButton({ onConfirm, children, confirmText = "Click again to confirm", className = "btn danger small",
  disabled, label }: { onConfirm: () => void; children: ReactNode; confirmText?: string; className?: string;
  disabled?: boolean; label?: string }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3500);
    return () => window.clearTimeout(t);
  }, [armed]);
  return (
    <button type="button" className={`${className}${armed ? " armed" : ""}`} disabled={disabled}
      aria-label={armed ? undefined : label} title={armed ? undefined : label}
      onClick={() => (armed ? (setArmed(false), onConfirm()) : setArmed(true))}>
      {armed ? confirmText : children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="list-empty">{children}</div>;
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms == null) return;
    const t = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(t);
  }, [ms]);
}

export { copyText } from "../lib/clipboard";
