#!/usr/bin/env node
// refcards.mjs: highlighted source reference cards from web pages and PDF pages.
//
// Each card is one PNG: a header (Reference N of M, capture date, source title, what
// the source supports), a browser-style URL bar showing the address that was actually
// loaded, the source crop with pink highlights on the exact phrases, and a footer that
// says the highlights were added and the page text is unchanged.
//
// No npm dependencies. Needs Node >= 20, Google Chrome / Chromium / Edge / Brave (run
// headless over --remote-debugging-pipe, so no debugging port is opened), and poppler
// (pdftotext, pdftoppm) for PDF sources.

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, delimiter, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const USAGE = `Usage:
  node refcards.mjs probe <spec.json> [--only KEY[,KEY...]] [--lines]
  node refcards.mjs build <spec.json> [--only KEY[,KEY...]] [--out DIR] [--allow-missing] [--keep]

probe   Load each source, apply the highlights and report what matched. Writes nothing.
        --lines also prints every text line of each PDF page with word coordinates.
build   Write one PNG card per source into the output directory (spec "out", default
        ./references next to the spec) plus refcards-report.json. A card with a missing
        highlight, a missing crop anchor or an HTTP error is not written, and the exit
        code is 1, unless --allow-missing. --keep keeps the work directory.

CHROME_PATH overrides browser discovery.`;

const HL = "rgba(229,57,53,.38)"; // highlight colour; the footer calls it pink
const PAD = 1.6; // PDF highlight padding, points
const MIN_H = 420; // web crop viewport height limits, CSS px
const MAX_H = 2800;
const FETCH_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const round1 = (v) => Math.round(v * 10) / 10;
const pngSize = (b) => [b.readUInt32BE(16), b.readUInt32BE(20)];

class UsageError extends Error {}

// ---------------------------------------------------------------------------
// Chrome over the DevTools protocol, using a pipe (fd 3 in, fd 4 out).

function findBrowser() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const apps = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  ];
  for (const p of apps) if (existsSync(p)) return p;
  const names = ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"];
  for (const dir of (process.env.PATH ?? "").split(delimiter)) {
    for (const n of names) if (dir && existsSync(join(dir, n))) return join(dir, n);
  }
  throw new Error("No Chrome, Chromium, Edge or Brave found. Install one, or set CHROME_PATH.");
}

class Chrome {
  static async launch() {
    const bin = findBrowser();
    const profile = mkdtempSync(join(tmpdir(), "refcards-profile-"));
    const args = [
      "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${profile}`,
      "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
      "--disable-background-networking", "--mute-audio", "--hide-scrollbars",
      "--force-color-profile=srgb", "--use-mock-keychain", "--password-store=basic", "about:blank",
    ];
    const proc = spawn(bin, args, { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
    const chrome = new Chrome(proc, profile);
    const version = await chrome.send("Browser.getVersion", {}, undefined, 30000);
    chrome.ua = version.userAgent.replace("HeadlessChrome/", "Chrome/");
    return chrome;
  }

  constructor(proc, profile) {
    this.proc = proc;
    this.profile = profile;
    this.nextId = 1;
    this.pending = new Map();
    this.sessions = new Map();
    this.parts = [];
    this.stderr = "";
    this.exited = false;
    proc.stderr.on("data", (d) => { this.stderr = (this.stderr + d).slice(-4000); });
    proc.stdio[3].on("error", () => {});
    proc.stdio[4].on("data", (chunk) => this.onData(chunk));
    const fail = (why) => {
      this.exited = true;
      const tail = this.stderr.trim().split("\n").slice(-3).join(" | ");
      for (const p of this.pending.values()) p.reject(new Error(`Chrome stopped (${why}). ${tail}`));
      this.pending.clear();
    };
    proc.on("exit", (code, signal) => fail(signal ?? `exit ${code}`));
    proc.on("error", (e) => fail(e.message));
    // process.exit() also runs on Ctrl-C and on errors: stop the browser and delete its profile then too.
    this.onExit = () => {
      if (!this.exited) proc.kill("SIGKILL");
      try { rmSync(profile, { recursive: true, force: true }); } catch { /* profile still locked */ }
    };
    process.once("exit", this.onExit);
  }

  onData(chunk) {
    let start = 0;
    let end;
    while ((end = chunk.indexOf(0, start)) !== -1) {
      this.parts.push(chunk.subarray(start, end));
      const raw = Buffer.concat(this.parts).toString("utf8");
      this.parts = [];
      start = end + 1;
      if (raw) this.dispatch(JSON.parse(raw));
    }
    if (start < chunk.length) this.parts.push(chunk.subarray(start));
  }

  dispatch(msg) {
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
      else p.resolve(msg.result);
      return;
    }
    this.sessions.get(msg.sessionId)?.onEvent(msg.method, msg.params);
  }

  send(method, params = {}, sessionId, timeoutMs = 90000) {
    if (this.exited) return Promise.reject(new Error("Chrome is not running"));
    const id = this.nextId++;
    const msg = { id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`${method} timed out after ${timeoutMs / 1000}s`));
      }, timeoutMs);
      this.pending.set(id, {
        method,
        resolve: (v) => { clearTimeout(timer); resolvePromise(v); },
        reject: (e) => { clearTimeout(timer); rejectPromise(e); },
      });
      this.proc.stdio[3].write(`${JSON.stringify(msg)}\0`);
    });
  }

  async newPage(colorScheme = "light") {
    const { targetId } = await this.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await this.send("Target.attachToTarget", { targetId, flatten: true });
    const page = new Page(this, targetId, sessionId);
    this.sessions.set(sessionId, page);
    await page.send("Page.enable");
    // A tab that is not in front can stop rendering, and its lifecycle events stall with it.
    await page.send("Page.bringToFront");
    await page.send("Network.enable");
    await page.send("Page.setLifecycleEventsEnabled", { enabled: true });
    await page.send("Page.setBypassCSP", { enabled: true });
    await page.send("Emulation.setUserAgentOverride", { userAgent: this.ua });
    await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: colorScheme }] });
    return page;
  }

  async close() {
    if (!this.exited) {
      const gone = new Promise((r) => this.proc.once("exit", r));
      this.send("Browser.close", {}, undefined, 5000).catch(() => {});
      await Promise.race([gone, sleep(5000)]);
      if (!this.exited) this.proc.kill("SIGKILL");
    }
    try { rmSync(this.profile, { recursive: true, force: true }); } catch { /* profile still locked */ }
    process.off("exit", this.onExit);
  }
}

class Page {
  constructor(chrome, targetId, sessionId) {
    this.chrome = chrome;
    this.targetId = targetId;
    this.sessionId = sessionId;
    this.lifecycle = [];
    this.docStatus = new Map();
  }

  send(method, params = {}, timeoutMs) {
    return this.chrome.send(method, params, this.sessionId, timeoutMs);
  }

  onEvent(method, params) {
    if (method === "Page.lifecycleEvent") this.lifecycle.push(params);
    else if (method === "Network.responseReceived" && params.type === "Document") this.docStatus.set(params.loaderId, params.response.status);
  }

  // Waits for the main frame's latest document to reach `until` (default: almost
  // network-idle, at most 2 open connections for 500 ms), then lets late scripts settle.
  // Local files wait for "load" only: there is no network to go idle.
  async goto(url, { settleMs = 2500, timeoutMs = 45000, until = "networkAlmostIdle" } = {}) {
    this.lifecycle = [];
    this.docStatus = new Map();
    const nav = await this.send("Page.navigate", { url }, timeoutMs);
    if (nav.errorText) throw new Error(`could not load ${url}: ${nav.errorText}`);
    let loader = nav.loaderId;
    let idle = false;
    const t0 = Date.now();
    while (!idle && Date.now() - t0 < timeoutMs) {
      for (const e of this.lifecycle) if (e.frameId === nav.frameId && e.name === "init") loader = e.loaderId;
      idle = this.lifecycle.some((e) => e.frameId === nav.frameId && e.loaderId === loader && e.name === until);
      if (!idle) await sleep(100);
    }
    await sleep(settleMs);
    return { status: this.docStatus.get(loader) ?? this.docStatus.get(nav.loaderId) ?? null, idle };
  }

  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error(`page script failed: ${d.exception?.description?.split("\n")[0] ?? d.text}`);
    }
    return r.result.value;
  }

  setViewport(width, height, deviceScaleFactor) {
    return this.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor, mobile: false });
  }

  async screenshot() {
    const { data } = await this.send("Page.captureScreenshot", { format: "png", fromSurface: true });
    return Buffer.from(data, "base64");
  }

  async close() {
    this.chrome.sessions.delete(this.sessionId);
    await this.chrome.send("Target.closeTarget", { targetId: this.targetId }).catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Runs inside the source page (serialized with toString), so it must not use
// anything from this module.

function pageOps(spec, mode) {
  const TAG = "rc-mark"; // a custom element, so no site CSS (mark { font-style: ... }) restyles it
  const FOLD = { "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-" };
  const INVISIBLE = /[\u00ad\u200b-\u200d\ufeff]/;
  const fold = (c) => FOLD[c] ?? c;
  const norm = (s) => Array.from(String(s).replace(/[\u00ad\u200b-\u200d\ufeff]/g, ""), fold).join("").replace(/\s+/g, " ").trim();

  const seen = new Map();
  const visible = (el) => {
    if (!el || el === document.body || el === document.documentElement) return true;
    if (seen.has(el)) return seen.get(el);
    const s = getComputedStyle(el);
    const v = s.display !== "none" && s.visibility !== "hidden" && s.visibility !== "collapse" && visible(el.parentElement);
    seen.set(el, v);
    return v;
  };

  // Visible text of root as one whitespace-collapsed string, with a map back to the
  // offsets of the concatenated text nodes.
  const scan = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let full = "";
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const p = n.parentElement;
      if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(p.tagName) || !visible(p)) continue;
      nodes.push([n, full.length]);
      full += n.data;
    }
    let text = "";
    let space = false;
    const map = [];
    for (let i = 0; i < full.length; i++) {
      const c = full[i];
      if (INVISIBLE.test(c)) continue;
      if (/\s/.test(c)) {
        if (!space) { text += " "; map.push(i); space = true; }
        continue;
      }
      text += fold(c);
      map.push(i);
      space = false;
    }
    return { nodes, text, map };
  };

  const find = (root, phrase, nth = 0) => {
    const p = norm(phrase);
    if (!p) return null;
    const sc = scan(root);
    let k = -1;
    for (let i = 0; i <= nth; i++) {
      k = sc.text.indexOf(p, k + 1);
      if (k < 0) return null;
    }
    return { nodes: sc.nodes, s: sc.map[k], e: sc.map[k + p.length - 1] + 1 };
  };

  const pick = (a) => {
    let all;
    try { all = [...document.querySelectorAll(a.sel)]; } catch { throw new Error(`invalid selector: ${a.sel}`); }
    all = all.filter((e) => visible(e) && (!a.has || norm(e.textContent).includes(norm(a.has))));
    if (a.pick === "smallest") all.sort((x, y) => x.textContent.length - y.textContent.length);
    return all[a.nth ?? 0] ?? null;
  };

  const hide = (el) => el.style.setProperty("display", "none", "important");

  if (mode === "prep") {
    const known = [
      "#onetrust-consent-sdk", "#onetrust-banner-sdk", ".onetrust-pc-dark-filter", "#CybotCookiebotDialog",
      "#usercentrics-root", "#didomi-host", ".osano-cm-window", "#truste-consent-track", ".truste_overlay",
      ".truste_box_overlay", ".contact-slider", "#readthedocs-ea-text-fixedfooter-sphinx", "[id^='readthedocs-ea']",
      "readthedocs-flyout", ".md-top", "#runllm-widget",
    ];
    let hidden = 0;
    for (const sel of [...known, ...(spec.hide ?? [])]) {
      let els;
      try { els = document.querySelectorAll(sel); } catch { throw new Error(`invalid hide selector: ${sel}`); }
      for (const el of els) if (el.style.display !== "none") { hide(el); hidden++; }
    }
    // Small fixed widgets in the lower part of the viewport: chat bubbles, "Ask AI", back-to-top.
    for (const el of document.querySelectorAll("body *")) {
      const s = getComputedStyle(el);
      if (s.position !== "fixed" || s.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.top > innerHeight * 0.45 && r.height < 260 && r.width < innerWidth * 0.6) {
        hide(el);
        hidden++;
      }
    }
    document.documentElement.style.setProperty("scroll-behavior", "auto", "important");
    return { hidden };
  }

  if (mode === "highlight") {
    if (document.querySelector(TAG)) return { results: [] };
    const paint = (el) => {
      const rules = [["background", spec.color], ["box-shadow", `0 0 0 2px ${spec.color}`], ["border-radius", "1px"], ["padding", "0"], ["margin", "0"], ["display", "inline"]];
      for (const [k, v] of rules) el.style.setProperty(k, v, "important");
    };
    const results = [];
    (spec.highlights ?? []).forEach((h, i) => {
      const scope = h.within ? ` in ${h.within.sel}${h.within.has ? ` with "${h.within.has}"` : ""}` : "";
      const label = `${h.text != null ? h.text : `cell ${h.cell}`}${scope}`;
      let root = h.within ? pick(h.within) : document.body;
      if (root && h.cell != null) root = root.querySelectorAll("th, td")[h.cell] ?? null;
      if (!root) {
        results.push({ i, label, ok: false, why: h.cell != null ? "cell not found" : "scope element not found" });
        return;
      }
      const m = find(root, h.text ?? root.textContent, h.nth ?? 0);
      if (!m) {
        results.push({ i, label, ok: false, why: "text not found" });
        return;
      }
      let marks = 0;
      for (const [n, off] of m.nodes) {
        const a = Math.max(m.s, off) - off;
        const b = Math.min(m.e, off + n.data.length) - off;
        if (b <= a || !n.data.slice(a, b).trim()) continue;
        let t = n;
        if (a > 0) t = t.splitText(a);
        if (b - a < t.data.length) t.splitText(b - a);
        const mk = document.createElement(TAG);
        paint(mk);
        t.parentNode.insertBefore(mk, t);
        mk.appendChild(t);
        marks++;
      }
      results.push(marks ? { i, label, ok: true, marks } : { i, label, ok: false, why: "matched only whitespace" });
    });
    // Code blocks: syntax highlighters split a line into token spans, so marks would show
    // seams and overlapping outlines. Drop the outline and fill same-line gaps between marks.
    for (const pre of document.querySelectorAll("pre")) {
      if (!pre.querySelector(TAG)) continue;
      for (const mk of pre.querySelectorAll(TAG)) {
        mk.style.setProperty("box-shadow", "none", "important");
        mk.style.setProperty("border-radius", "0", "important");
      }
      const walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT);
      const nodes = [];
      for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
      const markOf = (n) => n.parentElement && n.parentElement.closest(TAG);
      for (let k = 1; k < nodes.length - 1; k++) {
        const n = nodes[k];
        if (markOf(n) || n.data.includes("\n") || n.data.trim()) continue;
        const prev = markOf(nodes[k - 1]);
        if (!prev || !markOf(nodes[k + 1])) continue;
        const mk = document.createElement(TAG);
        mk.setAttribute("style", prev.getAttribute("style"));
        n.parentNode.insertBefore(mk, n);
        mk.appendChild(n);
      }
    }
    return { results };
  }

  if (mode === "region") {
    const docRect = (el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top + scrollY, bottom: r.bottom + scrollY };
    };
    const marks = [...document.querySelectorAll(TAG)].filter((m) => m.getClientRects().length);
    const anchor = (a, edge) => {
      if (!a || a.marks) {
        if (!marks.length) return null;
        const rs = marks.map(docRect);
        return edge === "top" ? Math.min(...rs.map((r) => r.top)) : Math.max(...rs.map((r) => r.bottom));
      }
      let el = null;
      if (a.sel) el = pick(a);
      else if (a.text) {
        const m = find(document.body, a.text, a.nth ?? 0);
        const hit = m && m.nodes.find(([n, off]) => off <= m.s && m.s < off + n.data.length);
        el = hit ? hit[0].parentElement : null;
        if (el && a.up) el = el.closest(a.up) ?? el;
        else {
          while (el && el.parentElement && el.parentElement !== document.body && getComputedStyle(el).display.startsWith("inline")) el = el.parentElement;
        }
      }
      if (!el) return null;
      const r = docRect(el);
      return (a.edge ?? edge) === "top" ? r.top : r.bottom;
    };
    const region = spec.region ?? {};
    const top = anchor(region.top, "top");
    const bottom = anchor(region.bottom, "bottom");
    // Height of a fixed or sticky header that covers the top of the viewport near the region.
    const keep = scrollY;
    if (top != null) window.scrollTo({ top: Math.max(0, top - 300), behavior: "instant" });
    let header = 0;
    for (const el of document.querySelectorAll("body *")) {
      const s = getComputedStyle(el);
      if ((s.position !== "fixed" && s.position !== "sticky") || s.display === "none" || s.visibility === "hidden") continue;
      const r = el.getBoundingClientRect();
      if (r.top <= 1 && r.bottom > 0 && r.width >= innerWidth * 0.5 && r.height >= 20 && r.height <= 220) header = Math.max(header, r.bottom);
    }
    window.scrollTo({ top: keep, behavior: "instant" });
    return { top, bottom, header: Math.round(header) };
  }

  throw new Error(`unknown mode ${mode}`);
}

// ---------------------------------------------------------------------------
// Web sources

async function sourceWeb(card, deck, ctx, build) {
  const chrome = await ctx.chrome();
  const page = await chrome.newPage(deck.colorScheme);
  try {
    const W = deck.width;
    await page.setViewport(W, 1000, 2);
    const nav = await page.goto(card.url, { settleMs: card.settleMs ?? 2500 });
    const base = { highlights: card.highlights, hide: card.hide ?? [], color: HL };
    const ops = (mode, region) => page.eval(`(${pageOps.toString()})(${JSON.stringify({ ...base, region })}, ${JSON.stringify(mode)})`);
    await ops("prep");
    const { results } = await ops("highlight");
    const url = await page.eval("location.href");
    const regions = card.regions ?? [card.region ?? {}];
    const shots = [];
    for (const [i, region] of regions.entries()) {
      const explicit = Boolean(region.top || region.bottom);
      const padT = region.padTop ?? (explicit ? 24 : 140);
      const padB = region.padBottom ?? (explicit ? 24 : 140);
      let r = await ops("region", region);
      const bad = anchorError(r, i, explicit);
      if (bad) { shots.push({ i, error: bad }); continue; }
      const need = Math.ceil(r.bottom - r.top + r.header + padT + padB);
      const H = Math.min(MAX_H, Math.max(MIN_H, need));
      const shot = { i, height: Math.round(r.bottom - r.top), header: r.header, viewport: H };
      if (need > MAX_H) shot.warning = `crop ${i + 1} needs ${need} px but ${MAX_H} px fit; split it into two regions`;
      if (build) {
        await page.setViewport(W, H, 2);
        await sleep(500);
        await ops("prep");
        r = await ops("region", region);
        const again = anchorError(r, i, explicit);
        if (again) { shots.push({ i, error: again }); continue; }
        const y = Math.max(0, Math.round(r.top - r.header - padT));
        await page.eval(`window.scrollTo({ top: ${y}, behavior: "instant" })`);
        await sleep(800);
        await ops("prep");
        await page.screenshot(); // warm-up: the first capture after a viewport change can come out tiled
        await sleep(300);
        shot.file = join(ctx.tmp, `${card.key}-${i}.png`);
        writeFileSync(shot.file, await page.screenshot());
      }
      shots.push(shot);
    }
    return { kind: "web", status: nav.status, url, redirected: url !== card.url, highlights: results, shots };
  } finally {
    await page.close();
  }
}

function anchorError(r, i, explicit) {
  if (!explicit && r.top == null) return `crop ${i + 1}: no highlight matched, so the default crop has nothing to frame`;
  if (r.top == null) return `crop ${i + 1}: top anchor not found`;
  if (r.bottom == null) return `crop ${i + 1}: bottom anchor not found`;
  if (r.bottom <= r.top) return `crop ${i + 1}: bottom anchor is above the top anchor`;
  return null;
}

// ---------------------------------------------------------------------------
// PDF sources

function tool(name, args) {
  const r = spawnSync(name, args.map(String), { encoding: "utf8", maxBuffer: 512 * 1024 * 1024, timeout: 180000 });
  if (r.error?.code === "ENOENT") throw new Error(`${name} not found. Install poppler (macOS: brew install poppler; Debian/Ubuntu: apt install poppler-utils).`);
  if (r.error?.code === "ETIMEDOUT") throw new Error(`${name} took longer than 180 s`);
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`${name} failed: ${String(r.stderr).trim().slice(0, 300)}`);
  return r.stdout;
}

// Returns the local file and, for downloads, the address the request ended at after redirects.
async function pdfFile(card) {
  if (card.pdfPath) {
    if (!existsSync(card.pdfPath)) throw new Error(`PDF not found: ${card.pdfPath}`);
    return { file: card.pdfPath, url: null };
  }
  const dir = join(tmpdir(), "refcards-cache");
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `${createHash("sha256").update(card.pdf).digest("hex").slice(0, 16)}.pdf`);
  const urlFile = `${file}.url`;
  if (existsSync(file) && existsSync(urlFile) && Date.now() - statSync(file).mtimeMs < 24 * 3600 * 1000) {
    return { file, url: readFileSync(urlFile, "utf8").trim() };
  }
  const res = await fetch(card.pdf, {
    headers: { "User-Agent": FETCH_UA, Accept: "application/pdf,*/*" },
    redirect: "follow",
    signal: AbortSignal.timeout(120000),
  }).catch((e) => {
    throw new Error(`PDF download failed: ${e.name === "TimeoutError" ? "no answer within 120 s" : e.message} ${card.pdf}`);
  });
  if (!res.ok) throw new Error(`PDF download failed: HTTP ${res.status} ${card.pdf}`);
  const body = await res.arrayBuffer().catch((e) => {
    throw new Error(`PDF download failed: ${e.name === "TimeoutError" ? "not finished within 120 s" : e.message} ${card.pdf}`);
  });
  const buf = Buffer.from(body);
  if (buf.subarray(0, 5).toString("latin1") !== "%PDF-") throw new Error(`not a PDF (${res.headers.get("content-type")}): ${card.pdf}`);
  const url = res.url || card.pdf;
  writeFileSync(file, buf);
  writeFileSync(urlFile, url);
  return { file, url };
}

const XML = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const decodeXml = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => {
  if (e[0] !== "#") return XML[e] ?? m;
  return String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
});
const PFOLD = { "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-" };
const WORD = /[\p{L}\p{N}]/u; // a match must not start or end inside a run of letters or digits
const pnorm = (s) => Array.from(String(s).normalize("NFKC").replace(/[\u00ad\u200b-\u200d\ufeff]/g, ""), (c) => PFOLD[c] ?? c).join("").replace(/\s+/g, " ").trim();

function pdfPage(file, pageNo) {
  const xml = tool("pdftotext", ["-f", pageNo, "-l", pageNo, "-bbox-layout", file, "-"]);
  const pm = xml.match(/<page width="([\d.]+)" height="([\d.]+)"/);
  if (!pm) throw new Error(`the PDF has no page ${pageNo}`);
  const lines = [];
  for (const m of xml.matchAll(/<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([\s\S]*?)<\/line>/g)) {
    const li = lines.length;
    const words = [...m[5].matchAll(/<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<\/word>/g)]
      .map((w) => ({ li, x0: +w[1], y0: +w[2], x1: +w[3], y1: +w[4], t: pnorm(decodeXml(w[5])) }))
      .filter((w) => w.t);
    if (words.length) lines.push({ li, x0: +m[1], y0: +m[2], x1: +m[3], y1: +m[4], words, text: words.map((w) => w.t).join(" ") });
  }
  return { w: +pm[1], h: +pm[2], lines };
}

// Whole-word phrase match over the words of the given lines, in order.
function matchWords(lines, phrase, nth = 0) {
  const words = lines.flatMap((l) => l.words);
  let joined = "";
  const owner = [];
  words.forEach((w, i) => {
    if (i) { joined += " "; owner.push(-1); }
    joined += w.t;
    for (let k = 0; k < w.t.length; k++) owner.push(i);
  });
  const p = pnorm(phrase);
  if (!p) return null;
  let hits = 0;
  for (let k = joined.indexOf(p); k !== -1; k = joined.indexOf(p, k + 1)) {
    const before = k === 0 || !WORD.test(joined[k - 1]);
    const after = k + p.length === joined.length || !WORD.test(joined[k + p.length]);
    if (!before || !after || hits++ !== nth) continue;
    const ids = new Set();
    for (let c = k; c < k + p.length; c++) if (owner[c] >= 0) ids.add(owner[c]);
    return [...ids].map((i) => words[i]);
  }
  return null;
}

// All lines that share the vertical band of the line containing the phrase (a table row).
function rowLines(pg, phrase) {
  const p = pnorm(phrase);
  const anchor = pg.lines.find((l) => ` ${l.text} `.includes(` ${p} `)) ?? pg.lines.find((l) => l.text.includes(p));
  if (!anchor) return null;
  const overlaps = (l) => Math.min(l.y1, anchor.y1) - Math.max(l.y0, anchor.y0) > 0.5 * Math.min(l.y1 - l.y0, anchor.y1 - anchor.y0);
  return pg.lines.filter(overlaps).sort((a, b) => a.x0 - b.x0);
}

// Continuation lines of a wrapped table cell: the lines right below that start at the same x.
function wrapWords(pg, words, count) {
  const out = [];
  const x0 = Math.min(...words.map((w) => w.x0));
  const x1 = Math.max(...words.map((w) => w.x1));
  const h = Math.max(...words.map((w) => w.y1 - w.y0));
  let y1 = Math.max(...words.map((w) => w.y1));
  for (let k = 0; k < count; k++) {
    const next = pg.lines.filter((l) => l.y0 >= y1 - 1 && l.y0 <= y1 + h && Math.abs(l.x0 - x0) < 3).sort((a, b) => a.y0 - b.y0)[0];
    if (!next) break;
    const ws = next.words.filter((w) => w.x0 < x1 + 3);
    out.push(...ws);
    y1 = Math.max(...ws.map((w) => w.y1));
  }
  return out;
}

// One rectangle per run of adjacent words on a line; a wide gap (another column) starts a new one.
function wordRects(words) {
  const byLine = new Map();
  for (const w of words) {
    if (!byLine.has(w.li)) byLine.set(w.li, []);
    byLine.get(w.li).push(w);
  }
  const rects = [];
  for (const ws of byLine.values()) {
    ws.sort((a, b) => a.x0 - b.x0);
    let cur = null;
    for (const w of ws) {
      if (cur && w.x0 - cur.x1 <= Math.max(6, (w.y1 - w.y0) * 0.9)) {
        cur.x1 = Math.max(cur.x1, w.x1);
        cur.y0 = Math.min(cur.y0, w.y0);
        cur.y1 = Math.max(cur.y1, w.y1);
      } else {
        if (cur) rects.push(cur);
        cur = { x0: w.x0, y0: w.y0, x1: w.x1, y1: w.y1 };
      }
    }
    if (cur) rects.push(cur);
  }
  return rects.map((r) => ({ x0: r.x0 - PAD, y0: r.y0 - PAD, x1: r.x1 + PAD, y1: r.y1 + PAD }));
}

// A box that starts inside the box above it is cut at that box's bottom, so the
// overlap does not show as a darker stripe.
function deOverlap(rects) {
  const rs = rects.map((r) => ({ ...r })).sort((a, b) => a.y0 - b.y0);
  for (let i = 0; i < rs.length; i++) {
    for (let j = 0; j < i; j++) {
      const a = rs[j];
      const b = rs[i];
      const xOverlap = Math.min(a.x1, b.x1) > Math.max(a.x0, b.x0);
      if (xOverlap && b.y0 > a.y0 && b.y0 < a.y1 && b.y1 > a.y1) b.y0 = a.y1;
    }
  }
  return rs;
}

function lineDump(pg) {
  return [...pg.lines]
    .sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)
    .map((l) => `      y ${l.y0.toFixed(1)}-${l.y1.toFixed(1)}  ${l.words.map((w) => `[${w.x0.toFixed(1)}-${w.x1.toFixed(1)}] ${w.t}`).join("  ")}`);
}

async function sourcePdf(card, deck, ctx, build, showLines) {
  const { file, url: loadedUrl } = await pdfFile(card);
  let pageNo = card.page;
  let foundOn;
  if (!pageNo) {
    const texts = card.highlights.filter((h) => h.text && !h.row).map((h) => pnorm(h.text));
    if (!texts.length) throw new Error('set "page", or add a text highlight (without "row") to find the page by');
    const pages = tool("pdftotext", ["-enc", "UTF-8", file, "-"]).split("\f").map(pnorm);
    foundOn = pages.flatMap((t, i) => (t.includes(texts[0]) ? [i + 1] : []));
    if (!foundOn.length) throw new Error(`"${texts[0]}" is not in the PDF text; set "page"`);
    pageNo = foundOn.find((n) => texts.every((t) => pages[n - 1].includes(t))) ?? foundOn[0];
  }
  const pg = pdfPage(file, pageNo);
  const rects = [];
  const results = card.highlights.map((h, i) => {
    if (h.box) {
      const [x0, y0, x1, y1] = h.box.map(Number);
      rects.push({ x0: x0 - PAD, y0: y0 - PAD, x1: x1 + PAD, y1: y1 + PAD });
      return { i, label: `box [${h.box.join(", ")}]`, ok: true, boxes: 1 };
    }
    const label = `${h.text}${h.row ? ` (row with "${h.row}")` : ""}${h.wrap ? ` + ${h.wrap} wrapped line${h.wrap > 1 ? "s" : ""}` : ""}`;
    const scope = h.row ? rowLines(pg, h.row) : pg.lines;
    if (!scope) return { i, label, ok: false, why: `no line contains "${h.row}"` };
    let words = matchWords(scope, h.text, h.nth ?? 0);
    if (!words) {
      const key = pnorm(h.text).split(" ").sort((a, b) => b.length - a.length)[0];
      const near = pg.lines.filter((l) => l.text.includes(key)).slice(0, 3).map((l) => `y ${l.y0.toFixed(1)}: ${l.text}`);
      return { i, label, ok: false, why: `not found as whole words${near.length ? `; lines with "${key}": ${near.join(" | ")}` : ""}` };
    }
    if (h.wrap) words = words.concat(wrapWords(pg, words, h.wrap));
    const rs = wordRects(words);
    rects.push(...rs);
    return { i, label, ok: true, boxes: rs.length };
  });
  const ext = rects.length ? rects : pg.lines;
  const top = cropEdge(pg, card.crop?.top, "top", () => snap(pg, Math.min(...ext.map((r) => r.y0)) - 40, "top"));
  const bottom = cropEdge(pg, card.crop?.bottom, "bottom", () => snap(pg, Math.max(...ext.map((r) => r.y1)) + 40, "bottom"));
  if (!(bottom > top)) throw new Error(`crop bottom (${round1(bottom)}) must be below crop top (${round1(top)})`);
  const loaded = loadedUrl ? loadedUrl.replace(/#.*$/, "") : null;
  const url = card.shownUrl ?? `${loaded ?? basename(file)}#page=${pageNo}`;
  const redirected = Boolean(loaded) && !card.shownUrl && loaded !== card.pdf.replace(/#.*$/, "");
  const shot = { i: 0, page: pageNo, crop: [round1(top), round1(bottom)] };
  if (build) shot.file = await renderPdfCrop(ctx, deck, card, file, pageNo, pg, deOverlap(rects), top, bottom);
  return { kind: "pdf", url, redirected, page: pageNo, foundOn, size: [round1(pg.w), round1(pg.h)], highlights: results, shots: [shot], lines: showLines ? lineDump(pg) : undefined };
}

// Moves y out of any text line it cuts: up to the line's top for a top edge, down to its bottom for a bottom edge.
function moveOut(pg, y, edge) {
  let v = Math.min(pg.h, Math.max(0, y));
  for (let moved = true; moved;) {
    moved = false;
    for (const l of pg.lines) {
      if (l.y0 < v && v < l.y1) {
        v = edge === "top" ? l.y0 : l.y1;
        moved = true;
      }
    }
  }
  return v;
}

// Adds up to 6 pt of space outside the edge, but never more than half the gap to the next line.
function pad(pg, v, edge) {
  if (edge === "top") {
    const above = Math.max(0, ...pg.lines.filter((l) => l.y1 <= v).map((l) => l.y1));
    return Math.max(0, v - Math.min(6, (v - above) / 2));
  }
  const below = Math.min(pg.h, ...pg.lines.filter((l) => l.y0 >= v).map((l) => l.y0));
  return Math.min(pg.h, v + Math.min(6, (below - v) / 2));
}

// Default and phrase crop edges: out of any line, plus a little space.
function snap(pg, y, edge) {
  return pad(pg, moveOut(pg, y, edge), edge);
}

// crop.top / crop.bottom: points (kept unless they cut a line), "footer" (bottom only) or { "text", "nth" } for the line holding a phrase.
function cropEdge(pg, v, edge, fallback) {
  if (v == null) return fallback();
  if (typeof v === "number") {
    const out = moveOut(pg, v, edge);
    return out === v ? v : pad(pg, out, edge);
  }
  if (v === "footer") return Math.min(pg.h, Math.max(...pg.lines.map((l) => l.y1)) + 16);
  const p = pnorm(v.text);
  const line = pg.lines.filter((l) => hasWords(l.text, p)).sort((a, b) => a.y0 - b.y0)[v.nth ?? 0];
  if (!line) throw new Error(`crop.${edge}: no line on this page has the words "${v.text}"`);
  return snap(pg, edge === "top" ? line.y0 : line.y1, edge);
}

// True when `p` occurs in `text` and neither end is inside a run of letters or digits.
function hasWords(text, p) {
  for (let k = text.indexOf(p); k !== -1; k = text.indexOf(p, k + 1)) {
    const before = k === 0 || !WORD.test(text[k - 1]);
    const after = k + p.length === text.length || !WORD.test(text[k + p.length]);
    if (before && after) return true;
  }
  return false;
}

async function renderPdfCrop(ctx, deck, card, file, pageNo, pg, rects, top, bottom) {
  const W = deck.width;
  const S = Math.min(2, (W - 80) / pg.w); // CSS px per point
  const stem = join(ctx.tmp, `${card.key}-page`);
  tool("pdftoppm", ["-f", pageNo, "-l", pageNo, "-r", Math.round(72 * S * 2), "-png", "-singlefile", file, stem]);
  const cropH = Math.round((bottom - top) * S);
  const f = (v) => v.toFixed(1);
  const boxes = rects.map((r) => `<div class="hl" style="left:${f(r.x0 * S)}px;top:${f(r.y0 * S)}px;width:${f((r.x1 - r.x0) * S)}px;height:${f((r.y1 - r.y0) * S)}px"></div>`).join("");
  const html = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#525659}
#view{width:${W}px;height:${cropH}px;overflow:hidden;position:relative}
#pg{position:absolute;left:${f((W - pg.w * S) / 2)}px;top:${f(-top * S)}px;width:${f(pg.w * S)}px;height:${f(pg.h * S)}px;background:#fff;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 4px 14px rgba(0,0,0,.45)}
#pg img{display:block;width:100%;height:100%}
.hl{position:absolute;background:${HL};mix-blend-mode:multiply;border-radius:2px}
</style><div id="view"><div id="pg"><img src="${basename(stem)}.png" alt="">${boxes}</div></div>`;
  const viewer = join(ctx.tmp, `${card.key}-view.html`);
  writeFileSync(viewer, html);
  const page = await (await ctx.chrome()).newPage("light");
  try {
    await page.setViewport(W, cropH, 2);
    await page.goto(pathToFileURL(viewer).href, { settleMs: 150, timeoutMs: 20000, until: "load" });
    await page.eval("Promise.all([...document.images].map((i) => i.decode()))");
    await page.screenshot(); // warm-up
    await sleep(200);
    const out = join(ctx.tmp, `${card.key}-0.png`);
    writeFileSync(out, await page.screenshot());
    return out;
  } finally {
    await page.close();
  }
}

// ---------------------------------------------------------------------------
// Cards

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function cardHtml({ n, total, deck, card, url, crops, pdf }) {
  const m = /^([a-z][a-z0-9+.-]*:\/\/)([^/?#]*)(.*)$/i.exec(url);
  const bar = m ? `<span class="host">${esc(m[2])}</span><span class="path">${esc(m[3])}</span>` : `<span class="host">${esc(url)}</span>`;
  const lock = '<svg width="13" height="15" viewBox="0 0 13 15" aria-hidden="true"><rect x="1" y="6.5" width="11" height="8" rx="1.5" fill="#bdc1c6"/><path d="M3.5 6.5V4.5a3 3 0 0 1 6 0v2" fill="none" stroke="#bdc1c6" stroke-width="1.6"/></svg>';
  const body = crops.map((file, i) => `${i ? '<div class="gap">⋯ same page, further down ⋯</div>' : ""}<img src="${esc(basename(file))}" alt="">`).join("\n");
  const foot = `${pdf ? "Page rendered from the PDF. " : ""}Pink highlights were added for this reference; the page text is unchanged.`;
  const W = deck.width;
  return `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#fff}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1f2328}
#card{width:${W}px;border:1px solid #d0d7de}
.head{padding:20px 26px 18px;background:#f6f8fa;border-bottom:1px solid #d0d7de}
.kick{font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#57606a}
.title{font-size:23px;font-weight:700;line-height:1.3;margin-top:7px}
.sup{font-size:18px;line-height:1.45;margin-top:10px}
.sup b{color:#b42318}
.bar{background:#35363a;padding:8px 12px}
.omni{display:flex;align-items:center;gap:10px;background:#202124;border-radius:18px;padding:7px 16px;font-size:14px;line-height:1.35;word-break:break-all}
.omni svg{flex:none}
.host{color:#e8eaed}.path{color:#9aa0a6}
img{display:block;width:${W}px}
.gap{background:#eaeef2;color:#57606a;font-size:14px;text-align:center;padding:7px;border-top:1px dashed #afb8c1;border-bottom:1px dashed #afb8c1}
.foot{display:flex;align-items:center;gap:10px;padding:10px 26px;background:#f6f8fa;border-top:1px solid #d0d7de;font-size:13px;color:#57606a}
.sw{display:inline-block;width:28px;height:13px;background:${HL};border-radius:2px}
</style><div id="card">
<div class="head"><div class="kick">Reference ${n} of ${total} · captured ${esc(deck.date)}</div>
<div class="title">${esc(card.title)}</div>
<div class="sup"><b>${esc(deck.label)}</b> ${esc(card.supports)}</div></div>
<div class="bar"><div class="omni">${lock}<div>${bar}</div></div></div>
${body}
<div class="foot"><span class="sw"></span>${esc(foot)}</div>
</div>`;
}

async function composeCard(ctx, deck, card, total, result, outFile) {
  const crops = result.shots.map((s) => s.file);
  const htmlFile = join(ctx.tmp, `${card.key}-card.html`);
  writeFileSync(htmlFile, cardHtml({ n: card.n, total, deck, card, url: result.url, crops, pdf: result.kind === "pdf" }));
  const page = await (await ctx.chrome()).newPage("light");
  try {
    const width = deck.width + 2; // card border
    await page.setViewport(width, 600, 2);
    await page.goto(pathToFileURL(htmlFile).href, { settleMs: 150, timeoutMs: 20000, until: "load" });
    await page.eval("Promise.all([...document.images].map((i) => i.decode()))");
    const height = await page.eval('Math.ceil(document.getElementById("card").getBoundingClientRect().height)');
    await page.setViewport(width, height, 2);
    await sleep(300);
    await page.screenshot(); // warm-up
    await sleep(200);
    const png = await page.screenshot();
    mkdirSync(dirname(outFile), { recursive: true });
    writeFileSync(outFile, png);
    return pngSize(png);
  } finally {
    await page.close();
  }
}

// ---------------------------------------------------------------------------
// Spec, CLI, output

function captureDate(iso) {
  const d = iso ? new Date(`${iso}T00:00:00`) : new Date();
  if (Number.isNaN(d.getTime())) throw new UsageError(`"captured" must be YYYY-MM-DD, got ${iso}`);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

function loadSpec(specPath) {
  const abs = resolve(specPath);
  let spec;
  try { spec = JSON.parse(readFileSync(abs, "utf8")); } catch (e) { throw new UsageError(`cannot read spec ${specPath}: ${e.message}`); }
  const base = dirname(abs);
  if (!Array.isArray(spec.cards) || !spec.cards.length) throw new UsageError('spec needs a non-empty "cards" array');
  const keys = new Set();
  spec.cards.forEach((c, i) => {
    const at = `cards[${i}]`;
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(c.key ?? "")) throw new UsageError(`${at}.key must be a file-name-safe slug`);
    if (keys.has(c.key)) throw new UsageError(`${at}.key "${c.key}" is used twice`);
    keys.add(c.key);
    if (!c.title || !c.supports) throw new UsageError(`${at} needs "title" and "supports"`);
    if (Boolean(c.url) === Boolean(c.pdf)) throw new UsageError(`${at} needs exactly one of "url" or "pdf"`);
    if (!Array.isArray(c.highlights) || !c.highlights.length) throw new UsageError(`${at} needs a non-empty "highlights" array`);
    if (!c.pdf && c.crop) throw new UsageError(`${at}.crop is for PDFs; web pages use "region" or "regions"`);
    if (c.pdf && (c.region || c.regions)) throw new UsageError(`${at}: "region" is for web pages; PDFs use "crop"`);
    for (const edge of ["top", "bottom"]) {
      const v = c.crop?.[edge];
      const ok = v == null || typeof v === "number" || (edge === "bottom" && v === "footer") || (typeof v?.text === "string" && v.text.trim());
      if (!ok) throw new UsageError(`${at}.crop.${edge} must be PDF points${edge === "bottom" ? ', "footer"' : ""} or { "text": "..." }`);
    }
    c.highlights.forEach((h, j) => {
      const hat = `${at}.highlights[${j}]`;
      if (c.pdf) {
        if (h.within || h.cell != null) throw new UsageError(`${hat}: "within" and "cell" are for web pages; PDFs use "row", "wrap" or "box"`);
        if (h.box && (!Array.isArray(h.box) || h.box.length !== 4)) throw new UsageError(`${hat}.box must be [x0, y0, x1, y1] in PDF points`);
        if (!h.text && !h.box) throw new UsageError(`${hat} needs "text" or "box"`);
      } else {
        if (h.row || h.wrap || h.box) throw new UsageError(`${hat}: "row", "wrap" and "box" are for PDFs; web pages use "within" and "cell"`);
        if (h.text == null && h.cell == null) throw new UsageError(`${hat} needs "text" or "cell"`);
      }
    });
    if (c.pdf && !/^https?:\/\//i.test(c.pdf)) c.pdfPath = resolve(base, c.pdf);
  });
  return { spec, base };
}

function parseArgs(argv) {
  const [cmd, specPath, ...rest] = argv;
  if (!cmd || cmd === "-h" || cmd === "--help" || cmd === "help") return null;
  if (cmd !== "probe" && cmd !== "build") throw new UsageError(`unknown command "${cmd}"`);
  if (!specPath) throw new UsageError("missing <spec.json>");
  const opts = { cmd, specPath, only: null, out: null, allowMissing: false, keep: false, lines: false };
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "--only") opts.only = new Set(String(rest[++i] ?? "").split(",").map((s) => s.trim()).filter(Boolean));
    else if (a === "--out") opts.out = rest[++i];
    else if (a === "--allow-missing") opts.allowMissing = true;
    else if (a === "--keep") opts.keep = true;
    else if (a === "--lines") opts.lines = true;
    else throw new UsageError(`unknown option ${a}`);
  }
  return opts;
}

function report(head, r) {
  let where = `web  HTTP ${r.status ?? "?"}`;
  if (r.kind === "pdf") where = `pdf  page ${r.page}${r.foundOn ? " (found by text)" : ""} · ${Math.round(r.size[0])}×${Math.round(r.size[1])} pt`;
  console.log(`${head}  ${where}`);
  console.log(`      ${r.url}${r.redirected ? "   (redirected: the URL bar shows this address)" : ""}`);
  const others = r.foundOn?.filter((p) => p !== r.page) ?? [];
  if (others.length) console.log(`  ! the first text highlight is also on page ${others.join(", ")}; set "page" to use another page`);
  if (r.kind === "web" && r.status >= 400) console.log(`  ✗ the server answered HTTP ${r.status}`);
  for (const h of r.highlights) {
    const n = h.marks ?? h.boxes;
    const unit = h.marks != null ? (n === 1 ? "piece" : "pieces") : n === 1 ? "box" : "boxes";
    console.log(h.ok ? `  ✓ ${h.label}  (${n} ${unit})` : `  ✗ ${h.label}  -- ${h.why}`);
  }
  for (const s of r.shots) {
    if (s.error) console.log(`  ✗ ${s.error}`);
    if (s.warning) console.log(`  ! ${s.warning}`);
    if (s.crop) console.log(`      crop y ${s.crop[0]}-${s.crop[1]} pt`);
  }
  if (r.lines) {
    console.log(`      text lines on page ${r.page}, top to bottom, in PDF points from the top-left:  y top-bottom  [x left-right] word ...`);
    console.log(r.lines.join("\n"));
  }
}

async function run(opts) {
  const { spec, base } = loadSpec(opts.specPath);
  const total = spec.cards.length;
  const deck = {
    width: Number(spec.width ?? 1366),
    colorScheme: spec.colorScheme ?? "light",
    date: captureDate(spec.captured),
    label: spec.supportsLabel ?? "Supports:",
  };
  if (opts.only) for (const k of opts.only) if (!spec.cards.some((c) => c.key === k)) throw new UsageError(`--only: no card with key "${k}"`);
  const cards = spec.cards.map((c, i) => ({ ...c, n: i + 1 })).filter((c) => !opts.only || opts.only.has(c.key));
  const outDir = opts.out ? resolve(opts.out) : resolve(base, spec.out ?? "references");
  const build = opts.cmd === "build";
  const tmp = mkdtempSync(join(tmpdir(), "refcards-"));
  let chrome = null;
  const ctx = { tmp, chrome: async () => (chrome ??= await Chrome.launch()) };
  const cleanup = () => {
    if (chrome && !chrome.exited) chrome.proc.kill("SIGKILL");
    if (!opts.keep) rmSync(tmp, { recursive: true, force: true });
  };
  const onSignal = () => { cleanup(); process.exit(130); };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  const rows = [];
  let failed = 0;
  try {
    for (const card of cards) {
      const head = `[${card.n}/${total}] ${card.key}`;
      try {
        const r = card.pdf ? await sourcePdf(card, deck, ctx, build, opts.lines) : await sourceWeb(card, deck, ctx, build);
        report(head, r);
        const problems = r.highlights.filter((h) => !h.ok).length + r.shots.filter((s) => s.error).length + (r.status >= 400 ? 1 : 0);
        const row = { key: card.key, n: card.n, kind: r.kind, source: card.url ?? card.pdf, url: r.url, status: r.status, page: r.page, highlights: r.highlights, crops: r.shots.map(({ file, ...s }) => s) };
        if (problems) failed++;
        if (build && (!problems || opts.allowMissing) && r.shots.some((s) => s.file)) {
          r.shots = r.shots.filter((s) => s.file);
          const outFile = join(outDir, `${card.key}.png`);
          const [w, h] = await composeCard(ctx, deck, card, total, r, outFile);
          row.output = outFile;
          console.log(`  → ${outFile}  (${w}×${h})`);
        } else if (build) {
          console.log("  card NOT written: fix the ✗ lines above, or pass --allow-missing");
        }
        rows.push(row);
      } catch (e) {
        failed++;
        console.log(`${head}  ERROR ${e.message}`);
        rows.push({ key: card.key, n: card.n, error: e.message });
      }
    }
  } finally {
    if (chrome) await chrome.close();
    if (opts.keep) console.log(`work directory kept: ${tmp}`);
    cleanup();
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
  }
  if (build) {
    mkdirSync(outDir, { recursive: true });
    const file = join(outDir, "refcards-report.json");
    writeFileSync(file, `${JSON.stringify({ spec: resolve(opts.specPath), captured: deck.date, cards: rows }, null, 2)}\n`);
    console.log(`report: ${file}`);
  }
  console.log(failed ? `${failed} of ${cards.length} source(s) need attention` : `all ${cards.length} source(s) OK`);
  return failed ? 1 : 0;
}

try {
  const opts = parseArgs(process.argv.slice(2));
  if (!opts) {
    console.log(USAGE);
  } else {
    process.exitCode = await run(opts);
  }
} catch (e) {
  console.error(`refcards: ${e.message}`);
  if (e instanceof UsageError) console.error(`\n${USAGE}`);
  process.exitCode = 2;
}
