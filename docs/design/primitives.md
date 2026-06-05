# Layout-Primitives

> **Zweck:** Vier wiederverwendbare Layout-Patterns als Tailwind-Klassen­kompositionen,
> mit denen alle Pages der v0.5-UX gebaut werden. Reduziert die heutige Vielfalt an
> ad-hoc `flex`-/`grid`-Kombinationen auf vier benannte Idiome.
>
> **Stilfamilie:** Editorial Dark (siehe `docs/design/tokens.md`). Defaults nutzen die
> dortigen Spacing-Tokens (4-px-Grid: `space-2/3/4/6/8/12/16/24`).
>
> **Status:** Pattern-Spec. Diese Dokumentation ist die Quelle, gegen die spätere Page-
> Redesigns reviewt werden. Die Primitives sind absichtlich **Klassen­kompositionen**,
> nicht Jinja-Macros – sie sollen lesbar im Template stehen, ohne dass eine zusätzliche
> Abstraktionsebene gepflegt werden muss.

---

## 1 Stack — vertikale Anordnung mit konsistentem Gap

**Wann:** Elemente werden untereinander gestapelt (Forms, Section-Body, Card-Inhalt).
**Wie:** `flex flex-col` plus eine Stack-Gap-Stufe.

| Variante | Tailwind | Verwendung |
|----------|----------|------------|
| `stack-tight` | `flex flex-col gap-2` | Form-Field + Help-Text, eng beieinander |
| `stack` | `flex flex-col gap-4` | Default — Form-Felder, Section-Inhalt |
| `stack-loose` | `flex flex-col gap-8` | Sektionen voneinander trennen |
| `stack-hero` | `flex flex-col gap-16` | Hero-Areas, Marketing-Sections |

**Beispiel:**

```html
<div class="flex flex-col gap-4">
  <h2 class="text-xl font-semibold">{{ t('gallery.share_heading') }}</h2>
  <p class="text-sm text-text-muted">{{ t('gallery.share_text') }}</p>
  <form …>…</form>
</div>
```

**Regel:** **Niemals** vertikale Abstände über `mb-*` auf jedem Kind setzen. Immer
einen Stack-Wrapper mit `gap-*` – das letzte Kind hat dann automatisch keinen
unteren Abstand, und die Stack-Stufe ist an einer Stelle änderbar.

---

## 2 Cluster — horizontale Anordnung, umbruchsicher

**Wann:** Inline-Gruppen wie Action-Buttons, Tags, Status-Pills, Filter-Chips,
Lang-Switcher. Soll auf engem Viewport sauber umbrechen.
**Wie:** `flex flex-wrap items-center` plus Gap-Stufe.

| Variante | Tailwind | Verwendung |
|----------|----------|------------|
| `cluster-tight` | `flex flex-wrap items-center gap-2` | Tags, Pills |
| `cluster` | `flex flex-wrap items-center gap-3` | Default — Action-Buttons, Toolbars |
| `cluster-loose` | `flex flex-wrap items-center gap-6` | Top-Bar, weit auseinander |

**Beispiel:**

```html
<div class="flex flex-wrap items-center gap-3">
  <button class="…">{{ t('gallery.share') }}</button>
  <button class="…">{{ t('gallery.export') }}</button>
  <button class="…">{{ t('gallery.delete') }}</button>
</div>
```

**Cluster mit Verteilung:** `justify-between` ergänzen, wenn der erste/letzte
Cluster-Eintrag an die Ränder gepinnt werden soll (z.B. Header mit Logo links
und Nav rechts):

```html
<div class="flex flex-wrap items-center justify-between gap-3">
  <h1>{{ t('nav.brand') }}</h1>
  <nav class="flex flex-wrap items-center gap-3">…</nav>
</div>
```

---

## 3 Grid — responsives Karten-Raster

**Wann:** Gleichartige Karten (Galerien, Bilder, Signups). Spaltenzahl skaliert mit
Viewport.
**Wie:** `grid` plus Breakpoint-Spalten-Klassen.

| Variante | Tailwind | Verwendung |
|----------|----------|------------|
| `grid-cards` | `grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3` | Galerie-Karten im Dashboard |
| `grid-tiles` | `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4` | Bild-Thumbnails im Galerie-Grid |
| `grid-tiles-dense` | `grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-2` | Sehr dichtes Image-Grid (Mobile zuerst) |
| `grid-list` | `grid grid-cols-1 md:grid-cols-2 gap-4` | Zwei-Spalten-Listen (z.B. Audit-Log) |

**Beispiel (Dashboard):**

```html
<div id="gallery-grid" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
  {% for g in galleries %}{% include "dashboard/_gallery_card.html" %}{% endfor %}
</div>
```

**Empty-State im Grid:** Empty-State als `col-span-full`-Kind hängen, damit das
Grid-Target für HTMX immer existiert (siehe `docs/design/build.md` Regel 2):

```html
<div id="gallery-grid" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
  {% for g in galleries %}…{% endfor %}
  {% if not galleries %}
    <div id="dashboard-empty-state" class="col-span-full …">…</div>
  {% endif %}
</div>
```

---

## 4 Container — Seitenbreite + Mantelpadding

**Wann:** Outermost Wrapper jeder Page-Sektion. Begrenzt Lesebreite und gibt
horizontales Padding auf kleinen Viewports.
**Wie:** `mx-auto` + Max-Width-Stufe + Padding-Stufe.

| Variante | Tailwind | Verwendung |
|----------|----------|------------|
| `container-prose` | `max-w-3xl mx-auto px-4 sm:px-6` | Legal-Pages, Long-Form-Lesetext |
| `container-form` | `max-w-md mx-auto px-4` | Auth-Forms (Login, Signup, Setup) |
| `container-page` | `max-w-7xl mx-auto px-4 sm:px-6 lg:px-8` | Default — Dashboard, Galerie-Detail, Audit-Log |
| `container-stage` | `max-w-none px-4 sm:px-6` | Guest-Viewer (Bilder sollen randlos wirken) |

**Beispiel (Dashboard-Page):**

```html
<div class="min-h-screen bg-surface-base">
  <header class="sticky top-0 z-sticky border-b border-border-subtle bg-surface-base/90">
    <h1>{{ t('dashboard.my_galleries') }}</h1>
    <button class="…">{{ t('dashboard.new_gallery') }}</button>
  </header>
  <div id="gallery-grid" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">…</div>
</div>
```

**Vertikales Padding** (`py-*`) ist nicht Teil des Containers — pro Page entscheiden.
Default: `py-8` für Dashboard-artige Pages, `py-16` für Hero/Auth, `py-12` für Legal.

---

## 5 Komposition

Die vier Primitives kombinieren sich frei. Eine typische Section-Komposition:

```html
<!-- container-page -->
<section class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
  <!-- stack -->
  <div class="flex flex-col gap-8">
    <!-- cluster (Header) -->
    <header class="flex flex-wrap items-center justify-between gap-3">
      <h2>…</h2>
      <div class="flex flex-wrap items-center gap-3">…</div>
    </header>
    <!-- grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">…</div>
  </div>
</section>
```

---

## 6 Was diese Primitives NICHT abdecken

- **Tabellen** – nutzen `<table>` mit eigenen Patterns (siehe Audit-Log-Komponente
  in `docs/design/components.md`)
- **Lightbox / Fullscreen-Overlays** – eigene Z-Index-Layer (siehe Z-Index-Tokens
  in `tokens.md` §7)
- **Sidebar-/Sticky-Layouts** – kommen wenn nötig in einer eigenen Sektion;
  aktuell hat Picture-Stage keine. Vorab nicht definieren.

---

## 7 Vorhandene ad-hoc Patterns (Migrations-Kandidaten)

Im heutigen Codebase tauchen u.a. auf:

- `max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8` (6×) → **container-page**
- `max-w-md` Wrapper (8×) → **container-form**
- `flex items-center justify-between mb-{2,4,6,8}` (häufig) → **cluster** mit `justify-between`
- `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4` (Image-Grids) → **grid-tiles**

Beim Page-Redesign (Welle 3) wird jeweils auf das passende Primitive migriert.
**Bewusst keine globale Replace-Aktion jetzt** – die Migration passiert organisch im
jeweiligen Page-Redesign, damit alte und neue Pages parallel funktionieren.
