# AGENTS.md - rules, skills and memory for every AI agent

## 1. Map (7 files)
| File | Role |
|---|---|
| radar.py | Collect → score → eligibility → site/index.html + jobs.json + summary.md. Tests + Claude hooks. Standard library only. |
| config.json | Keywords (EN + BHS), penalties, bonuses, **sources** (fallback URLs + manual link), `min_score`. |
| benchmark.json | 71 labelled titles (EN + BHS). Must stay 100 %. |
| radar.yml | Copy lives at `.github/workflows/radar.yml`. Plain git + python3, then upload-pages-artifact@v5 + deploy-pages@v5. |
| AGENTS.md · CLAUDE.md · README.md | Rules · Claude entry · deployment checklist. |
Generated: `site/index.html` (deployed, not committed), `jobs.json` (committed memory), `summary.md`.

Commands: `python radar.py --test` · `python radar.py` · `--why "Title"` · `--source WFP` · `--audit` · `--install`

## 2. Rules
1. Never invent qualifications, dates, employers or numbers about Damir. Not documented → `[UNVERIFIED]`.
2. Less is more: 7 tracked files + jobs.json. No packages, no paid services, no AI in the scheduled run.
3. Actions allowed in radar.yml: only upload-pages-artifact@v5+ and deploy-pages@v5+. Check that each runs on Node 24 before bumping it.
4. Pages Source must be "GitHub Actions". "Deploy from a branch" runs GitHub's own workflow, which has the Node 20 warning.
5. Respect robots.txt and anti-bot answers (empty HTTP 202). Never bypass them; use the official API or a manual link.
6. Secrets (RELIEFWEB_APPNAME) must never appear in the page, the logs or jobs.json. `redact()` handles this; keep it.
7. Scoring change → config.json → `--test` 100 % → commit. Wrong result → add it to benchmark.json (ask first).
8. A FAIL source is a source problem, not a scoring problem. Fix its URL or set `"off": true`.
9. End of session: update HANDOFF; add a LESSONS line if something went wrong.

## 3. Skills
**vacancy-screen**: eligibility PASS/FAIL/UNCLEAR per hard requirement → fit /100 (technical 35, sector 20, UN/donor 15, level 15, location 10, language 5) → gaps ✅/⚠️/❌ → APPLY ≥70 · CAUTION 55–69 · NO <55 → top 3 HR risks.
**cv-tailor**: map every keyword to documented evidence; ATS headings, one column, "Month Year"; real titles; "Bosnian/Croatian/Serbian: Native"; output CV + keyword report + match → REVIEW.
**cover-letter**: one page or the character limit; 2–3 evidence examples with numbers (17 preschools, 7 CSWs, EU IPA 2020); address the main risk in one sentence; never the "Bangui team" sentence → REVIEW.
**add-source**: Oracle → `oracle` + host + site (CX_…). Workday → `workday` + host/tenant/site. Cornerstone → `csod`. SuccessFactors → `auto` with `/services/rss/job/` + `/search/` fallback, `link_has: "/job/"`. careersmarketplace → `/SearchJobs/feed/`. Web page → `auto` + `link_has`, or `need_digit`. Shared feed → `tag`. Test with `--source NAME`; items must be > 0.
**REVIEW**: every claim ✅/⚠️/❌; mandatory requirements; ATS; character limits; scoring false positives/misses → **PASS** / **FIX FIRST**.

## 4. Memory
**HANDOFF** (overwrite): Date · Last task · State · Next step · Open questions

**LESSONS**
- V10: menu links ("Građevinarstvo") score like jobs → without link_has a link must contain a 3+ digit ID.
- V10: SuccessFactors RSS returns max 20 items → keyword query + search-page fallback.
- `--audit` runs every time: obsolete actions and ubuntu-latest in ANY workflow file show in the run Summary.
- UN-Habitat has no own feed; it posts on UN Careers → tagged.
- EIB dates are DD/MM/YYYY, Oracle dates are MM/DD/YYYY → never mix them.
- V9 run: the Node 20 warning came from GitHub's managed "pages build and deployment" workflow (jobs build / report-build-status / deploy), not from radar.yml. Fix: Pages Source = GitHub Actions.
- ubuntu-latest → Ubuntu 26 notice: pin ubuntu-24.04.
- ReliefWeb website/RSS returns an empty HTTP 202 to bots ("no element found"). Use API v2 with an approved appname (required since Nov 2025).
- UNjobs: robots.txt forbids collection → manual only. UNJobsFeed: no vacancy links → manual only.
- UNICEF RSS needs more than 3 MB. UNOPS XML needs `&`/entity repair. UN Careers sometimes returns HTML.
- UNDP/IOM: read Oracle JSON, not the page. Keyword score ≠ eligibility. "intern" ≠ "internal". "bih" ≠ "prohibited".
- A failed source keeps its last jobs max 7 days, marked "old data".

**DECISIONS**
| Date | Decision | Why |
|---|---|---|
| 2026-11 | V11: +11 sources (WFP, UNFPA, UN Women, UNIDO, UNESCO, World Bank, GIZ, OSCE, CoE, EBRD, EIB) + UN-Habitat tag; workflow audit | User request; one code path per platform |
| 2026-11 | V10: Pages via Actions (2 Node 24 actions), runner pinned to 24.04 | Removes the warning and the notice |
| 2026-11 | ReliefWeb via API + secret; status SETUP until approved | Website blocks bots |
| 2026-11 | Added Devex RSS, Klix Posao, MojPosao (same-host links + BiH dates) | Local + development jobs |
