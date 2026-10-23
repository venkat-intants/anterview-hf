# UI conventions (web)

The rules every screen in `web/src` follows: which word, which component, which
pattern. Decided in wave 3 of the 2026-10 UX consistency audit; the audit's IDs
(A1-H5) are given where a rule came from a finding. Where a rule is enforced by
a test, the guard is named. For the design system's history and the page
building blocks, see `web/src/design/README.md`.

Staff screens are English only. Candidate screens go through `t()` with EN, HI
and TE values (`lib/i18n.ts`); a counted key needs `_one` and `_other`.

## Words

### Glossary

| Thing | Staff screens | Candidate screens | Never |
|---|---|---|---|
| The thing being hired for | **opening** (the job title is a field on an opening) | **job** | position, posting |
| What a candidate sees before applying | the opening's **public details** | the job | posting |
| A person, before shortlisting (Applicants, resume screening) | **applicant** | - | |
| A person, from shortlisting on (pipeline, drawer, decisions, interviews, offers) | **candidate** | - | |
| The written MCQ round | **exam** | exam | test, assessment |
| The coding round | **coding round** | coding round | coding test |
| The AI assessment panel | **assessment** (only this) | | |
| The uploaded document | **resume** | resume | CV |
| Practice | **practice interview** (noun); **practise** (verb) | same | mock interview |
| Account access | **Sign in**, **Sign out** | same | log in, login, log out |
| A scheduled interview-loop slot | **interview** | interview | session |
| The login state | **session** (only this: "Your session has expired") | same | |

Spelling is British: authorisation, organisation, personalise, cancelled,
catalogue, practise (verb). One exception: the consent and automated-decision
disclosures in `lib/i18n.ts` still say "CV"; their wording changes only with
legal review, and each carries a `ui-copy-exempt:` comment saying so.

### Verbs

| Verb | Means | Example |
|---|---|---|
| **New X** | opens a creation form | "New opening", "New exam" |
| **Create X** | submits that form | "Create opening", "Create HR manager" |
| **Add X** | puts an item into an existing parent | "Add round", "Add to pool" |
| **Save** | stores an edit | "Save", "Save draft", "Save public details" |
| **Cancel** | abandons a form or an edit | the toggle that closes an open form says Cancel |
| **Close** | dismisses a panel that changes nothing | a drawer, a result summary |
| **Try again** | every retry | never "Retry", "Reload" or "Refresh" on a button |
| **Delete** | permanent | |
| **Remove** | takes an item out of a parent; the item still exists | |
| **Archive** | reversible | |

A destructive or revoking action goes through `ConfirmDeleteButton`, whose
confirm button repeats the action's own verb (B1, B2).

### Capitalisation

Sentence case everywhere, candidate text included: "Start interview", "Browse
jobs", "Your scorecard", "Back to dashboard", "Admin overview", "Super admin".
Acronyms (HR, AI, PDF, JD, MCQ, CSV) and product names (AntHire, Google) keep
their capitals. A reference to a named page or tab may use its name ("Add them
under Applicants").

### Feedback copy

| Kind | Pattern | Example |
|---|---|---|
| Error message | `Could not <verb> <object>. <what to do>.` Ends with a period. | "Could not load jobs. Please try again." |
| Error fallback | pass it to `errorMessage(err, fallback)` (`lib/errorMessage.ts`), which never shows "HTTP 500" | `errorMessage(e, 'Could not save the budget.')` |
| Success toast | short, no final period | "Budget saved" |
| A second sentence | goes in the toast's `description`, which is a sentence | `toast.success('Draft saved', { description: 'Candidates still see the published version.' })` |
| Counts | real plurals, never "item(s)" | `plural(n, 'candidate')` from `lib/formatters` on staff screens; i18n `_one` / `_other` on candidate screens |

Never: "Couldn't", "Failed to", "Something went wrong", a bare "X failed".

## Buttons

One component: `Button` from `@/components/ui/button` (`buttonVariants()` for a
`<Link>` styled as a button, or `Button asChild`).

| Role | Variant |
|---|---|
| The one primary action of a view | `default` (primary). One per view. |
| Save | primary; `outline` when it is a secondary action inside a card |
| Publish next to Save draft | Publish primary, Save draft `outline` (A4) |
| Cancel, Close | `ghost` |
| Destructive | `destructive`, behind `ConfirmDeleteButton` |
| Quiet secondary emphasis | `highlight` |

A raw `<button>` is only for toggles, chips, tabs and grid cells. `Pill` is
retired (A1).

## Forms

One set of controls, one height (G5): `Input` (`ui/input`), `Textarea`
(`ui/textarea`) and `SelectControl` (`ui/select-control`, a native `<select>`), with
`FormField` (`ui/form-field`) for the label, hint and error wiring. There is no
Radix `Select`: the legacy `ui/select` was deleted once its last importer moved to
`SelectControl`. The design
primitives `Field` (input with a leading icon) and `SelectField` are built on
`FormField`.

- Height: 40px (`size="md"`, the default), 36px (`size="sm"`, toolbars and dense
  rows): the same as `Button`. A textarea starts at three lines.
- Radius 10px, the `--input` border, accent focus ring, `invalid` (or
  `aria-invalid`) draws the danger border.
- `FormField` hands its control `id`, `aria-describedby` and `aria-invalid`:
  `<FormField label="Name" error={err}>{(p) => <Input {...p} />}</FormField>`.
- A control that cannot be one of these takes its classes from
  `ui/control-styles.ts`; it does not restate them.
- Checkbox, radio, file, hidden and range inputs stay native.

## Tables

Data tables use `Table`, `TableHeader`, `TableBody`, `TableRow`, `TableHead` and
`TableCell` from `@/components/ui/table`. An empty table shows an `EmptyState`
in a row, not ad-hoc text (D4).

## Loading, empty and error states

- Every list or query-driven region handles loading, error and empty. Use
  `QueryState` (`components/QueryState.tsx`): skeletons while loading, a
  `QueryErrorPanel` with **Try again** on failure, then `empty`, then the data.
  A failed load is never shown as an empty state (D1).
- Content areas load with `Skeleton` (`ui/skeleton`). A spinner appears only
  inside a button while its action runs.
- A pulsing status indicator (a live dot) is `motion-safe:animate-pulse`, which
  stops under reduced motion; it is not a placeholder.

## Formatting

- Dates, times, numbers and money only through `lib/formatters`, with the UI
  locale: `formatDate`, `formatDateTime`, `formatTime`, `formatNumber`,
  `formatMoney`, `formatScore`, `plural`. Candidate screens pass
  `uiLocale(i18n.language)`; staff screens use the default `en-IN` (F2).
- A future or scheduled time always shows its zone: `formatDateTime` prints it
  (F3). `formatTime` (no zone) is for a moment already past.
- Scores always carry their scale: `formatScore('resume' | 'exam' | 'round', v)`
  is out of 100, `formatScore('interview', v)` is out of 10 with one decimal (F1).
- A missing value is `-` (`MISSING`). Where a dash reads badly in a sentence,
  write "not set" ("Available from: not set").
- Title separator ` · `, object first: "Python developer · Results",
  "Acme · Careers".

## Layout

- An in-shell page renders `Page` (`design/components/page.tsx`) with a `width`
  (`form`, `default`, `workspace`) and a `PageHeader`; it never sets its own
  `mx-auto`, `max-w-*` or padding (G1; `PageFrameSweep.test.ts`).
- The page title equals its nav label (`components/layout/navSections.tsx`) (G2,
  E1). The HR home greets by name while it can and says "Hiring" otherwise; the
  candidate dashboard's title is its greeting.
- Detail pages carry `Breadcrumbs`. A back link appears only on an error or
  not-found state, worded "Back to <lowercase plural>" ("Back to offers"); when
  the origin is unknown (history back) it says "Go back" (G3).

## Dialogs

`Dialog` and `Sheet` (`ui/dialog`, `ui/sheet`, Radix) for every modal and side
panel: they trap focus, close on Escape and return focus. A hand-built
`role="dialog"` is allowed only for the documented exceptions below (G4).

Removing or deleting a user or a tenant opens a `Dialog` that names the object
(B4), as `StaffRoster` in `CompanyAdminConsole` and `DeleteCompanyDialog` do; an
inline `ConfirmDeleteButton` (above) covers every other destructive action.

## Documented exceptions

| What | Where | Why |
|---|---|---|
| Shell-less full-screen pages | `pages/Scorecard.tsx`, `features/interview/LiveKitInterview.tsx`, `pages/PublicExam.tsx` (and `pages/exam/CodingTaking.tsx`, which it renders) | they own the whole viewport, outside AppShell |
| Hand-built modals | `components/ConsentModal.tsx`, `pages/exam/CameraConsentModal.tsx`, `features/interview/LiveKitInterview.tsx`, `pages/PublicExam.tsx` | the consent gates and the full-screen candidate surfaces |
| Copy that keeps "CV" | consent and automated-decision disclosures in `lib/i18n.ts`, marked `ui-copy-exempt:` | legal wording |
| A raw input inside a shared control | `design/components/primitives.tsx` (`Field`) | it draws the shared box around an icon and an input |

## Guards

All in `web/src/__tests__/guards/`, run by `npm test`. They walk the TypeScript
AST (like `noLongDashes.test.ts`), so comments, identifiers, routes, class lists
and i18n keys are never mistaken for visible text.

| Guard | Enforces |
|---|---|
| `uiCopy.test.ts` | no retired phrase in visible text: mock interview, log in/login/log out, Retry, Couldn't, Failed to, Something went wrong, a bare "X failed", "(s)" plurals, position, posting, CV, American spellings, "practice" as a verb. A `// ui-copy-exempt: <reason>` comment exempts the next string only. |
| `formatLocale.test.ts` | no `toLocaleString` / `toLocaleDateString` / `toLocaleTimeString` or `Intl` formatter without a locale outside `lib/formatters.ts` |
| `noPill.test.ts` | nothing declares, exports, imports or renders `Pill` |
| `uiRatchets.test.ts` | the structural rules below, as ratchets |
| `noLongDashes.test.ts` | no em or en dash in UI text (use " - ", a colon or parentheses) |

### Ratchets

The structural rules are migrated file by file, so each has an allowlist instead
of failing everywhere at once:

| Rule (`allowlists/<id>.txt`) | Breach | Use |
|---|---|---|
| `raw-primary-button` | a raw `<button>` with `bg-primary` | `Button` |
| `native-form-control` | a native `<select>`, `<textarea>` or text-like `<input>` | `Input`, `Textarea`, `SelectControl` |
| `raw-table` | a raw `<table>` | `ui/table` |
| `hand-built-dialog` | `role="dialog"` / `role="alertdialog"` outside the documented exceptions | `Dialog`, `Sheet` |
| `hand-built-skeleton` | `animate-pulse` (other than `motion-safe:`) outside `ui/skeleton` | `Skeleton`, `QueryState` |

How the allowlists work:

- Each list names the files that broke its rule when the guard was written: one
  path per line, relative to `web/src`, forward slashes, sorted, no duplicates.
- The test fails when a file **not** on the list breaks the rule. It does not
  fail when a listed file becomes clean, so migrating a file needs no edit to
  the list.
- Never add a path to get a change through: use the shared component. A new
  permanent exception belongs in the rule's `exempt` list in
  `structuralRules.ts` and in the table above, with its reason.
- Once the area migrations land, one cleanup change deletes the clean paths
  from each list (the test also checks the lists stay sorted and unique).
