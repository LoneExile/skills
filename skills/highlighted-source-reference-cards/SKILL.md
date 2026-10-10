---
name: highlighted-source-reference-cards
description: "Make strong-reference screenshots of web pages and PDF pages: browser-style cards with the URL bar, capture date and pink highlights on the exact lines that back a number. Use when a proposal, bid, BOM, sizing sheet or architecture diagram needs evidence for its figures, or when asked to highlight a source, screenshot vendor documentation as proof, or cite a spec table."
---

# highlighted-source-reference-cards

Turn each source behind a claim into one PNG card:

- a header: **Reference N of M**, the capture date, the source title, and what the source supports;
- a browser-style URL bar with the address that was actually loaded (after redirects);
- the page crop with **pink highlights on the exact phrases**;
- a footer: the highlights were added and the page text is unchanged.

The bundled `refcards.mjs` has no npm dependencies. It drives headless Chrome over a pipe (no debugging port) and renders PDF pages with poppler. Cards are 2736 px wide (1366 CSS px at 2×).

## When to use

- Figures in a bid, proposal, BOM, sizing sheet or diagram need vendor evidence: manuals, datasheets, docs pages, PDF tables.
- The user shows screenshots with highlighted lines and wants the same for other sources.

## When not to use

- A link or a short quote is enough.
- The page needs a login, or the text is inside an iframe or shadow DOM. The script can't highlight those; take the screenshot by hand.

## Prerequisites

- Node 20 or later.
- Google Chrome, Chromium, Edge or Brave. Set `CHROME_PATH` if the browser isn't found.
- For PDFs: poppler (`brew install poppler`, or `apt install poppler-utils`).

## Workflow

1. **Check every number before you make its card.** Re-derive it from the design, with the assumptions it rests on (for example 3 copies vs erasure coding 2+2). A card proves a phrase on a page, not your arithmetic.
2. **Read each source and copy the exact phrases** that back the number. Write `supports` so it claims only what the highlighted text shows.
3. **Write a spec.** Start from `example.json` in this skill's folder: two web pages and a PDF table.
4. **Probe until every line shows ✓:**
   ```bash
   node <skill-dir>/refcards.mjs probe spec.json
   node <skill-dir>/refcards.mjs probe spec.json --only KEY           # one card while you fix it
   node <skill-dir>/refcards.mjs probe spec.json --only KEY --lines   # PDF: print the page's text lines
   ```
   A web page takes about 10-40 s (load, late scripts, then the checks). A PDF page takes about a second once downloaded. Use `--only` while you fix one card.
5. **Build:**
   ```bash
   node <skill-dir>/refcards.mjs build spec.json --out ./references
   ```
   This writes one `<key>.png` per card and `refcards-report.json`. A card with a ✗ is not written, and the exit code is 1. `--only` works here too. A build renders every card in Chrome, PDFs too, so it is slower than a probe: the three example cards took 53-98 s on a busy machine.
6. **View every PNG before you send it.** Check that the crop shows enough context, the highlights sit on the right words, and nothing in the crop contradicts the claim. If the page carries a caveat (for example, a table written for servers with more GPUs), tell the user; don't crop it out.

## Reading the probe output

```text
[3/3] ref-03-dell-r7725  pdf  page 85 (found by text) · 595×842 pt
      https://www.delltechnologies.com/assetlink/doc/...pdf#page=85   (redirected: the URL bar shows this address)
  ! the first text highlight is also on page 87; set "page" to use another page
  ✓ RTX Pro 6000 and H200 NVL will operate with a power cap of 450W, not 600W  (1 box)
  ✗ 450 (row with "NVIDIA RTX Pro 6000")  -- no line contains "NVIDIA RTX Pro 6000"
      crop y 368.8-486.8 pt
```

- **✓** The phrase was found once (`nth` picks a later match). The count is how many pieces the highlight is drawn in: one per text run on a web page (a phrase across a link or `code` span has several), one per line or cell in a PDF. More than one is normal.
- **✗** Not found, with the reason. The card is not written.
- **!** A warning: the same PDF text on other pages, or a web crop taller than one capture.
- **redirected** The URL bar shows the address the browser or the PDF download ended at, not the one in the spec.
- **crop y** The part of the PDF page the card shows, in points from the top.
- **`--lines`** Every text line of the PDF page, top to bottom: `y top-bottom`, then `[x left-right] word` for each word. Use the y values, or a phrase, for `crop`, and the x/y values for `box`.

## Spec reference

Top level: `cards` (required), `out` (default `./references` next to the spec), `supportsLabel` (default `"Supports:"`), `captured` (`YYYY-MM-DD`, default today), `width` (CSS px, default 1366), `colorScheme` (default `"light"`).

Every card: `key` (output file name), `title`, `supports`, and exactly one of `url` or `pdf`.

| Web card | Meaning |
| --- | --- |
| `highlights[].text` | Phrase to mark, as it is in the page text. `nth` picks a later match (0-based). |
| `highlights[].within` | Search only inside one element: `{ "sel": "tr", "has": "RTX Pro 6000" }`. Add `"pick": "smallest"` for nested elements such as `div` or `li`. |
| `highlights[].cell` | With `within`: mark the whole N-th `th`/`td` cell of that element. |
| `region` or `regions` | Crop anchors. `top`/`bottom` are `{ "sel", "has" }` or `{ "text", "up": "pre" }`, plus `padTop`/`padBottom` in px. The default crop is around all highlights. Several regions stack into one card. |
| `hide` | Extra CSS selectors to hide, for pop-ups the built-in list misses. |
| `settleMs` | Wait after the page loads, default 2500. |

| PDF card | Meaning |
| --- | --- |
| `pdf` | URL or local path. Without `page`, the script uses the first page that has every text highlight; the probe warns with `!` when the first highlight is on other pages too. Set `page` to choose. |
| `highlights[].text` | Phrase matched as whole words. `nth` picks a later match. |
| `highlights[].row` | Search only the table row whose line contains this phrase. |
| `highlights[].wrap` | Also mark N continuation lines of a wrapped table cell. |
| `highlights[].box` | `[x0, y0, x1, y1]` in PDF points, from `probe --lines`. |
| `crop` | `top` and `bottom`, each in points or `{ "text": "Table 65" }` for the line that holds a phrase (`nth` picks a later line). `"bottom": "footer"` keeps the printed page number. A number is used as given unless it cuts through a text line; then the edge moves out of that line. Phrase edges and the default crop also leave a few points of space. The default is about 40 pt around the highlights: set `crop` to include the table heading and notes. |
| `shownUrl` | URL bar text. Use it for a local PDF file, or to show the link you were given when the download redirects. |

## Gotchas

- **Phrases match the page's text, not how it renders.** CSS can uppercase a table header, so copy phrases from the page source. Curly quotes, dashes and non-breaking spaces are folded for you.
- **Sites redirect.** dell.com sends `/en-us/` manuals to `/en-th/` from Thailand, and Dell's `/asset/...pdf` links end at `/assetlink/doc/...`. The URL bar shows the address that was actually loaded, for pages and PDFs, and the probe flags the redirect. On a PDF card, `shownUrl` sets other URL bar text; a web card always shows the loaded address.
- **HTTP 403 from curl.** Some vendor sites, Dell among them, refuse curl's or Python's default user agent. The script sends a desktop Chrome user agent for pages and PDF downloads, so check a URL with `probe`, not curl.
- **Docs versions.** Some pages exist only in a project's development docs (Ceph's `/en/latest/`) and return 404 on the stable release. The probe reports the HTTP status. Tell the user which version a card uses.
- **Tall passages.** One crop holds up to 2800 CSS px. Split a long passage into two `regions`.
- **PDF tables.** Words in different columns of one row get separate boxes. Use `row` to pick a value from the right row and `wrap` for cell text that continues on the next line. Use `box` only as a last resort, because coordinates break when the PDF is revised.
- **Pop-ups.** Known cookie banners, chat bubbles, "Ask AI" and back-to-top buttons are hidden automatically. Add `hide` selectors for others, and check the PNG.
- **Sticky navigation.** A sticky sidebar shows what is in view at the crop position, sometimes with a menu item cut at the crop edge. Add its selector to `hide` if it distracts.
