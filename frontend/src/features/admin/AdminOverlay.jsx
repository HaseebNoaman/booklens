import React, { useEffect, useState } from "react";
import { Icon } from "../../components/ui.jsx";
import { authFetch } from "../../services/api.js";

    function CatalogueEditor({ token, record, onSaved }) {
      const empty = { title: "", author: "", isbn_13: "", google_volume_id: "",
        publisher: "", publication_year: "", genres: "", verified_summary: "",
        verification_notes: "", verification_status: "PENDING" };
      const [form, setForm] = useState(record || empty);
      const [error, setError] = useState("");
      const [busy, setBusy] = useState(false);
      useEffect(() => setForm(record || empty), [record && record.id]);
      function field(name, value) { setForm({ ...form, [name]: value }); }
      async function submit(e) {
        e.preventDefault(); setBusy(true); setError("");
        try {
          const path = record && record.id ? "/admin/catalogue/" + record.id : "/admin/catalogue";
          const res = await authFetch(path, token, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
          const data = await res.json();
          if (!res.ok) setError(data.error || "Could not save catalogue record.");
          else { setForm(empty); onSaved(); }
        } catch (e) { setError("Could not reach the server."); }
        finally { setBusy(false); }
      }
      return (
        <form className="card admin-editor" onSubmit={submit}>
          <h3>{record && record.id ? "Edit catalogue record" : "Add catalogue record"}</h3>
          <div className="admin-form-grid">
            <div><label>Title</label><input value={form.title || ""} onChange={(e) => field("title", e.target.value)} required /></div>
            <div><label>Author</label><input value={form.author || ""} onChange={(e) => field("author", e.target.value)} required /></div>
            <div><label>ISBN-13</label><input value={form.isbn_13 || ""} onChange={(e) => field("isbn_13", e.target.value)} /></div>
            <div><label>Google Volume ID</label><input value={form.google_volume_id || ""} onChange={(e) => field("google_volume_id", e.target.value)} /></div>
            <div><label>Publisher</label><input value={form.publisher || ""} onChange={(e) => field("publisher", e.target.value)} /></div>
            <div><label>Publication year</label><input value={form.publication_year || ""} onChange={(e) => field("publication_year", e.target.value)} /></div>
            <div className="span-2"><label>Genres</label><input value={form.genres || ""} onChange={(e) => field("genres", e.target.value)} /></div>
            <div className="span-2"><label>Verified full summary</label><textarea value={form.verified_summary || ""} onChange={(e) => field("verified_summary", e.target.value)} /></div>
            <div className="span-2"><label>Verification notes</label><textarea value={form.verification_notes || ""} onChange={(e) => field("verification_notes", e.target.value)} /></div>
            <div><label>Status</label><select value={form.verification_status || "PENDING"} onChange={(e) => field("verification_status", e.target.value)}>
              <option>PENDING</option><option>NEEDS_REVIEW</option><option>VERIFIED</option><option>REJECTED</option>
            </select></div>
          </div>
          <div className="button-row admin-form-actions">
            <button className="btn" disabled={busy}>{busy ? "Saving..." : "Save record"}</button>
            {record && record.id && <button type="button" className="btn-outline" onClick={() => onSaved()}>Cancel</button>}
          </div>
          {error && <div className="error-msg" role="alert">{error}</div>}
        </form>
      );
    }

    // ==================== ADMIN OVERLAY ====================
    function AdminOverlay({ token, onClose }) {
      const [tab, setTab] = useState("overview");
      const [stats, setStats] = useState(null);
      const [users, setUsers] = useState([]);
      const [books, setBooks] = useState([]);
      const [messages, setMessages] = useState([]);
      const [catalogue, setCatalogue] = useState([]);
      const [identifications, setIdentifications] = useState([]);
      const [system, setSystem] = useState(null);
      const [editingCatalogue, setEditingCatalogue] = useState(null);
      const [error, setError] = useState("");

      async function adminGet(path) {
        const res = await authFetch(path, token);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Request failed");
        return data;
      }

      async function loadAll() {
        setError("");
        try {
          setStats(await adminGet("/admin/stats"));
          setUsers(await adminGet("/admin/users"));
          setBooks(await adminGet("/admin/books"));
          setMessages(await adminGet("/admin/messages"));
          setCatalogue(await adminGet("/admin/catalogue"));
          setIdentifications(await adminGet("/admin/identifications"));
          setSystem(await adminGet("/admin/system"));
        } catch (e) { setError(e.message); }
      }

      useEffect(() => { loadAll(); }, []);

      // One delete helper for users, books and messages â€” they only differ
      // in the URL and the confirmation text.
      async function deleteItem(path, confirmText) {
        if (!window.confirm(confirmText)) return;
        try {
          const res = await authFetch(path, token, { method: "DELETE" });
          if (!res.ok) {
            const data = await res.json();
            setError(data.error || "Delete failed");
            return;
          }
          loadAll();
        } catch (e) {
          setError("Could not reach the server. Please try again.");
        }
      }

      return (
        <div className="admin-overlay">
          <div className="admin-head">
            <h2>Dashboard</h2>
            <button className="btn-outline" onClick={onClose}><Icon name="x" size={16} /> Close</button>
          </div>
          <div className="admin-body">
            {error && <div className="error-msg">{error}</div>}

            {stats && (
              <div className="stat-row">
                <div className="stat-card"><div className="s-icon"><Icon name="users" /></div><div><div className="label">Total Users</div><div className="value">{stats.total_users}</div></div></div>
                <div className="stat-card"><div className="s-icon"><Icon name="book" /></div><div><div className="label">Books in Cache</div><div className="value">{stats.total_books}</div></div></div>
                <div className="stat-card"><div className="s-icon"><Icon name="search" /></div><div><div className="label">Total Scans</div><div className="value">{stats.total_scans}</div></div></div>
                <div className="stat-card"><div className="s-icon"><Icon name="message" /></div><div><div className="label">Messages</div><div className="value">{messages.length}</div></div></div>
                <div className="stat-card"><div className="s-icon"><Icon name="database" /></div><div><div className="label">Verified Catalogue</div><div className="value">{stats.catalogue_verified}</div></div></div>
                <div className="stat-card"><div className="s-icon"><Icon name="search" /></div><div><div className="label">Needs Confirmation</div><div className="value">{stats.needs_confirmation}</div></div></div>
              </div>
            )}

            <div className="tabs">
              <button className={"tab " + (tab === "overview" ? "active" : "")} onClick={() => setTab("overview")}>Recent Activity</button>
              <button className={"tab " + (tab === "users" ? "active" : "")} onClick={() => setTab("users")}>Users</button>
              <button className={"tab " + (tab === "books" ? "active" : "")} onClick={() => setTab("books")}>Books</button>
              <button className={"tab " + (tab === "catalogue" ? "active" : "")} onClick={() => setTab("catalogue")}>Catalogue</button>
              <button className={"tab " + (tab === "identifications" ? "active" : "")} onClick={() => setTab("identifications")}>Identification Review</button>
              <button className={"tab " + (tab === "system" ? "active" : "")} onClick={() => setTab("system")}>System</button>
              <button className={"tab " + (tab === "messages" ? "active" : "")} onClick={() => setTab("messages")}>Messages</button>
            </div>

            {tab === "overview" && (
              <div className="card table-wrap">
                {stats && stats.recent_scans.length === 0 && <div className="empty-note">No scan activity yet.</div>}
                {stats && stats.recent_scans.length > 0 && (
                  <table>
                    <thead><tr><th>User</th><th>Book</th><th>Author</th><th>Time</th></tr></thead>
                    <tbody>
                      {stats.recent_scans.map((s) => (
                        <tr key={s.id}>
                          <td>{s.user_name}</td><td>{s.title}</td><td>{s.author}</td>
                          <td className="td-muted">{s.scanned_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {tab === "users" && (
              <div className="card table-wrap">
                <table>
                  <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Scans</th><th>Joined</th><th></th></tr></thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id}>
                        <td>{u.id}</td><td>{u.name}</td><td>{u.email}</td>
                        <td>{u.is_admin ? <span className="badge-admin">ADMIN</span> : <span className="badge-user">USER</span>}</td>
                        <td>{u.is_active ? "Active" : "Inactive"}</td>
                        <td>{u.scan_count}</td>
                        <td className="td-muted">{u.created_at}</td>
                        <td>{!u.is_admin && <div className="button-row compact-actions">
                          <button className="btn-outline touch-target" onClick={async () => {
                            await authFetch("/admin/users/" + u.id + "/active", token, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !u.is_active }) }); loadAll();
                          }}>{u.is_active ? "Deactivate" : "Activate"}</button>
                          <button className="btn-danger touch-target" onClick={() => deleteItem("/admin/users/" + u.id, "Delete user '" + u.name + "' and their history?")}><Icon name="trash" size={14} /> Delete</button>
                        </div>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === "books" && (
              <div className="card table-wrap">
                {books.length === 0 && <div className="empty-note">No books in the cache yet.</div>}
                {books.length > 0 && (
                  <table>
                    <thead><tr><th>ID</th><th>Title</th><th>Author</th><th>Publisher</th><th>Scans</th><th>Added</th><th></th></tr></thead>
                    <tbody>
                      {books.map((b) => (
                        <tr key={b.id}>
                          <td>{b.id}</td><td>{b.title}</td><td>{b.author}</td><td>{b.publisher}</td>
                          <td>{b.scan_count}</td>
                          <td className="td-muted">{b.created_at}</td>
                          <td><button className="btn-danger" onClick={() => deleteItem("/admin/books/" + b.id, "Delete book '" + b.title + "' from the cache?")}><Icon name="trash" size={14} /> Delete</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {tab === "catalogue" && (
              <div>
                <CatalogueEditor token={token} record={editingCatalogue}
                  onSaved={() => { setEditingCatalogue(null); loadAll(); }} />
                <div className="card table-wrap">
                  {catalogue.length === 0 && <div className="empty-note">No catalogue records yet. Import or add reviewed records; the application remains safe while the catalogue is incomplete.</div>}
                  {catalogue.length > 0 && <table>
                    <thead><tr><th>Title</th><th>Author</th><th>ISBN-13</th><th>Status</th><th>Updated</th><th></th></tr></thead>
                    <tbody>{catalogue.map((c) => <tr key={c.id}>
                      <td>{c.title}</td><td>{c.author}</td><td>{c.isbn_13 || "—"}</td>
                      <td><span className={c.verification_status === "VERIFIED" ? "badge-admin" : "badge-user"}>{c.verification_status}</span></td>
                      <td className="td-muted">{c.updated_at}</td>
                      <td><button className="btn-outline touch-target" onClick={() => setEditingCatalogue(c)}>Edit / review</button></td>
                    </tr>)}</tbody>
                  </table>}
                </div>
              </div>
            )}

            {tab === "identifications" && (
              <div className="card table-wrap">
                {identifications.length === 0 && <div className="empty-note">No identification attempts yet.</div>}
                {identifications.length > 0 && <table>
                  <thead><tr><th>User</th><th>Input</th><th>OCR status</th><th>Detected / entered</th><th>Decision</th><th>Selected book</th><th>Time</th></tr></thead>
                  <tbody>{identifications.map((a) => <tr key={a.id}>
                    <td>{a.user_name}</td><td>{a.input_method}</td><td>{a.ocr_status || "—"}</td>
                    <td>{a.query_title || a.ocr_title || a.query_isbn || "—"}</td><td>{a.decision}</td>
                    <td>{a.selected_title || "—"}</td><td className="td-muted">{a.created_at}</td>
                  </tr>)}</tbody>
                </table>}
              </div>
            )}

            {tab === "system" && system && (
              <div className="card">
                <h3>System information</h3>
                <div className="info-line"><b>Database</b><span>{system.database}</span></div>
                <div className="info-line"><b>Google Books</b><span>{system.google_books_configured ? "Configured" : "Not configured; Open Library fallback remains available"}</span></div>
                <div className="info-line"><b>External overview</b><span>Deterministic grounded sentence-window selection</span></div>
                <div className="info-line"><b>Legacy FLAN checkpoint</b><span>{system.flan_t5_available ? "Present but not used by the production overview" : "Not installed; not required by the production overview"}</span></div>
                <div className="info-line"><b>Primary identification</b><span>OCR first; barcode is optional fallback</span></div>
                <div className="info-line"><b>Catalogue</b><span>{system.catalogue.verified} verified, {system.catalogue.pending} pending, {system.catalogue.rejected} rejected</span></div>
              </div>
            )}

            {tab === "messages" && (
              <div className="card table-wrap">
                {messages.length === 0 && <div className="empty-note">No contact messages yet.</div>}
                {messages.length > 0 && (
                  <table>
                    <thead><tr><th>From</th><th>Email</th><th>Subject</th><th>Message</th><th>Time</th><th></th></tr></thead>
                    <tbody>
                      {messages.map((m) => (
                        <tr key={m.id}>
                          <td>{m.name}</td><td>{m.email}</td><td>{m.subject}</td>
                          <td className="message-cell">{m.message}</td>
                          <td className="td-muted">{m.created_at}</td>
                          <td><button className="btn-danger" onClick={() => deleteItem("/admin/messages/" + m.id, "Delete this message?")}><Icon name="trash" size={14} /> Delete</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        </div>
      );
    }

export { AdminOverlay };
