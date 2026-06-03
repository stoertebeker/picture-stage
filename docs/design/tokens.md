# Design-Tokens – Editorial Dark

> **Status:** Quelldokument für Epic `picture-stage-3av` (v0.5 UX-Redesign).
> Tokens hier sind die **Single Source of Truth**. Tailwind-Config (PS-UX-02)
> und CSS-Variablen in `input.css` werden aus dieser Spec gespeist.
>
> **Stil:** Editorial Dark – dunkle, neutrale Bühne, Bilder dominieren,
> Serif-Display + Sans-UI, ein einziger Akzent-Hue trägt die interaktive
> Spannung.

---

## 1 Naming-Konvention

Token-Namen sind **semantisch**, nicht visuell. `surface-base` statt `zinc-900`,
`text-primary` statt `white`. Der semantische Name beschreibt die Rolle, nicht
die Farbe – das macht Theme-Wechsel (Dark ↔ Light) trivial.

**Schema:** `<kategorie>-<rolle>[-<variante>]`

| Kategorie | Beispiele |
|-----------|-----------|
| `surface` | `surface-base`, `surface-raised`, `surface-sunken`, `surface-overlay` |
| `text` | `text-primary`, `text-secondary`, `text-muted`, `text-inverse`, `text-on-accent` |
| `border` | `border-subtle`, `border-strong`, `border-focus` |
| `accent` | `accent`, `accent-hover`, `accent-muted` |
| `favorite` | `favorite`, `favorite-hover` |
| `status` | `status-success`, `status-warn`, `status-danger`, `status-info` |
| `space` | `space-0` … `space-24` (4px-Grid) |
| `radius` | `radius-none/sm/md/lg/full` |
| `shadow` | `shadow-sm/md/lg` |
| `z` | `z-base/dropdown/sticky/overlay/modal/toast/tooltip` |

**Regel:** Templates verwenden semantische Namen via Tailwind-Custom-Klassen
(z.B. `bg-surface-raised`, `text-primary`). Rohe Tailwind-Farben
(`bg-zinc-800`) sind in Anwendungs-Templates **verboten**, nur in der
Token-Mapping-Schicht erlaubt.

---

## 2 Farben

### 2.1 Neutral-Palette (Basis: Tailwind `zinc`)

Tailwinds `zinc` ist die neutrale Grundpalette – leicht kühl, ohne Blaustich,
ideal für Editorial Dark, weil sie Bilder farblich nicht stört.

| Token | Hex | Tailwind |
|-------|-----|----------|
| `neutral-50`  | `#fafafa` | `zinc-50` |
| `neutral-100` | `#f4f4f5` | `zinc-100` |
| `neutral-200` | `#e4e4e7` | `zinc-200` |
| `neutral-300` | `#d4d4d8` | `zinc-300` |
| `neutral-400` | `#a1a1aa` | `zinc-400` |
| `neutral-500` | `#71717a` | `zinc-500` |
| `neutral-600` | `#52525b` | `zinc-600` |
| `neutral-700` | `#3f3f46` | `zinc-700` |
| `neutral-800` | `#27272a` | `zinc-800` |
| `neutral-900` | `#18181b` | `zinc-900` |
| `neutral-950` | `#09090b` | `zinc-950` |

### 2.2 Semantic-Layer (Dark-Mode = Default)

| Token | Hex (Dark) | Rolle |
|-------|------------|-------|
| `surface-base`    | `#09090b` (`zinc-950`) | Body-Background, hinterste Ebene |
| `surface-raised`  | `#18181b` (`zinc-900`) | Cards, Modals, gehobene Container |
| `surface-sunken`  | `#000000`              | Image-Bühne, Lightbox-Backdrop (echtes Schwarz für Bild-Kontrast) |
| `surface-overlay` | `rgb(9 9 11 / 0.85)`   | Modal-Backdrop, Scrim |
| `text-primary`    | `#fafafa` (`zinc-50`)  | Hauptkörper, Headings |
| `text-secondary`  | `#d4d4d8` (`zinc-300`) | Untergeordneter Text, Subheads |
| `text-muted`      | `#a1a1aa` (`zinc-400`) | Captions, Meta, Help-Text |
| `text-inverse`    | `#09090b` (`zinc-950`) | Text auf hellen Flächen (Light-Akzent-Buttons) |
| `text-on-accent`  | `#022c22`              | Text auf Emerald-CTAs (`emerald-950`-nah, optimaler Kontrast) |
| `border-subtle`   | `#27272a` (`zinc-800`) | Card-Borders, Trennlinien |
| `border-strong`   | `#52525b` (`zinc-600`) | Form-Borders, Tabs aktiv |
| `border-focus`    | `#34d399` (`emerald-400`) | Focus-Ring (a11y) |

### 2.3 Accent (Emerald)

Emerald trägt **alle interaktiven Akzente**: Hover-States, Selection-Marker,
primäre CTA-Buttons, Focus-Ringe, Links. Im Dark-Mode hellere Stufe (`-400`)
wegen Lesbarkeit auf dunklem Grund.

| Token | Hex (Dark) | Tailwind | Verwendung |
|-------|------------|----------|------------|
| `accent`         | `#34d399` | `emerald-400` | Primärer Akzent, Links, Selection |
| `accent-hover`   | `#6ee7b7` | `emerald-300` | Hover-State auf Akzent |
| `accent-strong`  | `#10b981` | `emerald-500` | Primary-Button-Fläche |
| `accent-muted`   | `rgb(52 211 153 / 0.15)` | – | Selection-Highlight (Bild ausgewählt: subtile Tönung) |
| `accent-ring`    | `rgb(52 211 153 / 0.4)`  | – | Focus-Ring-Outline (3 px) |

**Hinweis Akzent vs. Success:** Beide nutzen Emerald, sind aber **tonal
getrennt** – Akzent ist hellere Stufe (`-400`/`-500`), Success-Status nutzt
gedeckte Stufe (`-600`) plus immer Icon (Check-Symbol). So gibt es keine
Verwechslung „interaktiv = success".

### 2.4 Favorit (Rose)

Eigener semantischer Slot für „Lieblingsbild" – warm-rot, klassisches Herz.
**Vorläufige Wahl** (Käpt'ns Entscheidung: erst sehen, wie es wirkt).

| Token | Hex (Dark) | Tailwind | Verwendung |
|-------|------------|----------|------------|
| `favorite`        | `#fb7185` | `rose-400` | Herz-Icon aktiv, Favorit-Marker |
| `favorite-hover`  | `#fda4af` | `rose-300` | Hover beim Toggle |
| `favorite-muted`  | `rgb(251 113 133 / 0.15)` | – | Card-Tönung bei Favorit |

### 2.5 Status

| Token | Hex (Dark) | Tailwind | Verwendung |
|-------|------------|----------|------------|
| `status-success` | `#059669` | `emerald-600` | Erfolgreich (Upload OK, Email verifiziert) |
| `status-warn`    | `#f59e0b` | `amber-500`   | Warnung (Galerie läuft bald ab) |
| `status-danger`  | `#dc2626` | `red-600`     | Fehler, destruktive Aktion (Delete) |
| `status-info`    | `#0ea5e9` | `sky-500`     | Neutrale Info-Hinweise |

> **A11y:** Status-Farben **nie alleinstehend** – immer mit Icon oder Text.
> WCAG-AA-Kontrast auf `surface-base` für alle vier ≥ 4.5:1 (verifizieren in
> PS-UX-40).

### 2.6 Light-Mode-Mapping

Dark ist Default. Light-Mode als optionaler Toggle (PS-UX-04). Semantische
Tokens werden im `[data-theme='light']`-Layer überschrieben:

| Token | Hex (Light) | Tailwind |
|-------|-------------|----------|
| `surface-base`    | `#fafafa` | `zinc-50` |
| `surface-raised`  | `#ffffff` | `white` |
| `surface-sunken`  | `#f4f4f5` | `zinc-100` |
| `surface-overlay` | `rgb(255 255 255 / 0.92)` | – |
| `text-primary`    | `#18181b` | `zinc-900` |
| `text-secondary`  | `#3f3f46` | `zinc-700` |
| `text-muted`      | `#52525b` | `zinc-600` |
| `text-inverse`    | `#fafafa` | `zinc-50` |
| `text-on-accent`  | `#ffffff` | `white` |
| `border-subtle`   | `#e4e4e7` | `zinc-200` |
| `border-strong`   | `#a1a1aa` | `zinc-400` |
| `border-focus`    | `#10b981` | `emerald-500` |
| `accent`          | `#059669` | `emerald-600` |
| `accent-hover`    | `#047857` | `emerald-700` |
| `accent-strong`   | `#065f46` | `emerald-800` |
| `accent-muted`    | `rgb(16 185 129 / 0.12)` | – |
| `favorite`        | `#f43f5e` | `rose-500` |
| `favorite-hover`  | `#e11d48` | `rose-600` |
| `status-success`  | `#10b981` | `emerald-500` |
| `status-warn`     | `#d97706` | `amber-600` |
| `status-danger`   | `#dc2626` | `red-600` |
| `status-info`     | `#0284c7` | `sky-600` |

---

## 3 Typografie

### 3.1 Font-Stacks

| Token | Stack |
|-------|-------|
| `--font-display` | `"Fraunces", "Source Serif 4", Georgia, serif` |
| `--font-sans`    | `"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` |
| `--font-mono`    | `ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace` |

**Fraunces** (OFL, Variable Font mit Achsen `wght`, `opsz`, `SOFT`, `WONK`):
warm, leicht idiosynkratisch, ideal für Editorial Display. **Inter** (OFL,
Variable Font `wght`): zuverlässigster UI-Workhorse, sehr lesbar in allen
Größen, harmoniert farblich mit Fraunces.

**Self-Hosting** in PS-UX-03 – WOFF2-Subsets (latin + latin-ext), `font-display: swap`.

### 3.2 Type-Scale (modular 1.25, Basis 16 px)

| Token | px / rem | Verwendung |
|-------|----------|------------|
| `text-xs`  | 12 / 0.75   | Meta, Caption, Audit-Log-Detail |
| `text-sm`  | 14 / 0.875  | Help-Text, Form-Hints, Tabellen-Body |
| `text-base`| 16 / 1.0    | Body, Form-Labels, UI-Default |
| `text-lg`  | 20 / 1.25   | Lead-Paragraph, Card-Titel |
| `text-xl`  | 24 / 1.5    | Section-Heading (H3) |
| `text-2xl` | 32 / 2.0    | Page-Heading (H2) |
| `text-3xl` | 40 / 2.5    | Hero-Heading (H1, Dashboard) |
| `text-4xl` | 56 / 3.5    | Display-Heading (Login, Setup) |
| `text-5xl` | 72 / 4.5    | Editorial Hero (z.B. Guest-Viewer Galerie-Titel) |

### 3.3 Line-Heights

| Token | Wert | Verwendung |
|-------|------|------------|
| `leading-tight`   | 1.1  | Display-Headings (4xl, 5xl) |
| `leading-snug`    | 1.25 | Section-Headings (xl, 2xl, 3xl) |
| `leading-normal`  | 1.5  | Body-Text |
| `leading-relaxed` | 1.625| Legal-Pages, Long-Form |

### 3.4 Font-Weights

| Inter | Fraunces |
|-------|----------|
| 400 Regular (Body) | 400 Regular (Display Default) |
| 500 Medium (UI-Labels, Buttons) | 500 Medium (Subhead, betonte Display) |
| 600 Semibold (UI-Headings, Tabs aktiv) | 700 Bold (Hero, max. Betonung) |

**Fraunces-Optical-Size (`opsz`):** Bei `text-3xl` und größer setzen wir
`font-optical-sizing: auto` – das aktiviert die schmaleren, eleganteren
Display-Glyphen automatisch. Unterhalb bleibt die robuste Text-Variante.

### 3.5 Heading-Defaults (Empfehlung)

| Element | Font | Size | Weight | Line-Height |
|---------|------|------|--------|-------------|
| H1 (Hero)        | Fraunces | 3xl / 4xl | 500 | tight |
| H2 (Page)        | Fraunces | 2xl       | 500 | snug |
| H3 (Section)     | Inter    | xl        | 600 | snug |
| H4 (Subsection)  | Inter    | lg        | 600 | snug |
| Body             | Inter    | base      | 400 | normal |
| Meta / Caption   | Inter    | xs / sm   | 400 | normal |

Body bleibt Sans, weil Forms und Listen-UI das verlangen. Fraunces ist
für Display reserviert – mehr Editorial-Bühne, weniger Risiko von „Cluttered
Serif Body".

---

## 4 Spacing (4 px-Grid)

| Token | px | rem |
|-------|----|----|
| `space-0`   | 0   | 0 |
| `space-0.5` | 2   | 0.125 |
| `space-1`   | 4   | 0.25 |
| `space-1.5` | 6   | 0.375 |
| `space-2`   | 8   | 0.5 |
| `space-3`   | 12  | 0.75 |
| `space-4`   | 16  | 1.0 |
| `space-6`   | 24  | 1.5 |
| `space-8`   | 32  | 2.0 |
| `space-12`  | 48  | 3.0 |
| `space-16`  | 64  | 4.0 |
| `space-24`  | 96  | 6.0 |

**Anwendung:**
- Inline-Padding (Buttons, Inputs): `space-3` / `space-4`
- Card-Padding: `space-6`
- Section-Gap (Stack-Primitive): `space-8` / `space-12`
- Page-Container-Padding: `space-4` (Mobile) / `space-8` (Desktop)
- Hero-Spacing: `space-16` / `space-24`

---

## 5 Radius

| Token | Wert | Verwendung |
|-------|------|------------|
| `radius-none` | 0    | Image-Bühne, Lightbox, Hero-Bilder (randlos) |
| `radius-sm`   | 4 px | Inputs, Tags, Pills |
| `radius-md`   | 8 px | Buttons, Toasts |
| `radius-lg`   | 12 px| Cards, Modals, Image-Thumbs im Grid |
| `radius-full` | 9999 px | Avatars, Icon-Buttons, Status-Dots |

Editorial Dark mag dezent runde Ecken – nicht sharp wie Brutalism, nicht
übertrieben rund wie Mobile-Apps. `radius-md` ist die Default-Stufe für
interaktive Elemente.

---

## 6 Shadow

Im Dark-Mode sind Shadows **kaum sichtbar** – sie tragen die Hierarchie über
sehr leichtes „Lift" bei. Im Light-Mode kräftiger, um Cards von Background
abzusetzen.

### Dark-Mode

| Token | Wert |
|-------|------|
| `shadow-sm` | `0 1px 2px 0 rgb(0 0 0 / 0.4)` |
| `shadow-md` | `0 4px 6px -1px rgb(0 0 0 / 0.5), 0 2px 4px -2px rgb(0 0 0 / 0.4)` |
| `shadow-lg` | `0 10px 15px -3px rgb(0 0 0 / 0.6), 0 4px 6px -4px rgb(0 0 0 / 0.4)` |

### Light-Mode

| Token | Wert |
|-------|------|
| `shadow-sm` | `0 1px 2px 0 rgb(0 0 0 / 0.05)` |
| `shadow-md` | `0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.08)` |
| `shadow-lg` | `0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.08)` |

**Verwendung:** `shadow-sm` für hovered Cards, `shadow-md` für Dropdowns und
Toasts, `shadow-lg` für Modals und Lightbox.

---

## 7 Z-Index

| Token | Wert | Verwendung |
|-------|------|------------|
| `z-base`     | 0   | Default-Stacking |
| `z-dropdown` | 10  | Select-Dropdowns, Popovers |
| `z-sticky`   | 20  | Sticky-Headers, Sort/Filter-Bar |
| `z-overlay`  | 30  | Modal-Backdrop |
| `z-modal`    | 40  | Modal-Content, Confirm-Dialoge |
| `z-toast`    | 50  | Toast-Notifications |
| `z-tooltip`  | 60  | Tooltips (über allem) |

**Regel:** Keine `z-index`-Magic-Numbers (`z-50`, `z-999`) in Templates.
Immer Token-Klasse verwenden, damit der Stacking-Order zentral bleibt.

---

## 8 Glossar / Wartung

**Neuen Token einführen:**
1. Eintrag in der passenden Sektion dieses Dokuments hinzufügen
2. Tailwind-Config (`tailwind.config.js`) ergänzen (PS-UX-02)
3. CSS-Variable in `frontend/static/css/input.css` für Dark + Light setzen
4. Tailwind-CLI neu bauen
5. Im PR auf diese Spec verlinken

**Token umbenennen / ersetzen:**
1. Eintrag hier aktualisieren, alte Bezeichnung als deprecated markieren
2. Grep-Sweep über `app/templates/` für alte Tailwind-Klasse
3. Migration in einem atomaren Commit, Spec-Update separat

**Was hier NICHT gehört:**
- Komponenten-Definitionen (Buttons, Forms, Modals) → `docs/design/components.md` (PS-UX-06)
- Layout-Primitives (Stack, Cluster, Grid) → `docs/design/primitives.md` (PS-UX-05)
- Build-/Toolchain-Details → `tailwind.config.js` + CLAUDE.md

---

## 9 Offene Fragen / Vorbehalte

| Punkt | Entscheidung in |
|-------|-----------------|
| Wirkt Emerald-Akzent gut auf Bildern (warmer Fototon)? | Nach erstem Page-Mockup (PS-UX-20a/24a) – Kapitän reviewt visuell |
| Bleibt Rose als Favorit-Slot oder Wechsel zu Heart-Red (`red-500`)? | Nach Lightbox-Implementierung (PS-UX-17) – wenn Favorit-Toggle live |
| Fraunces im Body bei Legal-Pages? | PS-UX-30a (Legal-Mockup) – evaluieren ob Serif-Body dort Mehrwert bringt |
| Brauchen wir eine Markenfarbe (Logo)? | Out of Scope für v0.5 – kein dediziertes Picture-Stage-Logo geplant |
