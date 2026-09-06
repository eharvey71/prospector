"use client";
// Install page for the browser extension.
//
// Chrome only allows one-click installs from the Chrome Web Store, so this
// serves the extension as a download plus the four manual steps, and
// reports live whether it's already installed (bridge.js announces itself
// on this domain).

import { useEffect, useState } from "react";
import { Nav } from "../ui";

const ZIP = "/prospector-extension.zip";

export default function ExtensionPage() {
  const [installed, setInstalled] = useState(null);   // null = still checking

  useEffect(() => {
    const onMsg = (ev) => {
      if (ev.source === window && ev.data?.type === "JOB_ENGINE_EXTENSION_PRESENT") {
        setInstalled(ev.data.version || true);
      }
    };
    window.addEventListener("message", onMsg);
    // The content script may load before or after this page's JS — ping a
    // few times, then conclude it isn't there.
    const ping = () => window.postMessage({ type: "JOB_ENGINE_PING" }, "*");
    ping();
    const iv = setInterval(ping, 300);
    const done = setTimeout(() => {
      clearInterval(iv);
      setInstalled((v) => (v === null ? false : v));
    }, 1500);
    return () => {
      window.removeEventListener("message", onMsg);
      clearInterval(iv);
      clearTimeout(done);
    };
  }, []);

  return (
    <main className="container">
      <Nav active="/extension" />
      <h1>Browser extension</h1>
      <p style={{ color: "var(--muted)" }}>
        Two jobs: it fills application forms with your prepared answers, and
        its toolbar button sends any job page you&apos;re browsing into your
        queue. It has no login and makes no network calls of its own — this
        app hands it one job at a time.
      </p>

      {installed === null && <p className="hint">Checking…</p>}
      {installed === false && (
        <div className="banner" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>
          Not responding in this browser. Either it isn&apos;t installed, or
          it&apos;s an older copy — versions before 0.2.0 can&apos;t report
          themselves. If you already have it loaded, download below, replace
          the folder&apos;s contents, and hit the reload icon (↻) on the Job
          Engine card in <code>chrome://extensions</code>.
        </div>
      )}
      {installed && (
        <div className="banner" style={{ borderColor: "var(--ok)", color: "var(--ok)" }}>
          ✓ Installed{typeof installed === "string" ? ` (version ${installed})` : ""} and
          talking to this app. Re-run these steps only to update it.
        </div>
      )}

      <details className="panel tint-blue" open>
        <summary>Install it (about a minute)</summary>
        <div className="panelbody">
          <p className="hint" style={{ marginTop: 8 }}>
            Chrome only allows one-click installs from its Web Store, so this
            one installs from a folder — the same way developers load
            extensions. It stays installed and keeps working.
          </p>
          <ol style={{ lineHeight: 2, paddingLeft: 20 }}>
            <li>
              <a className="btn-primary" href={ZIP} download
                 style={{ textDecoration: "none" }}>
                Download the extension
              </a>
            </li>
            <li>Unzip it, and keep the folder somewhere permanent — deleting
                it uninstalls the extension.</li>
            <li>Open <code>chrome://extensions</code> and turn on
                <strong> Developer mode</strong> (top right).</li>
            <li>Click <strong>Load unpacked</strong> and choose the unzipped
                folder.</li>
          </ol>
          <p className="hint">
            Then pin it: click the puzzle-piece icon in Chrome&apos;s toolbar
            and pin &quot;Prospector Autofill&quot; so its button is always
            visible. Refresh this page and the banner above should turn green.
          </p>
        </div>
      </details>

      <details className="panel tint-green">
        <summary>Updating to a new version</summary>
        <div className="panelbody">
          <p className="hint" style={{ marginTop: 8 }}>
            Download again, replace the contents of the same folder, then open
            <code> chrome://extensions</code> and click the reload icon (↻) on
            the Prospector card. Check the version in the banner above to
            confirm.
          </p>
        </div>
      </details>

      <details className="panel tint-purple">
        <summary>What it can and can&apos;t do</summary>
        <div className="panelbody">
          <ul style={{ lineHeight: 1.9, paddingLeft: 20 }}>
            <li><strong>The toolbar button does whichever thing fits:</strong> when
                a job is loaded (blue dot on the icon) it shows the autofill
                panel on the page you&apos;re on — use it when you&apos;ve clicked
                through from LinkedIn to a careers page to the actual form and
                the panel didn&apos;t follow. With no job loaded, it sends the
                current page to your queue. Right-click the page to pick either
                one explicitly.</li>
            <li><strong>Fills</strong> text fields and simple dropdowns, including
                forms embedded in a frame inside a company&apos;s page.</li>
            <li><strong>Copies</strong> — everything else gets a copy button:
                custom dropdown widgets and the questions only you can answer.</li>
            <li><strong>Can&apos;t attach your resume.</strong> Browsers don&apos;t let
                extensions choose files, so the panel gives you a download
                link and you attach it yourself.</li>
            <li><strong>Never submits anything.</strong> You click the
                page&apos;s own Submit button, always.</li>
          </ul>
        </div>
      </details>
    </main>
  );
}
