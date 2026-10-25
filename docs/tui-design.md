# Possible TUI: evaluation and incremental design

Status: proposal, not implemented. Scope: an optional interactive companion to the CLI, not a replacement.

## Assessment

An overview table with selectable rows and per-product details is a **small-to-medium feature**, not a rewrite. The data already exists: products, retailer results, warnings, and price history. The main prerequisite is separating data collection and report calculations from terminal printing in `src/muninn/cli.py`.

A cached-data browser adds modest complexity. Refreshing while browsing adds materially more: background work, cancellation, progress, SQLite connection ownership, and browser verification. Editing product YAML from the TUI would be another separate project. Do not bundle these together.

The existing CLI should remain the default, with unchanged stdout/stderr, report-file output, refresh rules, and exit-status behavior. Cron and emailed reports should never need to load a TUI library.

## What the code already provides

- `config.py`: typed products/retailers, notes, ratings, targets, and track configuration.
- `store.py`: SQLite observations, last attempts, cached pages, and history queries.
- `visualization.py`: pure range graphics and sparklines reusable by either interface.
- `cli.py`: `Checked` results; refresh/failure handling; metrics; overview sorting; short and detailed rendering.
- `report()` already collects results before rendering, and reuses them for `--long=FILE`. That is a natural extraction point.

The coupling is localized rather than architectural: `report()` owns collection and printing; `refresh_if_needed()` calls terminal progress directly; `metrics()` mixes status decisions with formatted strings; track loading and bounds resolution live in `main()`. The report renderers print directly and depend on terminal width and global color state.

There is no need for a server, HTTP API, daemon, new database, or changed YAML format.

## Proposed first experience

Proposed invocation (not available today):

```sh
uv pip install -e '.[tui]'
muninn --tui monitor nvme
```

An explicit flag avoids taking over the currently valid track name `tui`. Do not automatically enter full-screen mode just because stdout is a terminal. Require an interactive terminal and give an actionable error if the optional dependency is missing. Reject conflicting report-output flags rather than silently ignoring them.

Start with a **cache-only viewer**, with refreshing still performed by the CLI:

1. Select a track; see a scrollable short-style comparison table.
2. Arrow keys or mouse select a row; Enter/click opens product details.
3. Details show all retailer rows, notes, rating, target, URLs, cache timestamps, price histories and numeric extremes, plus warnings beside affected retailers.
4. Focus the retailer corresponding to the selected overview row.
5. Escape returns to the table with selection and scroll position preserved; `q` quits.
6. A reload action rereads the cache after an external CLI check; no polling initially.

The current overview has **one row per retailer**, not one row per product. Preserve that comparison model rather than silently collapsing products to their cheapest listing. Use stable identity derived from track/product source path plus retailer identity, not truncated labels or row positions; retain source paths when loading products.

On narrow terminals, use a separate details screen rather than a mandatory split pane. A split view, search, sorting, and opening a retailer URL can follow once navigation works well.

Make cached-data mode explicit, including last successful observation and latest failure. Preserve CLI short-report filtering by default, but provide a way to reveal missing-price rows so an empty or failing product does not become inaccessible. Candidates with no links remain visible.

## Minimal shared boundary

Suggested organization, not a requirement to create every module immediately:

```text
configuration + Store + fetchers
             |
     report data / collection
          /          \
  CLI text renderer   TUI widgets
```

Extract a small shared report layer, using dataclasses and ordinary functions:

- Track/product/retailer snapshots containing typed values, history, warnings, and range bounds.
- A cache-only snapshot reader that never calls fetchers or rate limiting.
- A refresh operation that retains current scheduling and stale-data behavior.
- Shared status calculation and ordering. Keep Decimal values until presentation, so display rounding cannot affect sorting or target detection.
- A progress callback, defaulting to a no-op; CLI supplies its existing stderr reporter, TUI can later supply messages.
- Shared track loading and range resolution, without requiring argparse or a TUI import.

Both presenters consume the same data and business decisions, but own their layouts and colors. Do not parse the printed CLI report, build widget identity from displayed text, or maintain a second implementation of price-status rules. Capturing `product_report()` output might demonstrate the idea quickly, but its width/global-color dependencies make that a poor permanent boundary.

One semantic detail needs an explicit decision: today `NEW LOW!` depends on `Checked.refreshed`, not just history. A cache-only viewer should not claim that it just discovered a new low. Initially retain that distinction (no refresh event means no `NEW LOW!`); a persistent “at historical low” indicator would be a separate, clearly named enhancement.

No schema change is expected. Note that `Store` initialization currently creates directories/schema and can migrate a legacy cache: “cache-only” means no fetching, not a strictly read-only filesystem connection. True read-only operation would require additional work.

## Framework recommendation

Use **Textual**, installed as an optional `tui` extra and imported only for TUI invocation. Its tables, scrolling, keyboard/mouse handling, screens, layout, and headless UI-testing facilities fit this feature. Validate dependency compatibility with the project's supported Python versions when choosing a version.

Alternatives:

- `curses`: fewer third-party dependencies, substantially more manual table/layout/input work.
- `prompt_toolkit`: viable, but less directly suited to this table-and-detail application.
- A pager over a long report: cheap navigation improvement, but not the requested selectable-row experience.

Textual adds a framework and transitive dependencies to TUI installations. It should not add runtime dependencies or event-loop concepts to ordinary CLI use.

## Later: refresh inside the TUI

This is the main complexity boundary. Keep it a separate increment:

- Show cached results immediately, then refresh only on explicit request initially.
- Run existing synchronous fetching in one background worker, not on the UI event loop. No async rewrite of the network stack is needed.
- Create/use/close the SQLite connection and requests session within that worker. Do not pass the UI thread's SQLite connection to it. Send snapshots/progress back through framework messages.
- Serialize refresh jobs and retain vendor rate limits; prevent repeated keypresses from queuing duplicate jobs.
- Preserve selection while rows change; show refresh failures without discarding previous prices.
- Define cancellation as stopping between requests initially. Threads cannot safely interrupt arbitrary requests or browser work; shutdown and resource cleanup need deliberate handling.
- Handle browser verification explicitly. `browser.fetch_html()` currently calls `input()`; it cannot do that while a full-screen UI owns stdin. Either introduce a confirmation callback/modal bridge or initially leave interactive-browser checks to the CLI. Never silently change a configured fetcher or bypass verification.
- Document concurrent CLI/TUI refresh limitations, especially the shared browser profile. A cached viewer alongside cron is much simpler than two refreshers sharing that profile.

Do not implement force-refresh by casually reusing `mark_due()`: it currently changes an observation timestamp. A future UI refresh command should distinguish “check due URLs” from “force fetch” and preserve historical timestamps.

## Size and effort

Rough engineering estimates for someone familiar with Python and this code, including basic tests; not delivery commitments:

| Increment | Approximate effort | Complexity |
| --- | --- | --- |
| Throwaway table/details demo using collected results | 1–2 days | Low; not a finished integration |
| Shared report boundary + usable cache-only viewer | 3–7 days total | Modest |
| In-app refresh, progress, lifecycle/error handling | Another 3–7 days | Moderate |
| Verification workflow and broader terminal polish | Additional work depending on scope | Moderate and edge-case-heavy |

Expect hundreds of new lines plus tests for the basic viewer, with existing CLI logic moved rather than duplicated. A polished refresh-capable application can grow considerably beyond that. The first version should touch report orchestration and loading, add a small TUI module/package and optional dependency, and leave storage/extraction formats alone.

## Implementation checkpoints, if pursued

1. Extract shared report data/calculations while preserving CLI behavior. Run `uv run pytest`; retain regression coverage for short/long output, file output, sorting, warnings, and refresh decisions.
2. Add cache-only loading tests: no network or browser calls, empty history, failed latest attempt, missing-price success, no-link candidates, and track bounds.
3. Add the optional viewer and UI tests for row-to-product navigation, selected-retailer focus, back/quit, reload, empty states, and narrow layouts. Verify CLI still works without Textual installed.
4. Evaluate with real tracks before adding refresh or editing.
5. If refresh follows, test responsiveness, serialized jobs, warnings, worker errors, cancellation/quit, and verification handoff.

Recommendation: pursue the cache-only overview/detail viewer first if the interaction is valuable. It delivers the central benefit without turning Muninn into a continuously running monitoring application. The CLI remains a first-class frontend, not a compatibility mode.
