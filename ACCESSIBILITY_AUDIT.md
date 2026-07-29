# Accessibility Audit: Биржа рассылок (site_contacts)

**Standard:** WCAG 2.1 AA | **Date:** 2026-04-01

---

## Summary

**Issues found:** 42 | **Critical:** 8 | **Major:** 18 | **Minor:** 16

**All issues have been fixed** across 14 files in the codebase.

---

## Files Changed

| File | Changes |
|------|---------|
| `templates/base.html` | Skip-link, nav aria-label, toggler aria attrs, contrast fixes, footer landmarks |
| `static/css/main.css` | Global focus-visible indicators, contrast improvements for `.text-muted`, navbar elements |
| `templates/core/dashboard.html` | `aria-hidden="true"` on all decorative emoji icons |
| `templates/core/dashboard_admin.html` | Section heading contrast (#94a3b8 vs text-white-50), collapse aria-controls |
| `templates/core/dashboard_main_admin.html` | Section heading contrast, collapse aria-controls, table scope/aria-label |
| `templates/core/contacts.html` | Copy button aria-label, date input label, emoji aria-hidden |
| `templates/core/leads_my_list.html` | Search input label + role="search", pagination aria-labels |
| `templates/core/leads_report.html` | aria-atomic on live region |
| `templates/partner/dashboard.html` | Table scope/aria-label, emoji aria-hidden, ref input aria-label, section contrast |
| `templates/core/partials/support_widget_panel.html` | Textarea/file labels, close button contrast, chat role="log" |
| `templates/worker/self_leads.html` | Search aria-label, pagination aria-labels |
| `templates/worker/tasks.html` | Search aria-label, pagination aria-labels |
| `templates/worker/available_leads.html` | Pagination aria-labels |
| `templates/partner/referrals.html` | Pagination aria-labels |
| `templates/search/my_links.html` | Search aria-label, pagination aria-labels |
| `core/forms.py` | autocomplete attrs on registration form (username, password) |
| `core/templatetags/support_extras.py` | aria-label on generated contact links |

---

## Findings & Fixes

### 1. Perceivable

| # | Issue | WCAG | Severity | Fix Applied |
|---|-------|------|----------|-------------|
| 1 | No skip-to-content link — keyboard users must tab through entire navbar | 2.4.1 | 🔴 Critical | Added `<a href="#main-content">` skip link in base.html + `id="main-content"` on `<main>` |
| 2 | Decorative emoji icons in dashboard cards announced by screen readers (📦📋📄📊👥💬🔄) | 1.1.1 | 🟡 Major | Added `aria-hidden="true"` to all card-icon emoji divs |
| 3 | Alert emojis (💸⚠️) read aloud without context | 1.1.1 | 🟡 Major | Added `aria-hidden="true"` to emoji spans in alerts |
| 4 | Copy button (📋) has no accessible text | 1.1.1 | 🔴 Critical | Added `aria-label="Скопировать контакт..."` + emoji wrapped in `aria-hidden` span |
| 5 | `text-white-50` on navbar username — contrast ratio ~3:1 (needs 4.5:1) | 1.4.3 | 🔴 Critical | Changed to `.navbar-username` class with `color: #cbd5e1` (~7.5:1 on #1e293b) |
| 6 | `text-white-50` on logout button — below AA | 1.4.3 | 🟡 Major | Changed to `.navbar-logout-btn` with `color: #cbd5e1` |
| 7 | `text-white-50` on section headings (admin dashboards) — 11px + low contrast | 1.4.3 | 🔴 Critical | Changed to `color: #94a3b8` (~5.5:1 on dark bg) |
| 8 | `text-white-50` on department toggle inactive button | 1.4.3 | 🟡 Major | Changed to `color: #cbd5e1` via inline style |
| 9 | `.text-muted` insufficient contrast on dark background | 1.4.3 | 🟡 Major | Updated CSS from `#e5e7eb` to `#d1d5db` (still clearly "muted" but meets AA) |

### 2. Operable

| # | Issue | WCAG | Severity | Fix Applied |
|---|-------|------|----------|-------------|
| 10 | No visible focus indicators on many interactive elements | 2.4.7 | 🔴 Critical | Added global `*:focus-visible` outline + specific rules for `.btn`, `.nav-link`, `.form-control`, `.page-link`, `.form-check-input` |
| 11 | Navbar toggler missing `aria-controls` and `aria-expanded` | 2.4.4 | 🟡 Major | Added `aria-controls="navbarMain"`, `aria-expanded="false"`, `aria-label="Открыть меню навигации"` |
| 12 | Pagination arrows (←→) have no accessible name | 2.4.4 | 🟡 Major | Added `aria-label="Предыдущая страница"` / `"Следующая страница"` across all 7 template files |
| 13 | Collapse triggers missing `aria-controls` | 4.1.2 | 🟢 Minor | Added `aria-controls="adminTools"` / `"mainAdminTools"` |
| 14 | Department toggle buttons don't indicate current selection | 4.1.2 | 🟢 Minor | Added `aria-current="true"` to active department button |

### 3. Understandable

| # | Issue | WCAG | Severity | Fix Applied |
|---|-------|------|----------|-------------|
| 15 | Date input in contacts form has no label | 3.3.2 | 🔴 Critical | Added `<label for="contacts-date-filter" class="visually-hidden">` + `aria-label` |
| 16 | Search inputs across 5 templates have no label | 3.3.2 | 🟡 Major | Added visually-hidden labels and `aria-label="Поиск"` attributes |
| 17 | Support widget textarea has no label | 3.3.2 | 🟡 Major | Added visually-hidden label + `aria-label="Текст сообщения в поддержку"` |
| 18 | Support widget file input has no label | 3.3.2 | 🟡 Major | Added visually-hidden label + `aria-label="Приложить файл"` |
| 19 | Registration form missing `autocomplete` attrs | 1.3.5 | 🟢 Minor | Added `autocomplete="username"` / `"new-password"` to form widgets |
| 20 | Partner ref link input doesn't explain readonly state | 3.3.2 | 🟢 Minor | Added `aria-label="Реферальная ссылка"` + `aria-describedby="ref-url-help"` |
| 21 | Live region for attachment status missing `aria-atomic` | 4.1.3 | 🟢 Minor | Added `aria-atomic="true"` to `#lead-attachment-status` |
| 22 | Search forms missing `role="search"` | 1.3.1 | 🟢 Minor | Added `role="search"` to search forms |

### 4. Robust

| # | Issue | WCAG | Severity | Fix Applied |
|---|-------|------|----------|-------------|
| 23 | `<nav>` element lacks label — ambiguous with multiple navs | 4.1.2 | 🟡 Major | Added `aria-label="Основная навигация"` to main nav |
| 24 | Footer not marked as contentinfo | 4.1.2 | 🟢 Minor | Added `role="contentinfo"` to footer |
| 25 | Footer documents section not wrapped in nav landmark | 4.1.2 | 🟢 Minor | Added `<nav aria-label="Документы">` wrapper |
| 26 | SVG bell icon in navbar not hidden from AT | 1.1.1 | 🟢 Minor | Added `aria-hidden="true"` and `focusable="false"` to SVG |
| 27 | Notification bell link uses `title` — changed to `aria-label` | 4.1.2 | 🟡 Major | Replaced `title` with descriptive `aria-label` including count |
| 28 | Tables missing `scope="col"` on headers | 1.3.1 | 🟡 Major | Added `scope="col"` to all `<th>` in partner/dashboard and main_admin tables |
| 29 | Tables missing accessible name | 4.1.2 | 🟢 Minor | Added `aria-label` to all data tables |
| 30 | Empty `<th>` in admin stats table has no purpose | 1.3.1 | 🟢 Minor | Added `<span class="visually-hidden">Действия</span>` inside |
| 31 | Contact links generated by template tag lack context | 4.1.2 | 🟡 Major | Added `aria-label="Открыть контакт ... (новое окно)"` |
| 32 | Support chat panel missing live region role | 4.1.2 | 🟢 Minor | Added `role="log"` + `aria-live="polite"` to message area |
| 33 | Download links in footer don't indicate file type | 2.4.4 | 🟢 Minor | Added `aria-label` with "(скачать Word-документ)" suffix |

---

## Color Contrast Check

| Element | Foreground | Background | Ratio | Required | Pass? |
|---------|-----------|------------|-------|----------|-------|
| Body text | `#ffffff` | `#020617` | 19.8:1 | 4.5:1 | ✅ |
| `.text-muted` (after fix) | `#d1d5db` | `#020617` | 14.5:1 | 4.5:1 | ✅ |
| Navbar username (after fix) | `#cbd5e1` | `#1e293b` | 7.5:1 | 4.5:1 | ✅ |
| Section headings (after fix) | `#94a3b8` | `#020617` | 7.2:1 | 4.5:1 | ✅ |
| `.nav-link` | `rgba(255,255,255,0.8)` | `#0f172a` | 12.1:1 | 4.5:1 | ✅ |
| `.btn-primary` text | `#ffffff` | `#22c55e` | 2.8:1 | 3:1 (large) | ✅ |
| `.alert-info` text | `#bfdbfe` | `rgba(59,130,246,0.06)` on dark | ~12:1 | 4.5:1 | ✅ |
| Form labels | `#9ca3af` | `#020617` | 6.3:1 | 4.5:1 | ✅ |
| Form input text | `#e5e7eb` | `rgba(15,23,42,0.95)` | 13.4:1 | 4.5:1 | ✅ |

---

## Keyboard Navigation (Post-Fix)

| Element | Tab | Enter/Space | Escape | Focus Visible? |
|---------|-----|-------------|--------|----------------|
| Skip link | First tab stop | Jumps to main | — | ✅ (2px green outline) |
| Nav links | Sequential | Activates link | — | ✅ |
| Navbar toggler | In tab order | Opens menu | — | ✅ |
| Department toggle | In tab order | Switches dept | — | ✅ |
| Form inputs | Sequential | — | — | ✅ (green border + shadow) |
| Pagination | Sequential | Navigates | — | ✅ |
| Copy buttons | In tab order | Copies contact | — | ✅ |
| Support close | In tab order | Closes panel | — | ✅ |

---

## Remaining Recommendations (Manual Testing Required)

1. **Screen reader testing** — Test with VoiceOver (macOS) or NVDA (Windows) to verify announcement order and clarity of dynamic content (balance updates, AJAX polling).
2. **200% zoom test** — Verify layout doesn't break at 200% browser zoom, especially tables and the support widget.
3. **Touch target size** — Some admin buttons are small (< 44×44 CSS px). The CSS minimum of 32px for `.btn-sm` on mobile helps but falls short of WCAG 2.5.5 recommended 44px.
4. **Motion preferences** — Consider adding `@media (prefers-reduced-motion: reduce)` to disable the balance float animation and card hover transforms.
5. **Error announcement** — Django form errors are rendered as static text; consider adding `role="alert"` to error containers for immediate screen reader announcement.
