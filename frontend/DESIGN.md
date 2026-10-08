---
version: alpha
name: SAMB Rekonsiliasi AR
description: The screens Finance Invoicing uses to check scanned customer paperwork against SAMB's records; it should feel like a well-kept document register, not an app.
colors:
  kertas: "#FFFFFF"       # paper: the page ground
  meja: "#ECEEF1"         # desk: only behind paper (scans, thumbnails, the page viewer)
  tinta: "#1A1D22"        # ink: text
  pensil: "#5C636D"       # pencil: secondary text
  meja-muda: "#F5F6F8"    # a lighter desk: the row under the pointer, a filled field
  garis: "#D5D9DE"        # rules between register rows and sections
  garis-tebal: "#AEB4BC"  # the border of a control (button, input)
  biru-pulpen: "#1F3F8F"  # ballpoint blue: actions and links, nothing else
  cap-merah: "#B3261E"    # red stamp: needs a person, errors
  cap-hijau: "#1E6B45"    # green stamp: done, matches
  cap-kuning: "#8A5A00"   # amber: waiting for the system
  jenis-fp: "#5B2C91"     # document types: the filled tag of a page's type (white text), never a state
  jenis-ttg: "#0B6E7A"
  jenis-po: "#A3286A"
  jenis-sj: "#4D6A12"
  jenis-fpj: "#1D6FA3"
  jenis-pel: "#7A4A2E"
  jenis-lanjutan: "{colors.pensil}"
typography:
  title:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: 800
    letterSpacing: -0.02em
  section:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: 700
  body:
    fontFamily: Plus Jakarta Sans
    fontSize: 14.5px
    lineHeight: 1.55
  figures: tabular-nums, right-aligned in columns
rounded:
  paper: 1px
  control: 4px
  stamp: 3px
  surface: 0
spacing:
  row: 10px
  block: 20px
  section: 36px
components:
  button-primary:
    backgroundColor: "{colors.biru-pulpen}"
    textColor: "{colors.kertas}"
    rounded: "{rounded.control}"
    padding: 8px 14px
  stamp:
    borderColor: "currentColor"
    textColor: "{colors.cap-merah} | {colors.cap-hijau} | {colors.cap-kuning} | {colors.biru-pulpen}"
    rounded: "{rounded.stamp}"
---

## Overview

Finance Invoicing staff at SAMB (a Jakarta distributor) check stacks of scanned customer paperwork (Faktur
Penjualan, PO, Tanda Terima) against SAMB's records before an order's documents go to Satellite. Their world is
paper: black-and-white scans of ruled business forms, the *buku register* where document handovers are logged,
rubber stamps (DITERIMA, the store's own stamp, visible on the scans themselves), and the reconciliation sheet:
two amounts, the difference under a rule.

So the screens are a **document register**. The scans are the only paper on screen and the only thing that lifts.
Everything the system adds is written into ruled register rows beside them. Amounts are laid out the way an
accountant reconciles them. Each order's state is a rubber stamp.

## Colors

- **Kertas (#FFFFFF):** the page is white paper, so the white scans sit on it naturally. Not cream.
- **Meja (#ECEEF1):** the desk tone, used only *behind* paper: the page viewer, thumbnail wells. **Meja muda
  (#F5F6F8)** marks the row under the pointer.
- **Garis (#D5D9DE) / garis tebal (#AEB4BC):** rules between rows; the border of a button or input.
- **Tinta (#1A1D22):** 16.9:1 on kertas.
- **Pensil (#5C636D):** 6.1:1 on kertas, 5.2:1 on meja.
- **Biru pulpen (#1F3F8F):** the ballpoint the clerks write with: links and the one primary button on a screen.
  9.7:1. Never a status.
- **Cap merah / cap hijau / cap kuning:** stamp inks, used only for state: needs a person (6.5:1), done (6.5:1),
  waiting (5.9:1). No tinted pastel backgrounds: a state is ink on paper.
- **Jenis dokumen** (the user, 2026-10-08: "for each type of documents, a different color in their label, so i can
  distinguish better"): a page's type is a small filled tag with white text (`.tchip.t-<TYPE>`, `.ttag` on a
  thumbnail), one hue per type, each away from the stamp inks and the ballpoint: Faktur Penjualan violet #5B2C91
  (9.5:1), Tanda Terima teal #0B6E7A (6.0:1), PO plum #A3286A (6.8:1), Surat Jalan moss #4D6A12 (6.2:1), Faktur
  Pajak cerulean #1D6FA3 (5.4:1), Pelunasan brown #7A4A2E (7.4:1). A continuation is pensil, "other" is pensil
  outlined, and "unsure" is the red stamp, because it needs a person. A type is filled, a state is ink: never use a
  type's colour for anything else.

## Typography

One family, Plus Jakarta Sans, the typeface of Jakarta's own city identity. The scale does the work: page title
28/800, section 18/700, labels 13/600, body 14.5/400, secondary 13 in pensil. Every amount and count uses tabular
figures and sits right-aligned in its column. Monospace only inside input fields (where 0 and O must differ) and in
the technical folds. Sentence case everywhere; the stamp is the only uppercase.

## Layout

Lists are registers: a table with a header row and ruled rows, never a stack of cards. Where a document is involved
(Periksa order, Data terkirim, a page), the screen is a spread: the register on the left, the paper on the right.
Register pages are 1200px wide; spreads 1400px. Counts live in sentences or in the register's filter row, never in
big-number tiles.

## Elevation & Depth

Only paper lifts: scans and thumbnails get a small paper shadow on the desk tone. Nothing else has a shadow. The
sticky top bar is flat with a rule under it.

## Shapes

Paper 1px. Buttons and inputs 4px. Stamps 3px. Sections, rows and tables 0: they are ruled, not boxed. One level of
containment at most.

## Components

- **Top bar:** the wordmark (SAMB, then "Rekonsiliasi AR"), one Finance tab, Batch, with an ink underline when
  current and a red count of the batches waiting for a person; Unggah batch; the search box; "Teknis" at the end
  (the screens over every batch, then the developers' screens).
- **A batch's steps:** `.ws-steps`, five ruled columns (one per step, a list on a phone): its number and who acts
  (sistem / Anda), its name, then one line in the state's ink (red needs you, amber ◔ the system works, green ✓ done,
  pencil nothing yet) and one line in pensil. The open step is underlined in tinta on meja muda. In the batch
  register the same five states are small bordered marks (`.ws-marks`), each a link to its step.
- **Register:** `table.reg`: header row in pensil 12.5/600, rows ruled in garis, figures right-aligned.
- **Stamp:** `.cap` (+ `.merah`, `.hijau`, `.kuning`, `.biru`): the order's state, uppercase, bordered in its ink.
  One per order. `.cap.besar` (rotated a few degrees) only in an order's own header.
- **Reconciliation:** `table.rekon`: label left, amount right, the difference under a rule, the result double-ruled.
- **State of a value:** plain text in its ink with a leading mark (✓ for backed, ! for check it).
- **Type tag:** `.tchip.t-<TYPE>` wherever a page's or document's type is named (registers, tiles, tabs, the page
  header); `.ttag` on a thumbnail. The colour comes from `--jenis` set by `.t-<TYPE>`.
- **Buttons:** `.btn` (outlined, ink), `.btn.primary` (filled biru pulpen, one per screen area), `.linkbtn`.
- **Technical folds:** `details.tech`, closed, at the end of a screen.

The web app's stylesheet is `frontend/app/globals.css`. The developers' Teknis screens (server-rendered by the API,
`services/api/static/app.css`) share its tokens and base (top bar, registers, stamps): change both when the base
changes.

## Do's and Don'ts

- Do reuse the top bar, the register, the stamp and the tokens above on every page, unchanged.
- Do add a token here before a page uses it. Never invent a one-off inside a page.
- Don't change: Plus Jakarta Sans (chosen for this product), the Indonesian wording in services/api/bahasa.py.
- Light only: the screens show white paper scans, and there is no dark theme (removed 2026-10-02).
- Don't use: card boxes around list items or sections (SD cluster 4, K2), cards inside cards (K11), coloured
  side stripes (K4), big-number stat tiles (L4), pastel pill chips for every state (K12), ALL-CAPS section labels
  (SD4), "A · B · C" meta strings as decoration (SD4d), monospace for labels (SD4m), numbered circles on things that
  aren't a sequence (SD6), arrows glued to button labels (CP3), emoji as icons (I2), a dark theme (C7), cream
  paper with a terracotta accent (SD1), a logo mark in a rounded square (K6).
