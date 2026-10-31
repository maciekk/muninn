# Project notes

Working knowledge that would otherwise be lost between sessions: non-obvious
observations, unsuccessful approaches, decision rationale, and open questions.
This is not a changelog or a second usage manual.

## How to use these notes

- Record findings that could save someone repeating an investigation. Include a
  date, context/evidence, and limitations; distinguish observations from hypotheses.
- Keep entries short. Add follow-up evidence or mark an entry superseded rather
  than treating an observation as a permanent instruction.
- Move stable guidance into the relevant documentation and link to it here when
  useful. Delete obsolete notes that no longer help.
- Exclude private product selections, targets, cache contents, personal paths,
  credentials, and browser/session data.

## 2026-10-30 — Private configuration can hide shared knowledge

**Observation:** Local track configurations can encode operational choices and
research caveats that never reach the repository because `tracks/` is ignored.
An otherwise clean Git status does not mean those local files were unchanged.

**Implication:** Preserve broadly useful lessons here or in appropriate public
docs, without copying private configurations. Do not force-add ignored track
files merely to capture a lesson; get explicit agreement before publishing them.

**Context:** Discovered while preparing to commit local target/configuration
updates. Check `.gitignore` and `git status --ignored` when expected changes are
absent. The Obsidian note used in that workflow also lived outside this repo.

## 2026-10-30 — Fetching choices are operational observations

**Observation:** Current local usage favors automated browser fetching for
Amazon Canada and the existing Firefox-session bridge for Newegg Canada, Best
Buy Canada, and Memory Express. This preference is useful context, not a
benchmark proving those methods universally outperform the alternatives.

**Evidence/context:** The workflow is described in [the usage reference](usage.md),
including bridge setup and verification handling. Earlier Newegg link research
encountered security-verification pages with HTTP and automated Chromium.

**Caveat:** Retailer behavior changes. A failed automated fetch does not establish
that a listing is unavailable or that no price exists. Retain stale-price/failure
signals and verify the live offer when making purchase decisions. Revisit the
preferred method when new evidence contradicts it.

## 2026-10-30 — Research anchors are not interchangeable offers

**Observation:** A supplied price analysis distinguished retail-box and bare-drive
offers within the same SSD model family. It also surfaced shopping-result prices
that had not been verified at checkout, and revised targets without establishing
historical Canadian sale lows.

**Implication:** Preserve exact listing/SKU identity and uncertainty when applying
research. Do not silently replace a tracked URL with a seemingly cheaper variant
or write a research quote into observed price history. A technical score is not
purchase priority, and a suggested target is not evidence of a historical low.

**Open question:** When exact URLs/SKUs are missing from research, verify them
before adding offers. No automatic equivalence between packaging variants has
been established by this observation.
