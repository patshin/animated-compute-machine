import {
  type ButtonHTMLAttributes,
  type CSSProperties,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  useEffect,
  useId,
  useRef,
  useState
} from "react";

import { PixelIcon, type PixelIconName } from "./PixelIcon";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "console";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
}

export function Button({ variant = "secondary", loading = false, className = "", children, disabled, ...props }: ButtonProps) {
  return (
    <button
      className={`button button--${variant} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <span className="pixel-spinner" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  );
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: PixelIconName;
  label: string;
}

export function IconButton({ icon, label, className = "", ...props }: IconButtonProps) {
  return (
    <button className={`icon-button ${className}`} aria-label={label} title={label} {...props}>
      <PixelIcon name={icon} />
    </button>
  );
}

interface BadgeProps {
  children: ReactNode;
  tone?: "neutral" | "active" | "success" | "warning" | "error" | "model" | "evidence";
}

export function Badge({ children, tone = "neutral" }: BadgeProps) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

interface PanelProps {
  title?: ReactNode;
  context?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: "paper" | "console";
}

export function Panel({ title, context, actions, children, className = "", tone = "paper" }: PanelProps) {
  return (
    <section className={`panel panel--${tone} ${className}`}>
      {(title || context || actions) && (
        <header className="panel__header">
          <div>
            {context && <div className="machine-label">{context}</div>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="panel__actions">{actions}</div>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}

interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

interface TabsProps {
  items: TabItem[];
  selectedId: string;
  onChange: (id: string) => void;
  label: string;
}

export function Tabs({ items, selectedId, onChange, label }: TabsProps) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const panelId = useId();

  const move = (index: number, direction: number) => {
    const next = (index + direction + items.length) % items.length;
    onChange(items[next].id);
    tabRefs.current[next]?.focus();
  };

  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label={label}>
        {items.map((item, index) => (
          <button
            aria-controls={`${panelId}-${item.id}`}
            aria-selected={item.id === selected.id}
            className="tabs__tab"
            id={`${panelId}-${item.id}-tab`}
            key={item.id}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight") { event.preventDefault(); move(index, 1); }
              if (event.key === "ArrowLeft") { event.preventDefault(); move(index, -1); }
              if (event.key === "Home") { event.preventDefault(); onChange(items[0].id); tabRefs.current[0]?.focus(); }
              if (event.key === "End") { event.preventDefault(); onChange(items.at(-1)?.id ?? item.id); tabRefs.current.at(-1)?.focus(); }
            }}
            ref={(node) => { tabRefs.current[index] = node; }}
            role="tab"
            tabIndex={item.id === selected.id ? 0 : -1}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        aria-labelledby={`${panelId}-${selected.id}-tab`}
        className="tabs__panel"
        id={`${panelId}-${selected.id}`}
        role="tabpanel"
      >
        {selected.content}
      </div>
    </div>
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  hint?: string;
}

export function Input({ label, error, hint, id: providedId, ...props }: FieldProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const messageId = `${id}-message`;
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <input id={id} aria-invalid={Boolean(error)} aria-describedby={error || hint ? messageId : undefined} {...props} />
      {(error || hint) && <span className={`field__message ${error ? "field__message--error" : ""}`} id={messageId}>{error ?? hint}</span>}
    </label>
  );
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  children: ReactNode;
}

export function Select({ label, children, id: providedId, ...props }: SelectProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <label className="field field--select" htmlFor={id}>
      <span className="field__label">{label}</span>
      <select id={id} {...props}>{children}</select>
    </label>
  );
}

function useModalFocus(open: boolean, onClose: () => void, container: React.RefObject<HTMLElement | null>) {
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => Array.from(
      container.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex='-1'])") ?? []
    );
    const preferred = container.current?.querySelector<HTMLElement>("[data-autofocus]");
    (preferred ?? focusable()[0])?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items.at(-1) ?? first;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      returnTarget?.focus();
    };
  }, [container, open]);
}

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

export function Dialog({ open, onClose, title, children }: ModalProps) {
  const titleId = useId();
  const container = useRef<HTMLElement>(null);
  useModalFocus(open, onClose, container);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section aria-labelledby={titleId} aria-modal="true" className="dialog" ref={container} role="dialog">
        <header className="modal-header"><h2 id={titleId}>{title}</h2><IconButton icon="close" label="Close dialog" onClick={onClose} /></header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: ModalProps) {
  const titleId = useId();
  const container = useRef<HTMLElement>(null);
  useModalFocus(open, onClose, container);
  if (!open) return null;
  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside aria-labelledby={titleId} aria-modal="true" className="drawer" ref={container} role="dialog">
        <header className="modal-header"><h2 id={titleId}>{title}</h2><IconButton icon="close" label="Close drawer" onClick={onClose} /></header>
        <div className="modal-body">{children}</div>
      </aside>
    </div>
  );
}

interface ToastProps { message: string | null; }
export function Toast({ message }: ToastProps) {
  return <div className={`toast ${message ? "toast--visible" : ""}`} role="status" aria-live="polite">{message}</div>;
}

interface StateProps {
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: StateProps) {
  return <div className="empty-state"><div className="empty-state__glyph" aria-hidden="true"><i /><i /><i /></div><h2>{title}</h2><p>{description}</p>{action}</div>;
}

export function ErrorState({ title, description, action }: StateProps) {
  return <div className="error-state" role="alert"><span className="error-state__code">ERR//BOUNDARY</span><h2>{title}</h2><p>{description}</p>{action}</div>;
}

interface SplitPaneProps {
  primary: ReactNode;
  secondary: ReactNode;
  label?: string;
  initialSecondarySize?: number;
  secondarySize?: number;
  onSecondarySizeChange?: (size: number) => void;
}

export function SplitPane({ primary, secondary, label = "Resize inspector", initialSecondarySize = 340, secondarySize, onSecondarySizeChange }: SplitPaneProps) {
  const [internalSize, setInternalSize] = useState(initialSecondarySize);
  const size = secondarySize ?? internalSize;
  const update = (next: number) => {
    const clamped = Math.max(280, Math.min(480, Math.round(next)));
    setInternalSize(clamped);
    onSecondarySizeChange?.(clamped);
  };
  const resizeFromPointer = (target: HTMLButtonElement, clientX: number) => {
    const paneBounds = target.parentElement?.getBoundingClientRect();
    if (!paneBounds) return;
    const gutterOffset = target.getBoundingClientRect().width / 2;
    update(paneBounds.right - clientX - gutterOffset);
  };
  return (
    <div className="split-pane" style={{ "--split-size": `${size}px` } as CSSProperties}>
      <div className="split-pane__primary">{primary}</div>
      <button
        aria-label={label}
        aria-orientation="vertical"
        aria-valuemax={480}
        aria-valuemin={280}
        aria-valuenow={size}
        aria-valuetext={`${size}px inspector width`}
        className="split-pane__separator"
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") { event.preventDefault(); update(size + 24); }
          if (event.key === "ArrowRight") { event.preventDefault(); update(size - 24); }
          if (event.key === "Home") { event.preventDefault(); update(280); }
          if (event.key === "End") { event.preventDefault(); update(480); }
        }}
        onPointerCancel={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          resizeFromPointer(event.currentTarget, event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) resizeFromPointer(event.currentTarget, event.clientX);
        }}
        onPointerUp={(event) => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
          resizeFromPointer(event.currentTarget, event.clientX);
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        role="separator"
        type="button"
      ><span aria-hidden="true" /></button>
      <div className="split-pane__secondary">{secondary}</div>
    </div>
  );
}
