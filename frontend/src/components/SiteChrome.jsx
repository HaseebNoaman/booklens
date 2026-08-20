import React, { useState } from "react";
import { Icon } from "./ui.jsx";

    function Navbar({ token, user, onSignIn, onLogout, onAdmin, onProfile }) {
      // menuOpen controls the mobile hamburger dropdown.
      const [menuOpen, setMenuOpen] = useState(false);

      // Every menu item calls go(): it closes the mobile dropdown first,
      // then runs the extra action if one is given (logout, open admin...).
      function go(action) {
        setMenuOpen(false);
        if (action) action();
      }

      // The links are the same on desktop and mobile, so we build them once.
      const links = (
        <React.Fragment>
          <a className="nav-link" href="#scan" onClick={() => go()}>Identify</a>
          {token && <a className="nav-link" href="#browse" onClick={() => go()}>Browse</a>}
          {token && <a className="nav-link" href="#library" onClick={() => go()}>Library</a>}
          {!token && <a className="nav-link" href="#value" onClick={() => go()}>What you get</a>}
          {token && user && user.is_admin ? (
            <button className="nav-link" onClick={() => go(onAdmin)}>Dashboard</button>
          ) : null}
          {token ? (
            <React.Fragment>
              {/* Clicking the name chip opens the profile popup */}
              <button className="user-chip chip-btn" onClick={() => go(onProfile)}
                      title="Open profile">
                <Icon name="user" size={16} /> <b>{user ? user.name.split(" ")[0] : "Account"}</b>
              </button>
              <button className="btn-outline" onClick={() => go(onLogout)}>Sign Out</button>
            </React.Fragment>
          ) : (
            <button className="btn-outline" onClick={() => go(onSignIn)}>Sign In</button>
          )}
        </React.Fragment>
      );

      return (
        <div className="navbar">
          <div className="nav-inner">
            <a className="brand" href="#top" onClick={() => go()} aria-label="BookLens home">
              <span className="brand-mark"><Icon name="book" size={20} /></span>
              <span>BookLens</span>
            </a>
            {/* Desktop: links in a row (hidden on mobile by CSS) */}
            <div className="nav-links">{links}</div>
            {/* Mobile: hamburger button (hidden on desktop by CSS) */}
            <button className="hamburger" onClick={() => setMenuOpen(!menuOpen)}
                    aria-label={menuOpen ? "Close menu" : "Open menu"}
                    aria-expanded={menuOpen} aria-controls="mobile-navigation">
              <Icon name={menuOpen ? "x" : "menu"} size={24} />
            </button>
          </div>
          {/* Mobile dropdown panel */}
          {menuOpen && <div className="mobile-menu" id="mobile-navigation">{links}</div>}
        </div>
      );
    }

    function Footer({ token, onSignIn, onSignUp }) {
      return (
        <div className="footer">
          <div className="footer-inner">
            <div>
              <div className="fbrand"><Icon name="book" size={24} /> Book<span>Lens</span></div>
              <p>
                Point your camera at a book you do not know, and find out
                whether it is one for you.
              </p>
            </div>
            <div>
              <h4>Explore</h4>
              {/* One link per destination, and none that points at a section
                  the visitor cannot see. #library only exists once signed in. */}
              <a className="flink" href="#scan">Identify a book</a>
              {token && <a className="flink" href="#browse">Browse books</a>}
              {token && <a className="flink" href="#library">My library</a>}
              <a className="flink" href="#contact">Contact</a>
            </div>
            <div>
              <h4>Account</h4>
              {!token && <button className="flink" onClick={onSignIn}>Sign In</button>}
              {!token && <button className="flink" onClick={onSignUp}>Create Account</button>}

            </div>
          </div>
          <div className="footer-bottom">
            © 2026 BookLens. Final Year Project.
          </div>
        </div>
      );
    }

export { Navbar, Footer };
