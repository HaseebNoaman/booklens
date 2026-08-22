import React, { useEffect, useRef } from "react";
import { NO_COVER } from "../services/api.js";

export function Icon({ name, size = 20 }) {
  const icons = {
    book: <g><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></g>,
    camera: <g><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></g>,
    search: <g><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></g>,
    users: <g><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/></g>,
    mail: <g><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></g>,
    clock: <g><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></g>,
    shield: <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>,
    trash: <g><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></g>,
    check: <polyline points="20 6 9 17 4 12"/>,
    upload: <g><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></g>,
    globe: <g><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></g>,
    database: <g><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></g>,
    message: <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>,
    lock: <g><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></g>,
    x: <g><path d="M18 6 6 18"/><path d="m6 6 12 12"/></g>,
    menu: <g><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/></g>,
    star: <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>,
    starFill: <polygon fill="currentColor" points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>,
    user: <g><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></g>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" focusable="false">
      {icons[name] || icons.book}
    </svg>
  );
}

// A cover that degrades instead of breaking.
//
// Catalogue covers are fetched from Open Library by edition id, and on a
// measured sample only 73% exist. The URL carries "default=false", so a missing
// cover is a 404 rather than a blank grey image -- which would otherwise render
// as a broken-image icon on roughly one card in four.
//
// The chain is: the real cover, then the reader's own scan photo when we have
// one (it is the actual book in their hands), then the placeholder.
export function BookCover({ src, fallback = "", alt, className = "", loading }) {
  const chain = [src, fallback, NO_COVER].filter(Boolean);
  const [step, setStep] = React.useState(0);

  // A new book means a new chain; without this the failed step of the previous
  // cover would persist and hide a perfectly good new one.
  React.useEffect(() => { setStep(0); }, [src, fallback]);

  return (
    <img className={className} alt={alt} loading={loading}
         src={chain[Math.min(step, chain.length - 1)]}
         onError={() => setStep((s) => (s + 1 < chain.length ? s + 1 : s))} />
  );
}

export function ModalShell({ children, onClose, wide = false, labelledBy }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const dialog = dialogRef.current;
    const focusableSelector = "button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";
    const initialTarget = dialog?.querySelector("input:not([disabled]), button:not([disabled])");
    (initialTarget || dialog)?.focus();
    function onKeyDown(event) {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && dialog) {
        const focusable = [...dialog.querySelectorAll(focusableSelector)];
        if (!focusable.length) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div ref={dialogRef} tabIndex="-1" className={`modal ${wide ? "modal-wide" : ""}`}
        role="dialog" aria-modal="true" aria-labelledby={labelledBy}
        onMouseDown={(event) => event.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close dialog">
          <Icon name="x" size={20} />
        </button>
        {children}
      </div>
    </div>
  );
}

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <main className="container section" id="main-content">
          <div className="card error-state" role="alert">
            <h1>BookLens could not display this page</h1>
            <p>Refresh the page. Your account and library data remain stored on the server.</p>
            <button className="btn" onClick={() => window.location.reload()}>Refresh BookLens</button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
