// Glue between xterm.js and the Python WKWebView host.
//
// Three channels of webkit.messageHandlers (registered Python-side):
//   - input  : strings the user typed; forwarded to claude's PTY stdin.
//   - resize : {rows, cols} after FitAddon recomputes; drives TIOCSWINSZ.
//   - ready  : signalled once xterm.js is mounted so Python can flush
//              any buffered output from the very first PTY reads.
//
// Python pushes terminal output by calling
//   window.feedBytes('<base64>')
// via evaluateJavaScript:. Base64 is the safe encoding for arbitrary bytes
// (including ANSI escapes) through a JS string literal.

(function () {
  "use strict";

  const term = new Terminal({
    fontFamily: "Menlo, Monaco, 'Courier New', monospace",
    fontSize: 13,
    cursorBlink: true,
    scrollback: 5000,
    // Transparent background so the panel's HUD blur shows through.
    // ``allowTransparency: true`` is xterm.js's switch for letting
    // alpha-zero theme.background actually compose with what's behind
    // the canvas; without it the default opaque buffer is used and the
    // CSS transparency above is wasted.
    allowTransparency: true,
    theme: {
      background: "rgba(0, 0, 0, 0)",
      foreground: "#e6e6e6",
      cursor: "#e6e6e6",
    },
    allowProposedApi: true,
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(document.getElementById("term"));

  // Expose on window for in-page debugging from the WebKit inspector
  // (e.g. ``window.term.clear()`` while diagnosing rendering issues).
  // Python doesn't call this directly — output goes through
  // ``window.feedBytes`` so the base64 → Uint8Array conversion happens
  // here rather than in Python.
  window.term = term;

  // Python feeds output as base64-encoded raw PTY bytes. xterm.js's
  // ``term.write`` accepts Uint8Array for raw-byte input and treats it
  // as UTF-8 — passing the binary string from ``atob`` directly makes
  // xterm.js misread multi-byte UTF-8 sequences as individual Latin-1
  // codepoints (the box-drawing chars in claude's TUI become "â"
  // smears). The conversion is cheap; do it in JS instead of Python so
  // we still pay the smaller payload over the bridge.
  window.feedBytes = function (b64) {
    const bin = atob(b64);
    const len = bin.length;
    const arr = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      arr[i] = bin.charCodeAt(i);
    }
    term.write(arr);
  };

  function postInput(data) {
    try {
      window.webkit.messageHandlers.input.postMessage(data);
    } catch (e) {
      // Standalone-browser preview (no webkit bridge): echo so we can
      // visually confirm xterm works without Python wired up.
      term.write(data);
    }
  }

  function postResize() {
    try {
      fit.fit();
    } catch (e) {
      // Layout can be momentarily zero-sized during open; ignore.
      return;
    }
    const dims = { rows: term.rows, cols: term.cols };
    try {
      window.webkit.messageHandlers.resize.postMessage(dims);
    } catch (e) {
      // No bridge in standalone preview — nothing to do.
    }
  }

  term.onData(postInput);

  // Tell Python the JS side is ready to receive output. Without this
  // signal, claude's startup banner (printed within the first few ms of
  // spawn) can race ahead of term.open() and end up dropped.
  function postReady() {
    try {
      window.webkit.messageHandlers.ready.postMessage(null);
    } catch (e) {
      // Standalone-browser preview — nothing to coordinate with.
    }
  }

  // Initial fit + an additional one after first paint to catch the case
  // where the browser still reports zero dims on the first synchronous tick.
  postResize();
  requestAnimationFrame(() => {
    postResize();
    postReady();
  });

  // ResizeObserver covers panel resizes initiated by macOS (e.g. screen
  // change), but Tokenmon's panel is currently fixed-size so this is mostly
  // belt-and-suspenders for the future.
  const ro = new ResizeObserver(postResize);
  ro.observe(document.getElementById("term"));

  // Focus on click anywhere — xterm only captures keystrokes once focused.
  document.addEventListener("mousedown", () => term.focus());
  term.focus();
})();
