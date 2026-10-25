# snipe

A cache-first, local CLI for watching product prices. It is designed for occasional sale-season use: product configuration, fetched HTML, and SQLite price history all live in the current project directory.

## Install

```sh
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e '.[dev]'
pytest -q

mkdir -p hunts/monitor
cp product.yaml.example hunts/monitor/my-monitor.yaml
# edit hunts/monitor/my-monitor.yaml
snipe check monitor
```

`check` fetches a URL only when its configured freshness window has expired (default: `2h`). HTML is saved under `.snipe/v1/pages/`; all price observations and failed fetches are retained in `.snipe/v1/history.sqlite3`.

If refreshing fails, Snipe reports the last successful price and warns that it is stale. It never replaces a valid cached page with a failed response.

## Hunt and product configuration

A **hunt** is a class of purchase (for example, NVMe drives or monitors). Each hunt is a subdirectory of `./hunts/`, containing one YAML file for each viable product candidate; see `product.yaml.example`.

```text
hunts/
  nvme/
    lexar-nm790-4tb.yaml
    wd-black-sn7100-4tb.yaml
  monitor/
    candidate-one.yaml
```

```sh
snipe check nvme monitor  # named hunts
snipe nvme monitor        # `check` is optional
snipe                     # check every hunt directory
```

Use `--hunts-dir path` to use a different directory. Each retailer entry needs a `name` and `url`. Built-in HTML extractors cover Amazon, Canada Computers, Newegg Canada, and Memory Express. `price_selector` is an optional CSS-selector override for a site whose markup changes. The selector's text must contain the displayed price.

Durations accept positive `m`, `h`, or `d` values, such as `30m`, `2h`, or `1d`. Target prices are met at or below the configured amount. Add optional `notes` as a string or list of strings for buying rationale, tradeoffs, or things to watch; these appear as wrapped bullets in the product details.

## Report indicators

Each hunt begins with a cross-product retailer comparison sorted by desirability
(highest first), then current price (lowest first) within each rating. Unrated
products sort after rated ones; missing prices sort last within a rating. The
`Rating` column is shown in the overview, including `--short` mode. In `--long` mode each product also has a retailer table, a Product Details section for its rating and notes, and separate vendor sections with observation count and numeric low/high. Cache timestamps are shown dimmed at the end of each vendor section.

- `TARGET MET!` — current price is at or below the product target.
- `NEW LOW!` — current price is the lowest successful observation stored for that URL.
- `↓ $…` / `↑ $…` — change from the preceding successful observation.

Use normal scheduling (for example cron) to run `snipe`; the freshness window prevents unnecessary requests. Interactive reports use colour and bold emphasis by default; piped/cron output is plain. Override this with `--color always` or `--color never`.

Reports default to `--short`: only the overall comparison table for each hunt,
without any per-product subsections.
Use `snipe check nvme --long` to include per-product tables, Details, and notes,
or `--short` to explicitly
select the default. These flags are mutually exclusive; warnings are collected in a single section at the end in either mode.

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

`src/snipe/browser.py` isolates the Playwright implementation. Chromium runs
headlessly only for due refreshes, retains cookies in `.snipe/v1/browser-profile/`,
and saves rendered HTML through the same cache as HTTP. It may click an ordinary
“Continue shopping” button once, but never requests human intervention or solves
CAPTCHAs. Known challenges/timeouts become failed fetches with stale-price fallback.
Browser fetching is best-effort, not a guarantee against retailer blocking.
Do not run simultaneous checks sharing the same browser profile. The profile
contains cookies: keep `.snipe/` private and ignored by Git.

Switch back to `fetcher: http` (or remove the field) to disable browser fetching.
Changing fetchers does not bypass the normal refresh delay.

## Candidate desirability

Set optional `desirability: 9.5` in a product YAML (a finite number from 0 to 10).
This is your subjective preference, not a measured performance score. Rating is
the primary overview sort key; price breaks ties. Existing files without ratings
remain valid.

A candidate awaiting verified listings may use `retailers: []`. It appears as
`NO LINKS` and emits a warning rather than preventing the rest of the hunt from
running. Add exact product URLs when found; do not use search-result URLs as
price sources. Availability comments in notes are not live inventory checks.

## Vendor rate limiting

`--vendor-delay SECONDS` sets the minimum interval between top-level fetch starts
for the same vendor (default **2 seconds**; fractional values accepted).

```sh
snipe check nvme --vendor-delay 5
```

The last start time is persisted in `.snipe/v1/history.sqlite3`, shared by all
hunts and subsequent runs in that project. Vendor identity comes from the URL's
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
`--long` product tables. Override the bounds for the hunt being checked:

```sh
snipe check nvme --range-min 500 --range-max 900
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
