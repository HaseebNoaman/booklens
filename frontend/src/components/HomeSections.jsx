import React, { useState } from "react";
import { Icon } from "./ui.jsx";
import { postJson } from "../services/api.js";

    function Hero({ token, onSignUp }) {
      return (
        <section className="hero" id="top">
          <div className="container hero-grid">
            <div className="hero-copy">
              <h1>
                Know the book<br />in front of you.
              </h1>
              <p className="lead">
                Point your camera at a cover. BookLens tells you what it is, and
                whether it is anything like the books you already read.
              </p>
              <div className="hero-actions">
                {token ? (
                  <a className="btn" href="#scan"><Icon name="camera" size={18} /> Identify a book</a>
                ) : (
                  <button className="btn" onClick={onSignUp}>Create free account</button>
                )}
                {/* The section this points at only renders for signed-out
                    visitors, so the link must disappear with it. */}
                {!token && (
                  <a className="text-link" href="#value">What you get <span aria-hidden="true">&#8594;</span></a>
                )}
              </div>
            </div>

            {/* A single honest line instead of a pipeline diagram. The old panel
                listed OCR, ranking and catalogue lookup -- true, and of no use
                whatsoever to someone deciding whether to open the app. */}
            <aside className="hero-aside" aria-label="What BookLens is for">
              <p className="hero-aside-quote">
                &ldquo;Is this one for me?&rdquo;
              </p>
              <p className="hero-aside-body">
                The question you ask standing in front of a shelf. BookLens
                answers it from the books you have actually read &mdash; and says
                so plainly when it cannot.
              </p>
            </aside>
          </div>
        </section>
      );
    }

    // What the reader gets, in the reader's terms.
    //
    // This replaced a four-step explanation of the identification pipeline --
    // "OCR extracts likely title and author text", "the verified 250-book
    // catalogue is queried before any external provider". Accurate, and written
    // for someone marking the project rather than someone using it. How a thing
    // works is not a reason to use it.
    function HowItWorks() {
      const points = [
        {
          n: "01",
          title: "No typing",
          body: "Photograph the cover. Titles you cannot spell, cannot read, or " +
                "cannot be bothered to type all work the same way.",
        },
        {
          n: "02",
          title: "Is it your kind of book?",
          body: "Checked against what you have actually finished and saved — " +
                "and it names those books, so you can judge the answer yourself.",
        },
        {
          n: "03",
          title: "It admits when it is unsure",
          body: "A worn cover in bad light will not produce a confident guess. " +
                "You get an honest “not sure” and a faster way to check.",
        },
      ];
      return (
        <section className="section value-section" id="value">
          <div className="container">
            <div className="value-head">
              <h2 className="sec-title">Three things it does for you</h2>
            </div>
            <div className="value-grid">
              {points.map((point) => (
                <article className="value-item" key={point.n}>
                  <span className="value-num">{point.n}</span>
                  <h3>{point.title}</h3>
                  <p>{point.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
      );
    }

    function ContactSection() {
      const [name, setName] = useState("");
      const [email, setEmail] = useState("");
      const [subject, setSubject] = useState("");
      const [message, setMessage] = useState("");
      const [error, setError] = useState("");
      const [success, setSuccess] = useState("");
      const [busy, setBusy] = useState(false);

      async function submit(e) {
        e.preventDefault();
        setError(""); setSuccess(""); setBusy(true);
        try {
          const { ok, data } = await postJson("/contact", { name, email, subject, message });
          if (ok) {
            setSuccess(data.message);
            setName(""); setEmail(""); setSubject(""); setMessage("");
          } else {
            setError(data.error || "Something went wrong");
          }
        } catch (e) {
          setError("Could not reach the server. Please try again.");
        } finally {
          setBusy(false);
        }
      }

      return (
        <section className="container section" id="contact">
          <div className="sec-center">
            <div className="sec-kicker">Contact</div>
            <h2 className="sec-title">We'd love to hear from you</h2>
            <p className="sec-sub">Questions, feedback, or identification issues? Send us a note.</p>
          </div>

          <div className="contact-grid">
            <div className="card">
              <h3 className="contact-heading">Get in touch</h3>
              <div className="info-line">
                <span className="i-icon"><Icon name="mail" /></span>
                <div><b>Email</b><span>support@booklens.app</span></div>
              </div>
              <div className="info-line">
                <span className="i-icon"><Icon name="clock" /></span>
                <div><b>Response time</b><span>We usually reply within 24 hours</span></div>
              </div>
              <div className="info-line">
                <span className="i-icon"><Icon name="globe" /></span>
                <div><b>Not sure about a result?</b><span>Send the title and we will look into it</span></div>
              </div>
            </div>

            <form className="card" onSubmit={submit}>
              <label htmlFor="contact-name">Your name</label>
              <input id="contact-name" value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="Full name" autoComplete="name" required />
              <label htmlFor="contact-email">Email</label>
              <input id="contact-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     placeholder="you@example.com" autoComplete="email" required />
              <label htmlFor="contact-subject">Subject</label>
              <input id="contact-subject" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="What is it about?" />
              <label htmlFor="contact-message">Message</label>
              <textarea id="contact-message" value={message} onChange={(e) => setMessage(e.target.value)}
                        placeholder="Write your message here..." required />
              <br /><br />
              <button className="btn full" type="submit" disabled={busy}>
                <Icon name="mail" size={17} /> {busy ? "Sending..." : "Send Message"}
              </button>
              {error && <div className="error-msg" role="alert">{error}</div>}
              {success && <div className="success-msg" role="status">{success}</div>}
            </form>
          </div>
        </section>
      );
    }

export { Hero, HowItWorks, ContactSection };
