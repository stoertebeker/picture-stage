# Komponenten-Inventar

> **Zweck:** Bestandsaufnahme aller wiederkehrenden UI-Bausteine in den 27 Jinja-
> Templates plus Lücken, die in Welle 2 (Komponenten-Issues PS-UX-10..17) geschlossen
> werden. Dient als Single Source of Truth für die Welle-2-Arbeit und als Review-
> Referenz für Welle-3-Page-Redesigns.
>
> **Lese-Reihenfolge:** Status-Spalte zuerst. `vorhanden` = funktional & visuell
> akzeptabel, kein Eingriff nötig. `teilweise` = funktional, aber ad-hoc verteilt
> oder visuell uneinheitlich → wird in Welle 2 zu Macro/Component konsolidiert.
> `fehlt` = ist heute nicht implementiert, würde aber spürbar fehlen.

---

## 1 Layout & Strukturen

| Komponente | Status | Heute zu finden | Konsolidierung |
|------------|--------|-----------------|----------------|
| **Layout-Primitives** (Stack/Cluster/Grid/Container) | dokumentiert | ad-hoc `flex`/`grid`-Kombinationen über alle Templates verteilt | `docs/design/primitives.md` (PS-UX-05) |
| **Base-Layout (Photographer)** | vorhanden | `app/templates/base.html` | – |
| **Base-Layout (Guest)** | vorhanden | `app/templates/guest_base.html` | – |
| **Theme-Toggle** (Light/Dark) | vorhanden | `base.html`, `setup/index.html`, `auth/{login,signup,verify}.html` via `[data-theme-toggle]` | PS-UX-04 (closed) |
| **Lang-Switcher** (DE/EN) | vorhanden | `base.html:23`, `guest_base.html` | – |
| **Top-Nav** (Photographer) | vorhanden | `base.html:13-43` | wird im Page-Redesign visuell aufgewertet (Welle 3) |
| **Cookie-Banner** | vorhanden | `guest_base.html:43-62` | – |

---

## 2 Buttons

| Variante | Status | Heute zu finden | Welle-2-Issue |
|----------|--------|-----------------|---------------|
| **Primary** | vorhanden | `_macros/buttons.html` `button(variant='primary')` (semantic Tokens: `bg-accent`, `text-on-accent`) | PS-UX-10 ✓ |
| **Secondary** | vorhanden | `button(variant='secondary')` (Border + `bg-surface-raised`) | PS-UX-10 ✓ |
| **Ghost** | vorhanden | `button(variant='ghost')` (transparent, Hover-Surface) | PS-UX-10 ✓ |
| **Danger** | vorhanden | `button(variant='danger')` (`bg-status-danger`) | PS-UX-10 ✓ |
| **Success** | vorhanden | `button(variant='success')` (`bg-status-success`) | PS-UX-10 ✓ |
| **Icon-only** | vorhanden | `button(icon_only=true)` (quadratisches Padding) | PS-UX-10 ✓ |
| **Loading-State** | vorhanden | `button(loading=true)` (Spinner-SVG + `disabled` + `aria-busy`) | PS-UX-10 ✓ |
| **Größen** (sm / md / lg) | vorhanden | `button(size='sm'/'md'/'lg')` | PS-UX-10 ✓ |

**Verprobt in:** `dashboard/index.html` (Header + Empty-State + Modal Erstellen/Abbrechen), `auth/login.html` (Submit), `galleries/detail.html` (Status-Transitions + Expiry).

**Migrations-Backlog:** Restliche Button-Stellen in `galleries/detail.html` (Rename, Bulk-Delete, Share-Modal), `auth/signup.html`, `auth/verify.html`, `setup/index.html`, `admin/_signup_row.html`, `guest/_complete_modal.html`, `guest/_password.html`, `guest/_lightbox.html`, `guest/viewer.html` werden im jeweiligen Page-Redesign (Welle 3) migriert.

---

## 3 Forms

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Field-Wrapper** (Label + Input + Help + Error) | vorhanden | `_macros/forms.html` `field(label, name, error, help, required)`; ARIA: `aria-describedby` über fixe `{name}-help`/`{name}-error` IDs | PS-UX-11 ✓ |
| **Text-Input** | vorhanden | `text_input(name, value, type, placeholder, required, autofocus, autocomplete, attrs)` | PS-UX-11 ✓ |
| **Password-Input** | vorhanden | `password_input(...)` (Default `autocomplete='current-password'`) | PS-UX-11 ✓ |
| **Email-Input** | vorhanden | `email_input(...)` (Default `autocomplete='email'`) | PS-UX-11 ✓ |
| **Textarea** | vorhanden | `textarea(name, value, placeholder, rows, attrs)` | PS-UX-11 ✓ |
| **Select** | vorhanden | `select(name, options, selected, required, attrs)` (options als Liste von Tuples oder Dicts) | PS-UX-11 ✓ |
| **Checkbox** | vorhanden | `checkbox(name, label, value, checked, attrs)` mit inline-Label | PS-UX-11 ✓ |
| **CSRF Hidden Field** | vorhanden | `csrf_input(token)` (alias für das alte Hidden-Input-Pattern, klarere Aufruf-Stelle) | PS-UX-11 ✓ |
| **Form-Error Banner** | vorhanden | `form_error(message)` (Page-Level-Alert, `role="alert"`) | PS-UX-11 ✓ |
| **Toggle** | fehlt | – | (späterer Pass, wenn ein Toggle-Use-Case auftaucht) |
| **File-Upload (Drop-Zone)** | vorhanden | `galleries/_upload.html` mit Alpine-Komponente `uploadZone()` aus `components.js` | – (Page-spezifisch, nicht in Macro lift-bar) |

**Verprobt in:** `auth/login.html` (Email + Password + Form-Error), `auth/signup.html` (Email + 2× Password + Form-Error), `dashboard/index.html` Create-Modal (Text-Input mit autofocus).

**Migrations-Backlog:** Restliche Form-Stellen in `auth/verify.html`, `setup/index.html`, `guest/_password.html`, `galleries/audit_log.html` (Select), `galleries/detail.html` (Rename + Expiry), `galleries/_share_modal.html` (Passwort), `guest/viewer.html` (Sort/Filter-Selects) werden im jeweiligen Page-Redesign (Welle 3) migriert.

**Bewusste Auslassung:** Tailwind opacity-modifier (`bg-status-danger/10`) funktioniert mit unseren CSS-Variable-Tokens **nicht** out-of-the-box (Tailwind erwartet RGB-Komponenten in der Variable, wir haben Hex). Das `form_error`-Macro nutzt deshalb temporär `bg-red-500/10` direkt. Sobald `tokens.md` auf RGB-Komponenten-Variablen migriert ist, kann der Macro auf semantic Token zurückwechseln — bis dahin als TODO-Kommentar im Macro vermerkt.

---

## 4 Modals & Overlays

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Dialog (`<dialog>` native)** | teilweise | `dashboard/index.html` Create-Modal, `galleries/detail.html` Preview-Modal | **PS-UX-12** |
| **Modal über `<div>`** | teilweise | `galleries/_share_modal.html`, `_delete_modal.html`, `guest/_complete_modal.html` | **PS-UX-12** (auf `<dialog>`-Basis vereinheitlichen) |
| **Focus-Trap** | fehlt | aktuell kein Focus-Lock im Modal | **PS-UX-12** |
| **ESC-Close** | teilweise | nativer `<dialog>` schließt mit ESC, `<div>`-Variante nicht | **PS-UX-12** |
| **Backdrop-Click-Close** | fehlt | kein konsistentes Pattern | **PS-UX-12** |
| **Lightbox-Overlay** | vorhanden | `guest/_lightbox.html` (eigene Alpine-Komponente, eigenes Z-Index-Layer) | **PS-UX-17** (Refactor: Tastatur-Nav + Mobile-Swipe + inline Selection) |

**Aktion Welle 2:** Macro `_macros/modal.html` mit nativer `<dialog>`-Basis, Focus-Trap, ESC- und Backdrop-Close. Bestehende `<div>`-Modals auf das Macro migrieren.

---

## 5 Feedback (Toasts & Banner)

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Toast / Notification-System** | fehlt | – (Success/Error nach mutierenden Aktionen ist heute unsichtbar oder via Page-Reload) | **PS-UX-13** |
| **Inline-Error-Banner** | teilweise | Login/Password-Verify als rote Box | wird zu Field-Wrapper (PS-UX-11) oder Toast (PS-UX-13) |
| **Cookie-Banner** | vorhanden | `guest_base.html:43-62` | – |
| **Pending-Approval-Hinweis** | vorhanden | `login.html` bei Status `pending` | bleibt page-spezifisch |

**Aktion Welle 2:** Macro `_macros/toast.html` + globaler Container in `base.html` + Alpine-Listener auf `HX-Trigger: showToast`-Server-Header. Drei Varianten: success / warn / danger. ARIA-Rollen.

---

## 6 Cards & Tiles

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Gallery-Card** | umgesetzt | `dashboard/_gallery_card.html` (Hero-Cover, Status, Quick-Actions) | **PS-UX-24b** |
| **Image-Card / Thumbnail-Tile** | teilweise | `galleries/_image_grid.html`, `guest/_image_grid.html` (zwei separate Implementierungen) | **PS-UX-14** |
| **Status-Pill** (draft/shared/completed/archived) | umgesetzt | `_macros/cards.html` + Dashboard/Galerie-Detail | PS-UX-14 |
| **Empty-Tile / Placeholder** | teilweise | grauer Quadrat in `_image_grid.html`, wenn keine Preview | bleibt Card-intern |

**Aktion Welle 2:** Macro `_macros/cards.html` stellt generische Card, Tile und Status-Pill bereit. Dashboard nutzt zusätzlich eine spezialisierte Hero-Gallery-Card, weil Cover, Counts und Quick-Actions dort fest zusammengehören.

---

## 7 Empty-States

| Page | Status | Heute zu finden | Welle-2-Issue |
|------|--------|-----------------|---------------|
| **Dashboard ohne Galerien** | vorhanden | `dashboard/index.html` (mit Icon, Headline, CTA im HTMX-Grid) | wird in PS-UX-15 zu Macro |
| **Galerie ohne Bilder** | vorhanden | `galleries/_image_grid.html:57-68` (gleiches Pattern) | **PS-UX-15** |
| **Audit-Log leer** | fehlt | keine Empty-Behandlung – Tabelle bleibt einfach leer | **PS-UX-15** |
| **Pending-Signups leer** | fehlt | Admin-Tabelle ohne Empty-Hinweis | **PS-UX-15** |
| **Guest-Viewer Filter ergibt 0** | fehlt | Image-Grid bleibt leer ohne Hinweis | **PS-UX-15** |

**Aktion Welle 2:** Macro `_macros/empty.html` (Icon-Slot, Headline, Description, CTA-Slot). Die zwei vorhandenen Empty-States auf Macro migrieren, drei neue ergänzen.

---

## 8 Loading & Skeletons

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Upload-Progress-Bar** | vorhanden | `_upload.html` via Alpine-State `uploadProgress` | – |
| **HTMX-Indicator (Spinner)** | fehlt | kein `hx-indicator` aktiv | **PS-UX-16** |
| **Image-Grid-Skeleton** | fehlt | – | **PS-UX-16** |
| **Card-Skeleton** | fehlt | – | **PS-UX-16** |
| **Row-Skeleton** (Tabellen-Loading) | fehlt | – | **PS-UX-16** |

**Aktion Welle 2:** Macro `_macros/skeleton.html` mit pulsierenden Platzhaltern. `hx-indicator`-Pattern konsistent über alle Partial-Loads. CSS `prefers-reduced-motion` respektieren.

---

## 9 Tabellen & Pagination

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Audit-Log-Tabelle** | vorhanden | `_audit_log_table.html` mit Pagination | (Welle 3 — PS-UX-29) |
| **Admin-Signup-Tabelle** | vorhanden | `admin/pending.html` + `_signup_row.html` | (Welle 3 — PS-UX-27) |
| **Pagination** | vorhanden | `_audit_log_table.html` mit Prev/Next + Page-Counter | bleibt page-spezifisch |

Tabellen-Layout selbst wird im jeweiligen Page-Redesign (Welle 3) visuell überarbeitet; ein gemeinsames Tabellen-Macro lohnt sich für zwei Tabellen nicht.

---

## 10 Lightbox

| Komponente | Status | Heute zu finden | Welle-2-Issue |
|------------|--------|-----------------|---------------|
| **Bild-Lightbox** | vorhanden | `guest/_lightbox.html` + Methoden in `components.js` (openLightbox, closeLightbox, nextImage, prevImage, handleKeydown) | **PS-UX-17** (Refactor) |
| **Tastatur-Nav** | vorhanden | `handleKeydown` in `guestViewer()` | refinen in PS-UX-17 |
| **Swipe-Geste (Mobile)** | fehlt | – | **PS-UX-17** |
| **Inline Selection / Favorite** | vorhanden | `_lightbox.html:44-49` mit `toggleSelect` / `toggleFavorite` | UX-Politur in PS-UX-17 |
| **Bild-Counter (3/12)** | fehlt | – | **PS-UX-17** |
| **Preload nächstes/vorheriges Bild** | fehlt | – | **PS-UX-17** |

---

## 11 Patterns (kein Macro, aber Konvention)

Diese sind keine Komponenten im engeren Sinne, sondern Vorgaben, die jedes Template
einhält:

- **CSRF-Header global**: `<body hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'>` in `base.html` – jede HTMX-Anfrage trägt den Token. Kein per-Form-Macro nötig (siehe `docs/design/build.md` Regel 6).
- **`<input type="hidden" name="csrf_token">`** in jeder mutierenden Form – Fallback für die nicht-HTMX-Submission (siehe Regel 5).
- **`method="post" action="…"`** auf allen HTMX-mutierenden Forms – Progressive-Enhancement-Fallback (siehe Regel 5).
- **`x-data="…()"` referenziert globale Funktionen** in `frontend/static/js/components.js` – kein Inline-`<script>` im Template (siehe Regel 3).
- **`type="button"`** auf Buttons in Forms, die nicht submitten sollen (siehe Regel 2).
- **HTMX-Targets immer im DOM** (auch im Empty-State; Empty-State als `col-span-full`-Kind im Grid; siehe Regel 1).

---

## 12 Zusammenfassung Welle 2

Acht Issues, eindeutig mit Komponenten verknüpft:

| Issue | Macro/Asset |
|-------|-------------|
| PS-UX-10 | `_macros/buttons.html` |
| PS-UX-11 | `_macros/forms.html` (Field-Wrapper + Inputs) |
| PS-UX-12 | `_macros/modal.html` (`<dialog>`-basiert) |
| PS-UX-13 | `_macros/toast.html` + `HX-Trigger: showToast`-Pattern |
| PS-UX-14 | `_macros/cards.html` (Gallery, Image, Status-Pill als Sub-Macro) |
| PS-UX-15 | `_macros/empty.html` (Icon + Headline + CTA) |
| PS-UX-16 | `_macros/skeleton.html` + HTMX-Indicator-Konvention |
| PS-UX-17 | Lightbox-Refactor (kein Macro, Komponente in `components.js`) |

Jedes Macro wird vor Page-Redesign in Welle 3 in mindestens einem echten Page-Use-Case
verprobt – nicht isoliert „im stillen Kämmerlein" geschrieben.

---

## 13 Wartung

Wenn eine neue Komponente aufkommt:

1. Hier einsortieren (Status `fehlt`, Welle-2-Issue verlinken).
2. Wenn das passende Welle-2-Issue noch nicht existiert: neues `bd create` mit
   Parent-Verweis auf Epic `picture-stage-3av`.
3. Beim Abschluss: Status auf `vorhanden` setzen, Pfad zum Macro / zur Datei
   ergänzen.

Diese Datei ist Single Source of Truth für **welche** UI-Bausteine existieren oder
nötig sind. **Welche Tokens** sie verwenden, steht in `docs/design/tokens.md`. **Wie**
sie verschaltet sind (CSP/HTMX/Alpine/Forms), steht in `docs/design/build.md`.
