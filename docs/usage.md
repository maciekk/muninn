# Usage reference

[Back to README](../README.md)

## Caching and progress

`check` fetches a URL only when its configured freshness window has expired (default: `2h` after a successful fetch, `1h` after a failed fetch). HTML is saved under `.muninn/v1/pages/`; all price observations and failed fetches are retained in `.muninn/v1/history.sqlite3`. On its first run, Muninn moves an existing `.snipe/v1/` cache into this new location.

If refreshing fails, Muninn reports the last successful price and warns that it is stale. It never replaces a valid cached page with a failed response. During interactive checks, progress uses one updating line on stderr (including cache hits, refreshes, and failures), while the final report remains on stdout. When stderr is redirected (for example, from cron), each progress event is written as a separate line for useful logs.

## Track and product configuration

A **track** is a class of purchase (for example, NVMe drives or monitors). Each track is a subdirectory of `./tracks/`, containing one YAML file for each viable product candidate; see [`product.yaml.example`](../product.yaml.example).

```text
tracks/
  nvme/
    track.yaml
    lexar-nm790-4tb.yaml
    wd-black-sn7100-4tb.yaml
  monitor/
    candidate-one.yaml
```

```sh
muninn check nvme monitor  # named tracks
muninn nvme monitor        # `check` is optional
muninn                     # check every track directory
```

Use `--tracks-dir path` to use a different directory. Each retailer entry needs a `name` and `url`. Built-in HTML extractors cover Amazon, Best Buy Canada, Canada Computers, Dell Canada, Newegg Canada, and Memory Express. `price_selector` is an optional CSS-selector override for a site whose markup changes. The selector's text must contain the displayed price. For marketplace listings, add optional `seller` and `seller_type: third_party` (or `first_party`); third-party listings are marked `[3P]` in reports.

Durations accept positive `m`, `h`, or `d` values, such as `30m`, `2h`, or `1d`. `refresh_after` controls the interval after a successful fetch (default `2h`); `retry_after` controls the interval after a failed fetch (default `1h`). Either may be set at product level and overridden for an individual retailer. Target prices are met at or below the configured amount. Add optional `notes` as a string or list of strings for buying rationale, tradeoffs, or things to watch; these appear as wrapped bullets in the product details.

## Report indicators

Each track begins with a cross-product retailer comparison sorted by desirability
(highest first), then current price (lowest first) within each rating. Unrated
products sort after rated ones; missing prices sort last within a rating. The
`Rating` column is shown in the overview, including `--short` mode. In `--long` mode each product also has a retailer table, a Product Details section for its rating and notes, and separate vendor sections with the full vendor URL, observation count, and numeric low/high. Vendor URLs and cache timestamps are shown dimmed so URLs remain easy to click or copy.

- `TARGET MET!` — current price is at or below the product target.
- `NEW LOW!` — current price is the lowest successful observation stored for that URL.
- `↓ $…` / `↑ $…` — change from the preceding successful observation.

Use normal scheduling (for example cron) to run `muninn`; the freshness window prevents unnecessary requests. Interactive reports use colour and bold emphasis by default; piped/cron output is plain. Override this with `--color always` or `--color never`.

Reports default to `--short`: only the overall comparison table for each track,
without any per-product subsections.
Use `muninn check nvme --long` to include per-product tables, Details, and notes,
or `--short` to explicitly
select the default. These flags are mutually exclusive; each track's warnings follow that track's report sections in either mode.

For a spreadsheet-like visual separation in track overviews, use `--zebra`. It
shades every other product with `#E0E0E0`, a light grey suited to light backgrounds.
All retailer rows for a product share the same background.
Choose a specific background with `--zebra=COLOR`, using an ANSI
colour name (such as `blue` or `bright_black`), a palette index from `0` to
`255`, or a true-colour value such as `#303030`. Zebra shading is opt-in and
only appears when terminal colour is enabled. It applies to track overviews in
both `--short` and `--long` reports, not the per-product detail sections.

## Optional browser fetching

HTTP is the default. For retailers needing rendered HTML, install the optional
browser dependency and Chromium:

```sh
uv pip install -e '.[dev,browser]'
python -m playwright install chromium
```

Select it per retailer:

```yaml
retailers:
  - name: Amazon Canada
    url: https://www.amazon.ca/dp/B0C4KQ9N79
    fetcher: browser
```

`src/muninn/browser.py` isolates the Playwright implementation. `fetcher: browser`
runs Chromium headlessly only for due refreshes, retains cookies in
`.muninn/v1/browser-profile/`, and saves rendered HTML through the same cache as
HTTP. Known challenges/timeouts become failed fetches with stale-price fallback.

For retailers such as Memory Express that only occasionally challenge a browser,
use `fetcher: browser_interactive`. It first uses the persistent profile headlessly;
only when the retailer requests verification does it reopen visibly and pause in
the terminal. Complete the normal retailer verification, then press Enter for Muninn
to read and cache the rendered price. It does not solve or bypass challenges.
Give these links a longer refresh window, for example:

```yaml
  - name: Memory Express
    url: https://www.memoryexpress.com/Products/MXEXAMPLE
    fetcher: browser_interactive
    refresh_after: 24h
```

Use `fetcher: interactive` when every browser fetch should be visible.

Choose an engine per retailer with `browser_engine: chromium` (the default) or
`browser_engine: firefox`. This works with all three browser fetchers. Install
Firefox separately before selecting it:

```sh
uv run playwright install firefox
```

For example, to try visible Firefox for Newegg:

```yaml
  - name: Newegg Canada
    url: https://www.newegg.ca/p/EXAMPLE
    fetcher: interactive
    browser_engine: firefox
    refresh_after: 24h
```

Firefox keeps its own cookies in `.muninn/v1/browser-profile-firefox/`; Chromium
continues using `.muninn/v1/browser-profile/`. Neither uses your everyday browser
profile. A different engine may improve compatibility, but does not guarantee
that a retailer will accept automated browsing.

Browser
fetching is best-effort, not a guarantee against retailer blocking. Do not run
simultaneous checks sharing the same browser profile. The profile
contains cookies: keep `.muninn/` private and ignored by Git.

Switch back to `fetcher: http` (or remove the field) to disable browser fetching.
Changing fetchers does not bypass the normal refresh delay. A failed interactive or browser-interactive attempt instead uses its `retry_after` delay (one hour by default).

### Automatically collect Newegg prices in your existing Firefox

Use `fetcher: bridge` for Newegg Canada to let `muninn check` request rendered
pages from your everyday Firefox session. No Playwright install is needed:

```yaml
  - name: Newegg Canada
    url: https://www.newegg.ca/p/EXAMPLE
    fetcher: bridge
    refresh_after: 24h
```

1. Run `uv run muninn browser-bridge` in the project directory and leave it running.
   Optionally restrict its allowlist with track names, e.g. `browser-bridge nvme`.
2. Open Firefox's `about:debugging#/runtime/this-firefox`. Choose **Load Temporary
   Add-on**, then select `extensions/firefox/manifest.json` in this repository.
   If already installed, click **Reload** beside **Muninn tab importer** to get
   the updated permissions and background worker. If connecting reports
   **Could not establish connection. Receiving end does not exist**, remove the
   temporary add-on and load `manifest.json` again; Firefox may still have the
   older manifest without a background worker. Temporary installs disappear
   when Firefox restarts.
3. Click Firefox's puzzle-piece extensions button → **Muninn tab importer**.
   Paste the token printed by the bridge and click **Connect for automatic
   collection**. Wait for **Connected**, then close the popup.
4. In a second terminal, run:

   ```sh
   uv run muninn reset --vendor newegg.ca  # optional: force refresh even if fresh
   uv run muninn check
   ```

The report fetches due `bridge` URLs one at a time, using normal cache freshness,
retry windows and vendor spacing. The extension creates one **dedicated tab**,
then reuses it; it never navigates your other tabs. The first page opens visibly.
If Newegg requests verification, complete it in that tab: the extension waits up
to 150 seconds for a stable, visible main-product price. It does not solve or bypass
verification. Leave the worker's tab on the requested product/variant while a
check is running. Closing it mid-fetch fails that request; the next request can
create a new dedicated tab.

Only configured `bridge` URLs in the bridge's selected tracks may be requested.
Automatic collection currently supports HTTPS `newegg.ca` / `www.newegg.ca`
only. The final tab URL must match the configured URL exactly (apart from its
fragment); redirects to a different variant or changed query parameters are
rejected. The main product price is used, never a recommendation/comparison or
other seller's price. The worker currently waits for the built-in main-price
markup even if you configure a custom `price_selector` for extraction.

If the worker is disconnected, verification times out, or no main price is found,
the check reports `FETCH_FAILED` and keeps previous data. To retry immediately,
use `muninn check --force` (optionally with a track name), or reset Newegg again. After restarting the bridge, paste its **new token** and
reconnect; after reloading the extension, reconnect too. Click **Disconnect** in
the popup or stop the bridge with **Ctrl-C** to stop automatic collection. Keep
one connected worker and one check at a time per project.

### Import just the current Firefox tab

The same extension still offers **Import current tab** for a one-off import.
Start the bridge, paste its token into the popup, open a configured product page,
and click **Import current tab**. Connecting the automatic worker is not required.
Use `fetcher: manual` if you want only these user-triggered imports instead of
automatic collection during reports. Check the product, variant, seller and
currency before importing. Imports are timestamped at import time.

### Firefox bridge privacy and setup

The bridge listens only on `127.0.0.1:8765`, requires a random pairing token,
accepts at most 10 MiB per page, and does not enable CORS for websites. The
extension has persistent access only to Newegg Canada and loopback, plus
`activeTab` for one-off imports on other configured retailers. Automatic polling
starts only when you click **Connect** and continues with the popup closed.

The token is stored in extension-local storage and, while the bridge is running,
in a mode-0600 `.muninn/v1/bridge.json` file for the check command. Treat the token,
that file and cached HTML as private: pages may contain account information. Do
not proxy or port-forward the bridge. This is a local development extension,
not a signed Firefox Add-ons release. Automated tests simulate the worker;
actual Firefox permission/network behavior depends on your local installation.

### Import a page saved from your normal browser

If a retailer repeatedly blocks automated browsers, select `fetcher: manual`:

```yaml
  - name: Newegg Canada
    url: https://www.newegg.ca/p/EXAMPLE
    fetcher: manual
    refresh_after: 24h
```

Manual retailers never make network requests or open browsers during checks.
They show the last recorded price; once due (or if no price exists), the report
shows `MANUAL_REFRESH_REQUIRED`. `reset` makes them due but does not fetch them.

1. Open the product in your everyday Firefox and wait for its price to render.
2. Press **Cmd-S** and save as **Web Page, HTML only** (not a PDF).
3. Import the HTML, using the **exact URL from your product YAML**, not a temporary
   challenge/redirect URL:

```sh
uv run muninn import --url 'https://www.newegg.ca/p/EXAMPLE' --html ~/Downloads/product.html
# Optionally restrict matching to a track:
uv run muninn import nvme --url 'https://www.newegg.ca/p/EXAMPLE' --html ~/Downloads/product.html
uv run muninn nvme
```

The command uses the configured price extractor (including `price_selector`),
keeps the saved HTML in the cache, and appends a price observation at **import
time**, not the time the file was saved. Existing history is retained, and the
normal freshness window starts again. Challenge pages and pages without an
extractable price are rejected without replacing the existing cache or history.

Import requires a URL configured in the selected tracks and makes no network
requests. **Check that the saved page is the correct product and variant**: the
URL you supply determines which history receives the price; Muninn does not
verify the saved page's product identity. Save pages privately, since they may
contain account information. If HTML-only saves omit the price, try Firefox's
**Web Page, complete** option and import its `.html` file (not the companion folder).

### Manually refresh selected URLs

Use `reset` to make configured URLs eligible for their next check immediately. It preserves observations and cached pages; it only moves the latest attempt timestamp into the past.

```sh
muninn reset nvme                    # all configured retailers in the nvme track
muninn reset nvme --vendor newegg.ca # just Newegg URLs in that track
muninn reset --vendor newegg.ca      # Newegg URLs across every track
muninn check nvme                    # performs the now-due fetches
```

## Candidate desirability

Set optional `desirability: 9.5` in a product YAML (a finite number from 0 to 10).
This is your subjective preference, not a measured performance score. Rating is
the primary overview sort key; price breaks ties. Existing files without ratings
remain valid.

A candidate awaiting verified listings may use `retailers: []`. It appears as
`NO LINKS` and emits a warning rather than preventing the rest of the track from
running. Add exact product URLs when found; do not use search-result URLs as
price sources. Availability comments in notes are not live inventory checks.

## Vendor rate limiting

`--vendor-delay SECONDS` sets the minimum interval between top-level fetch starts
for the same vendor (default **2 seconds**; fractional values accepted).

```sh
muninn check nvme --vendor-delay 5
```

The last start time is persisted in `.muninn/v1/history.sqlite3`, shared by all
tracks and subsequent runs in that project. Vendor identity comes from the URL's
hostname, not its configurable display name; known retailer subdomains share a
bucket. HTTP and browser fetches use the same limiter. Cache hits never wait or
update the timestamp, and failed fetch attempts count. Time already spent fetching
counts toward the interval. `--vendor-delay 0` disables waiting.

This spaces product fetches, not every browser asset request, redirect, or
interstitial click. Separate projects have separate limiters. SQLite coordinates
claims across processes, but concurrent runs sharing a browser profile are still
not supported. If simultaneous runs use different delays, each enforces its own.

## Price range graphic

Displayed amounts round to whole dollars (half-up); history, comparisons, targets,
and sorting retain exact cents. Tables show Target, Now, and a **17-character**
Range graphic instead of numeric Low/High columns. Numeric extremes appear in
`--long` Details per retailer.

- `●`: current price; `T`: target.
- `├─┤`: observed low-to-high interval; `·`: outside that interval to include a target.
- `◎`: current and target occupy the same graphic cell (not necessarily exactly equal).
- Current/target markers take precedence over endpoints when they overlap.
- A flat history has no interval line; missing history shows `—`.

Every row shares a fixed scale, defaulting to **$500–$1,000**, including in
`--long` product tables. Set persistent bounds for a track in
`tracks/<track>/track.yaml` (the legacy-friendly name `config.yaml` is also
accepted):

```yaml
# tracks/nvme/track.yaml
range_min: 500
range_max: 900
```

This file is separate from product YAML files, leaving room for future track-wide
settings. A command-line bound overrides that one value for every selected track:

```sh
muninn check nvme --range-max 950
```

Bounds must be finite, non-negative numbers, with minimum strictly below maximum.
Use `--legend` to print the scale and Range graphic explanation. Prices and targets outside the scale
are clamped to the edges and indicated by `<` or `>`; these overflow indicators
take precedence over other edge markers. Historical extremes also count, so an
arrow can indicate an older outlier even when the current price is inside the
scale. Numeric prices remain unclamped; exact values are preserved in history.
Marker positions use exact amounts, not rounded display prices. The graphic is an
approximate positional aid, not a time-series chart; only the numeric comparison
controls `TARGET MET!`.

### Saving a full report

Use `muninn check nvme --long=report.txt` to write the full report to a UTF-8
text file while keeping the short overview tables (and warnings) on stdout.
The file is overwritten and contains no terminal colour codes. Data is fetched
only once for both outputs. Without a filename, `--long` still prints the full
report to stdout. Optional flag values must use `=` (for example, `--long=report.txt` or
`--zebra=blue`). Bare flags never consume the following command or track name.
