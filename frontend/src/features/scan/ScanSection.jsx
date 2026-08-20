import React, { useRef, useState } from "react";
import { Icon } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";
import { ResultCard } from "./ResultViews.jsx";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

function responseMessage(response, data, fallback) {
  if (data?.error) return data.error;
  if (response.status === 401) return "Your session has expired. Sign in and try again.";
  if (response.status === 413) return "That image is too large. Choose an image under 10 MB.";
  if (response.status === 429) return "Too many requests. Wait a moment, then try again.";
  if (response.status >= 500) return "BookLens is temporarily unavailable. Please try again.";
  return fallback;
}

function TitleSearchBox({ onSearch, loading }) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [isbn, setIsbn] = useState("");

  function submit(event) {
    event.preventDefault();
    onSearch(title, author, isbn);
  }

  return (
    <form className="title-search" onSubmit={submit}>
      <p className="desc-text form-intro">
        Enter a title and, when possible, the author or ISBN. Ambiguous results will be shown for confirmation.
      </p>
      <label htmlFor="search-title">Book title</label>
      <input id="search-title" value={title} onChange={(event) => setTitle(event.target.value)}
             placeholder="e.g. The Great Gatsby" />
      <label htmlFor="search-author">Author <span className="td-muted">(optional)</span></label>
      <input id="search-author" value={author} onChange={(event) => setAuthor(event.target.value)}
             placeholder="e.g. F. Scott Fitzgerald" />
      <label htmlFor="search-isbn">ISBN <span className="td-muted">(optional)</span></label>
      <input id="search-isbn" value={isbn} onChange={(event) => setIsbn(event.target.value)}
             inputMode="numeric" placeholder="ISBN-10 or ISBN-13" />
      <button className="btn full form-submit" type="submit" disabled={loading}>
        <Icon name="search" size={17} /> {loading ? "Searching…" : "Find this book"}
      </button>
    </form>
  );
}

export function ScanSection({ token, onNeedLogin, onScanned }) {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState("scan");
  const [barcodeHint, setBarcodeHint] = useState(false);

  function clearImage() {
    setFile(null);
    setPreview("");
    if (inputRef.current) inputRef.current.value = "";
  }

  function chooseFile(selectedFile) {
    if (!selectedFile) return;
    if (!ACCEPTED_IMAGE_TYPES.has(selectedFile.type)) {
      clearImage();
      setError("Choose a JPG, PNG, or WebP image.");
      return;
    }
    if (selectedFile.size > MAX_IMAGE_BYTES) {
      clearImage();
      setError("That image is too large. Choose an image under 10 MB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = (event) => setPreview(event.target.result);
    reader.onerror = () => setError("This image could not be previewed. Try another file.");
    reader.readAsDataURL(selectedFile);
    setFile(selectedFile);
    setResult(null);
    setError("");
  }

  async function handleScan() {
    if (!file) return;
    setLoading(true);
    setLoadingMessage("Reading the cover and comparing reliable candidates…");
    setError("");
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("image", file);
      // Opt into the barcode read, but only when the reader chose that route.
      // /api/scan reads an ISBN from the image ONLY when this flag is present,
      // so without it the "Scan barcode" button would send them to photograph a
      // barcode that nothing ever looks at. It is also the only way an ISBN can
      // come off the physical object during a scan, which is what lets the card
      // ever say an edition was verified rather than supplied by a provider.
      if (barcodeHint) formData.append("allow_barcode_fallback", "1");
      const response = await authFetch("/scan", token, { method: "POST", body: formData });
      const data = await readJson(response);
      if (response.ok) {
        setResult(data);
        if (data.book) onScanned();
      } else if (response.status === 422) {
        setResult({
          status: "partial",
          book: null,
          message: data.error || "No readable title or author text was found on this image.",
          ocr: { extracted_title: "", extracted_author: "" },
          confidence: "low",
        });
      } else {
        setError(responseMessage(response, data, "The cover could not be identified."));
      }
    } catch {
      setError("BookLens could not reach the server. Check that it is running, then try again.");
    } finally {
      setLoading(false);
      setLoadingMessage("");
    }
  }

  async function handleSearch(titleText, authorText = "", isbnText = "") {
    const title = (titleText || "").trim();
    const author = (authorText || "").trim();
    const isbn = (isbnText || "").trim();
    if (!title && !isbn) {
      setError("Enter a book title or ISBN.");
      return;
    }
    setLoading(true);
    setLoadingMessage("Comparing local and external book records…");
    setError("");
    try {
      const response = await authFetch("/search-by-title", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, author, isbn }),
      });
      const data = await readJson(response);
      if (response.ok) {
        setResult(data);
        if (data.book) onScanned();
      } else {
        setError(responseMessage(response, data, "The book could not be found."));
      }
    } catch {
      setError("BookLens could not reach the server. Check that it is running, then try again.");
    } finally {
      setLoading(false);
      setLoadingMessage("");
    }
  }

  // intent lets the refusal panel say what the reader chose to do next.
  // "barcode" returns to the photo picker with the barcode instruction, because
  // BookLens reads an ISBN from a photograph of the barcode -- there is no
  // separate live scanner, and the button must not imply one.
  function reset(intent) {
    clearImage();
    setResult(null);
    setError("");
    // "type" sends them to the title form; anything else returns to the photo
    // picker, with the barcode instruction when that is what they asked for.
    setMode(intent === "type" ? "type" : "scan");
    setBarcodeHint(intent === "barcode");
  }

  function resolveCandidate(data) {
    setResult(data);
    onScanned();
  }

  return (
    <section className="container section" id="scan" aria-labelledby="scan-title">
      <div className="section-intro">
        <span className="eyebrow">Identify</span>
        <h2 className="sec-title" id="scan-title">Start with what you have</h2>
        <p className="sec-sub">A clear front-cover photo works best. You can also search by title, author, or ISBN.</p>
      </div>

      <div className="scan-wrap">
        {!token && (
          <div className="locked-note">
            <Icon name="lock" size={34} />
            <p>Sign in to identify books and keep your scan history.</p>
            <button className="btn" onClick={onNeedLogin}>Sign in to scan</button>
          </div>
        )}

        {token && result && (
          <ResultCard result={result} onReset={reset} token={token}
                      onSearchByTitle={handleSearch} onResolved={resolveCandidate}
                      onRetry={result.status === "partial" && file ? handleScan : null}
                      onLibraryChanged={onScanned} loading={loading} error={error}
                      scanImage={preview} />
        )}

        {token && !result && (
          <div className="card scan-card">
            {!loading && (
              <div className="mode-tabs" role="tablist" aria-label="Identification method">
                <button className={mode === "scan" ? "btn" : "btn-outline"} type="button"
                        role="tab" aria-selected={mode === "scan"} aria-controls="cover-panel"
                        onClick={() => { setMode("scan"); setError(""); }}>
                  <Icon name="camera" size={16} /> Cover photo
                </button>
                <button className={mode === "type" ? "btn" : "btn-outline"} type="button"
                        role="tab" aria-selected={mode === "type"} aria-controls="title-panel"
                        onClick={() => { setMode("type"); setError(""); setBarcodeHint(false); }}>
                  <Icon name="search" size={16} /> Title or ISBN
                </button>
              </div>
            )}

            {mode === "type" && !loading && <div id="title-panel"><TitleSearchBox onSearch={handleSearch} loading={loading} /></div>}

            {mode === "scan" && !loading && (
              <div id="cover-panel">
                {barcodeHint && (
                  <p className="barcode-hint" role="status">
                    Photograph the <b>barcode on the back cover</b>. An ISBN read
                    from the barcode identifies the exact edition, so it succeeds
                    on covers the title text cannot.
                  </p>
                )}
                {!preview ? (
                  <label className="dropzone" htmlFor="fileInput"
                         onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]); }}
                         onDragOver={(event) => event.preventDefault()}>
                    <div className="dz-icon"><Icon name="upload" size={40} /></div>
                    <p><b>Drop a front cover here</b> or choose an image</p>
                    <p className="small">JPG, PNG, or WebP · maximum 10 MB</p>
                  </label>
                ) : (
                  <div className="preview-panel">
                    <img src={preview} className="preview-img" alt="Selected book cover preview" />
                    <div className="preview-copy">
                      <p className="preview-label">Image ready</p>
                      <p className="small file-name">{file?.name}</p>
                      <p className="small">For the best result, keep the title visible and the front cover in frame.</p>
                      <div className="preview-actions">
                        <button className="btn" type="button" onClick={handleScan}>
                          <Icon name="search" size={17} /> Identify this book
                        </button>
                        <label className="btn-outline" htmlFor="fileInput">Change image</label>
                        <button className="text-button destructive-text" type="button" onClick={clearImage}>Remove</button>
                      </div>
                    </div>
                  </div>
                )}
                <input ref={inputRef} id="fileInput" className="visually-hidden" type="file"
                       accept="image/jpeg,image/png,image/webp" capture="environment"
                       onChange={(event) => chooseFile(event.target.files[0])} />
              </div>
            )}

            {loading && (
              <div className="processing-state" role="status" aria-live="polite">
                <div className="spinner" aria-hidden="true" />
                <h3>Identifying your book</h3>
                <p>{loadingMessage}</p>
                <small>Unclear covers take a little longer.</small>
              </div>
            )}

            {error && <div className="error-msg" role="alert">{error}</div>}
          </div>
        )}
      </div>
    </section>
  );
}
