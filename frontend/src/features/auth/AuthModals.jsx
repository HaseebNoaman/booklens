import React, { useEffect, useState } from "react";
import { Icon, ModalShell } from "../../components/ui.jsx";
import { authFetch, postJson } from "../../services/api.js";

    function AuthModal({ mode, setMode, onClose, onLogin, initialInfo, resetToken }) {
      const [name, setName] = useState("");
      const [email, setEmail] = useState("");
      const [password, setPassword] = useState("");
      const [error, setError] = useState("");
      // initialInfo lets App show a message here (e.g. "session expired")
      const [info, setInfo] = useState(initialInfo || "");
      // busy = a request is in flight. We disable the button so a double
      // click cannot send the same request twice.
      const [busy, setBusy] = useState(false);
      // Set when the server says this address exists but has never confirmed
      // itself. It is the only case where offering to send another link is
      // useful, so the button appears only then.
      const [needsConfirm, setNeedsConfirm] = useState(false);
      // What the "check your inbox" panel should say. Filled by signup, by a
      // resend, and by a password-reset request -- all three end the same way.
      const [sent, setSent] = useState(null);

      function reachFailed() {
        setError("Could not reach the server. Please try again.");
      }

      // The server reports whether mail actually left the building. "logged"
      // means no provider is configured and the link went to the server log
      // instead, which is normal on a developer machine and a serious problem
      // anywhere else -- so it is shown rather than smoothed over.
      function inboxPanel(data) {
        return {
          message: data.message,
          devNote: data.delivery === "logged"
            ? "No mail provider is configured on this server, so the link was written to the server log instead of being sent."
            : "",
        };
      }

      async function submitLogin(e) {
        e.preventDefault();   // stop the browser reloading the page on submit
        setError(""); setNeedsConfirm(false); setBusy(true);
        try {
          const { ok, data } = await postJson("/login", { email, password });
          if (ok) onLogin(data.token, data.user);
          else {
            setError(data.error || "Login failed");
            if (data.code === "email_unverified") setNeedsConfirm(true);
          }
        } catch (e) {
          reachFailed();
        } finally {
          setBusy(false);
        }
      }

      async function submitRegister(e) {
        e.preventDefault();
        setError(""); setBusy(true);
        try {
          const { ok, data } = await postJson("/register", { name, email, password });
          // An account now starts life unconfirmed, so there is nothing to
          // sign in to yet. Sending the reader to a sign-in form here would
          // hand them a password that does not work.
          if (ok) setSent(inboxPanel(data));
          else setError(data.error || "Registration failed");
        } catch (e) {
          reachFailed();
        } finally {
          setBusy(false);
        }
      }

      async function resendConfirmation() {
        setError(""); setBusy(true);
        try {
          const { ok, data } = await postJson("/resend-verification", { email });
          if (ok) setSent(inboxPanel(data));
          else setError(data.error || "Could not send the email");
        } catch (e) {
          reachFailed();
        } finally {
          setBusy(false);
        }
      }

      async function submitForgot(e) {
        e.preventDefault();
        setError(""); setBusy(true);
        try {
          const { ok, data } = await postJson("/forgot-password", { email });
          if (ok) setSent(inboxPanel(data));
          else setError(data.error || "Could not send the email");
        } catch (e) {
          reachFailed();
        } finally {
          setBusy(false);
        }
      }

      async function submitReset(e) {
        e.preventDefault();
        setError(""); setBusy(true);
        try {
          const { ok, data } = await postJson("/reset-password", {
            token: resetToken, password,
          });
          if (ok) {
            setPassword("");
            setMode("login");
            setInfo(data.message);
          } else {
            setError(data.error || "Could not update the password");
          }
        } catch (e) {
          reachFailed();
        } finally {
          setBusy(false);
        }
      }

      function goTo(next) {
        setMode(next); setError(""); setInfo(""); setNeedsConfirm(false); setSent(null);
      }

      // One panel for every "we have emailed you" outcome. It deliberately
      // does not say whether the address was already registered -- the server
      // does not tell us, on purpose.
      if (sent) {
        return (
          <ModalShell onClose={onClose} labelledBy="auth-dialog-title">
            <h2 id="auth-dialog-title" className="dialog-title">Check your inbox</h2>
            <p className="dialog-intro">{sent.message}</p>
            {sent.devNote && <div className="error-msg" role="status">{sent.devNote}</div>}
            <br />
            <button className="btn full" type="button" onClick={() => goTo("login")}>
              Back to sign in
            </button>
          </ModalShell>
        );
      }

      return (
        <ModalShell onClose={onClose} labelledBy="auth-dialog-title">
            {mode === "reset" ? (
              <form onSubmit={submitReset}>
                <h2 id="auth-dialog-title" className="dialog-title">Choose a new password</h2>
                <p className="dialog-intro">
                  You will be signed out everywhere else once this is saved.
                </p>
                <label htmlFor="reset-password">New password (minimum 8 characters)</label>
                <input id="reset-password" type="password" value={password}
                       onChange={(e) => setPassword(e.target.value)}
                       placeholder="••••••••" autoComplete="new-password" minLength={8} required />
                <br /><br />
                <button className="btn full" type="submit" disabled={busy}>
                  {busy ? "Saving..." : "Save new password"}
                </button>
                {error && <div className="error-msg" role="alert">{error}</div>}
                <button type="button" className="link" onClick={() => goTo("login")}>
                  Back to sign in
                </button>
              </form>
            ) : mode === "forgot" ? (
              <form onSubmit={submitForgot}>
                <h2 id="auth-dialog-title" className="dialog-title">Reset your password</h2>
                <p className="dialog-intro">
                  Enter your email address and we will send you a link.
                </p>
                <label htmlFor="forgot-email">Email</label>
                <input id="forgot-email" type="email" value={email}
                       onChange={(e) => setEmail(e.target.value)}
                       placeholder="you@example.com" autoComplete="email" required />
                <br /><br />
                <button className="btn full" type="submit" disabled={busy}>
                  {busy ? "Sending..." : "Send reset link"}
                </button>
                {error && <div className="error-msg" role="alert">{error}</div>}
                <button type="button" className="link" onClick={() => goTo("login")}>
                  Back to sign in
                </button>
              </form>
            ) : mode === "login" ? (
              // A real <form> means pressing Enter submits, like users expect.
              <form onSubmit={submitLogin}>
                <h2 id="auth-dialog-title" className="dialog-title">Welcome back</h2>
                <p className="dialog-intro">Sign in to continue scanning books.</p>
                {info && <div className="success-msg" role="status">{info}</div>}
                <label htmlFor="login-email">Email</label>
                <input id="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                       placeholder="you@example.com" autoComplete="email" required />
                <label htmlFor="login-password">Password</label>
                <input id="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                       placeholder="••••••••" autoComplete="current-password" required />
                <br /><br />
                <button className="btn full" type="submit" disabled={busy}>
                  {busy ? "Signing in..." : "Sign In"}
                </button>
                {error && <div className="error-msg" role="alert">{error}</div>}
                {needsConfirm && (
                  <button type="button" className="btn-outline full" disabled={busy}
                          onClick={resendConfirmation}>
                    Send the confirmation link again
                  </button>
                )}
                <button type="button" className="link" onClick={() => goTo("forgot")}>
                  Forgotten your password?
                </button>
                <button type="button" className="link" onClick={() => goTo("register")}>
                  New to BookLens? Create a free account
                </button>
              </form>
            ) : (
              <form onSubmit={submitRegister}>
                <h2 id="auth-dialog-title" className="dialog-title">Create your account</h2>
                <p className="dialog-intro">
                  We will email you a link to confirm your address.
                </p>
                <label htmlFor="register-name">Full name</label>
                <input id="register-name" value={name} onChange={(e) => setName(e.target.value)}
                       placeholder="Your name" autoComplete="name" required />
                <label htmlFor="register-email">Email</label>
                <input id="register-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                       placeholder="you@example.com" autoComplete="email" required />
                <label htmlFor="register-password">Password (minimum 8 characters)</label>
                <input id="register-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                       placeholder="••••••••" autoComplete="new-password" minLength={8} required />
                <br /><br />
                <button className="btn full" type="submit" disabled={busy}>
                  {busy ? "Creating account..." : "Create Account"}
                </button>
                {error && <div className="error-msg" role="alert">{error}</div>}
                <button type="button" className="link" onClick={() => goTo("login")}>
                  Already have an account? Sign in
                </button>
              </form>
            )}
        </ModalShell>
      );
    }

    // ==================== PROFILE MODAL ====================
    function ProfileModal({ token, onClose, onLogout }) {
      const [profile, setProfile] = useState(null);

      // change-password form
      const [currentPw, setCurrentPw] = useState("");
      const [newPw, setNewPw] = useState("");
      const [confirmPw, setConfirmPw] = useState("");
      const [pwError, setPwError] = useState("");
      const [pwSuccess, setPwSuccess] = useState("");
      const [busy, setBusy] = useState(false);

      // delete-account form
      const [deletePw, setDeletePw] = useState("");
      const [delError, setDelError] = useState("");

      // Interests. Editable here because taste moves, and a choice made once
      // at signup should not follow someone around forever. They are only a
      // cold-start signal: once real books exist, the profile prefers those
      // and these fall silent on their own.
      const [available, setAvailable] = useState([]);
      const [chosen, setChosen] = useState([]);
      const [interestSaved, setInterestSaved] = useState("");

      function toggleInterest(subject) {
        setInterestSaved("");
        setChosen((current) => current.includes(subject)
          ? current.filter((s) => s !== subject)
          : current.length >= 8 ? current : [...current, subject]);
      }

      async function saveInterests() {
        try {
          const res = await authFetch("/profile/interests", token, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ interests: chosen }),
          });
          if (res.ok) setInterestSaved("Saved.");
        } catch (e) { /* ignore */ }
      }

      // Load the account info once when the popup opens.
      useEffect(() => {
        async function load() {
          try {
            const res = await authFetch("/profile", token);
            const data = await res.json();
            if (res.ok) setProfile(data);
            const opts = await authFetch("/interests", token);
            const list = await opts.json();
            if (opts.ok) {
              setAvailable(list.available || []);
              setChosen(list.chosen || []);
            }
          } catch (e) { /* ignore */ }
        }
        load();
      }, []);

      async function changePassword(e) {
        e.preventDefault();
        setPwError(""); setPwSuccess("");
        // Catch typos before bothering the server.
        if (newPw !== confirmPw) {
          setPwError("New passwords do not match.");
          return;
        }
        setBusy(true);
        try {
          const res = await authFetch("/profile/password", token, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ current_password: currentPw, new_password: newPw })
          });
          const data = await res.json();
          if (res.ok) {
            setPwSuccess(data.message);
            setCurrentPw(""); setNewPw(""); setConfirmPw("");
            if (data.reauthenticate) window.setTimeout(onLogout, 900);
          } else {
            setPwError(data.error || "Could not change password");
          }
        } catch (e) {
          setPwError("Could not reach the server. Please try again.");
        } finally {
          setBusy(false);
        }
      }

      async function deleteAccount(e) {
        e.preventDefault();
        setDelError("");
        if (!window.confirm("This permanently deletes your account and scan history. Continue?")) return;
        try {
          const res = await authFetch("/profile", token, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: deletePw })
          });
          const data = await res.json();
          if (res.ok) {
            onLogout();   // account is gone -> log out immediately
          } else {
            setDelError(data.error || "Could not delete account");
          }
        } catch (e) {
          setDelError("Could not reach the server. Please try again.");
        }
      }

      return (
        <ModalShell onClose={onClose} labelledBy="profile-dialog-title" wide>
            <h2 id="profile-dialog-title" className="profile-title">My profile</h2>

            {profile && (
              <div className="profile-summary">
                <div className="info-line"><span className="i-icon"><Icon name="user" size={18} /></span><div><b>Name</b><span>{profile.name}</span></div></div>
                <div className="info-line"><span className="i-icon"><Icon name="mail" size={18} /></span><div><b>Email</b><span>{profile.email}</span></div></div>
                <div className="info-line"><span className="i-icon"><Icon name="clock" size={18} /></span><div><b>Joined</b><span>{profile.created_at}</span></div></div>
                <div className="info-line"><span className="i-icon"><Icon name="search" size={18} /></span><div><b>Total scans</b><span>{profile.scan_count}</span></div></div>
              </div>
            )}

            {available.length > 0 && (
              <div className="interest-editor">
                <div className="section-heading">Reading interests</div>
                <p className="interest-help">
                  Used only until you have books of your own. Once you mark books
                  as read, BookLens answers from those instead.
                </p>
                <div className="interest-options">
                  {available.map((subject) => (
                    <button key={subject} type="button"
                            className={"tag interest-option" +
                                       (chosen.includes(subject) ? " chosen" : "")}
                            aria-pressed={chosen.includes(subject)}
                            onClick={() => toggleInterest(subject)}>
                      {subject}
                    </button>
                  ))}
                </div>
                <div className="interest-actions">
                  <button className="btn" type="button" onClick={saveInterests}>
                    Save interests
                  </button>
                  {chosen.length > 0 && (
                    <button className="btn-outline" type="button"
                            onClick={() => { setChosen([]); setInterestSaved(""); }}>
                      Clear all
                    </button>
                  )}
                  {interestSaved && <span className="interest-saved">{interestSaved}</span>}
                </div>
              </div>
            )}

            <form onSubmit={changePassword}>
              <div className="section-heading">Change password</div>
              <label htmlFor="current-password">Current password</label>
              <input id="current-password" type="password" value={currentPw} onChange={(e) => setCurrentPw(e.target.value)}
                     autoComplete="current-password" required />
              <label htmlFor="new-password">New password (minimum 8 characters)</label>
              <input id="new-password" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)}
                     autoComplete="new-password" minLength={8} required />
              <label htmlFor="confirm-password">Repeat new password</label>
              <input id="confirm-password" type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                     autoComplete="new-password" minLength={8} required />
              <br /><br />
              <button className="btn full" type="submit" disabled={busy}>
                {busy ? "Saving..." : "Change Password"}
              </button>
              {pwError && <div className="error-msg" role="alert">{pwError}</div>}
              {pwSuccess && <div className="success-msg" role="status">{pwSuccess}</div>}
            </form>

            <form className="danger-zone" onSubmit={deleteAccount}>
              <b className="danger-heading">Danger zone</b>
              <p className="danger-copy">
                Deleting your account removes your login and scan history permanently.
              </p>
              <label htmlFor="delete-password">Type your password to confirm</label>
              <input id="delete-password" type="password" value={deletePw} onChange={(e) => setDeletePw(e.target.value)}
                     autoComplete="current-password" required />
              <br /><br />
              <button className="btn-danger" type="submit"><Icon name="trash" size={14} /> Delete My Account</button>
              {delError && <div className="error-msg" role="alert">{delError}</div>}
            </form>
        </ModalShell>
      );
    }

export { AuthModal, ProfileModal };
