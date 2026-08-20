import React, { useCallback, useEffect, useState } from "react";
import { authFetch, setUnauthorizedHandler } from "./services/api.js";
import { Navbar, Footer } from "./components/SiteChrome.jsx";
import { Hero, HowItWorks, ContactSection } from "./components/HomeSections.jsx";
import { ScanSection } from "./features/scan/ScanSection.jsx";
import { LibrarySection } from "./features/library/LibrarySection.jsx";
import { BrowseSection } from "./features/browse/BrowseSection.jsx";
import { AuthModal, ProfileModal } from "./features/auth/AuthModals.jsx";
import { AdminOverlay } from "./features/admin/AdminOverlay.jsx";

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("user") || "null");
    } catch {
      localStorage.removeItem("user");
      return null;
    }
  });
  const [modal, setModal] = useState(null);
  const [adminOpen, setAdminOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [libraryVersion, setLibraryVersion] = useState(0);
  const [authInfo, setAuthInfo] = useState("");

  const clearSession = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setToken("");
    setUser(null);
    setAdminOpen(false);
    setProfileOpen(false);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      if (token) await authFetch("/logout", token, { method: "POST" });
    } catch {
      // Local sign-out must still succeed if the API is temporarily offline.
    } finally {
      clearSession();
    }
  }, [clearSession, token]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearSession();
      setAuthInfo("Your session expired. Please sign in again.");
      setModal("login");
    });
    return () => setUnauthorizedHandler(() => {});
  }, [clearSession]);

  function handleLogin(newToken, newUser) {
    localStorage.setItem("token", newToken);
    localStorage.setItem("user", JSON.stringify(newUser));
    setToken(newToken);
    setUser(newUser);
    setModal(null);
    setAuthInfo("");
  }

  return (
    <div className="page">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <Navbar token={token} user={user}
        onSignIn={() => setModal("login")}
        onLogout={handleLogout}
        onAdmin={() => setAdminOpen(true)}
        onProfile={() => setProfileOpen(true)} />

      <main id="main-content">
        <Hero token={token} onSignUp={() => setModal("register")} />
        {/* The pitch sits directly under the headline for someone deciding
            whether to sign up, and disappears once they have. It used to live
            below the scanner, browse and library -- roughly 14,000px down the
            page, where a first-time visitor would never reach it, and where a
            returning reader would have to scroll past it to get to work. */}
        {!token && <HowItWorks />}
        <ScanSection token={token}
          onNeedLogin={() => setModal("login")}
          onScanned={() => setLibraryVersion((version) => version + 1)} />
        {token && <BrowseSection token={token} />}
        {token && <LibrarySection token={token} version={libraryVersion} />}
        <ContactSection />
      </main>
      <Footer token={token} onSignIn={() => setModal("login")} onSignUp={() => setModal("register")} />

      {modal && <AuthModal mode={modal} setMode={setModal} initialInfo={authInfo}
        onClose={() => { setModal(null); setAuthInfo(""); }} onLogin={handleLogin} />}
      {adminOpen && <AdminOverlay token={token} onClose={() => setAdminOpen(false)} />}
      {profileOpen && token && <ProfileModal token={token} onClose={() => setProfileOpen(false)} onLogout={handleLogout} />}
    </div>
  );
}
