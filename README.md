# Damir Job Radar V11

Every 6 hours GitHub collects vacancies from **23 sources**. Each vacancy is scored against your profile (EN + BHS) and checked for eligibility blockers. The results are published as a **web page**.

| Group | Sources |
|---|---|
| UN agencies | UNDP, UNFPA, UN Women, IOM (Oracle) · WFP (Workday) · UNICEF (RSS) · UN Careers, including **UN-Habitat** (shown as "UN Careers · UN-Habitat") · UNOPS · UNIDO, UNESCO |
| Banks / Europe | World Bank · EBRD · EIB · OSCE · Council of Europe · GIZ |
| Aggregators / local | ReliefWeb ×2 (official API) · Impactpool · Devex · Save the Children · Klix Posao · MojPosao |

There is no AI and there are no packages. The workflow uses only 2 actions, both on Node.js 24, and is pinned to Ubuntu 24.04. Each run also audits **all** your workflow files.

## Minimal deployment checklist
| # | Do | Verify |
|---|---|---|
| 1 | **Settings → Pages → Source: GitHub Actions** | This stops GitHub's own "pages build and deployment" workflow, which caused the `upload-artifact@v4` warning. |
| 2 | **Code → `.github/workflows/`**: delete every file except `radar.yml` | Only `radar.yml` is left. |
| 3 | Upload the 7 V11 files, replacing the old ones. Delete the old `index.html` and `.nojekyll` | The repository root holds the 7 files plus `jobs.json`. |
| 4 | Open `.github/workflows/radar.yml` → pencil → paste the full **radar.yml** → Commit | The file contains `ubuntu-24.04` twice, `upload-pages-artifact@v5`, `deploy-pages@v5`, and `--audit`. |
| 5 | **Settings → Actions → General → Workflow permissions: Read and write** | Saved. |
| 6 | **Actions → Job Radar → Run workflow** | **radar** ✓ and **deploy** ✓. Annotations show no Node.js 20 and no Ubuntu 26 messages. The run **Summary** reads "no obsolete actions, no moving runner labels". |
| 7 | Open the link under **deploy** and bookmark it | The page shows today's update time. |
| 8 | Scroll to **Source health** | Each source shows OK / SETUP / FAIL with a reason. Send the FAIL lines for repair. |
| 9 | Optional, for ReliefWeb: request a free appname at apidoc.reliefweb.int. Then **Settings → Secrets and variables → Actions → New repository secret**: `RELIEFWEB_APPNAME` | On the next run, ReliefWeb changes from SETUP to OK. |

## Reading the page
**OK** = no blocker found · **CHECK** = deadline/eligibility not readable · **INELIGIBLE** = internal only / other nationals only / EU only / local post outside BiH / deadline passed (greyed) · *NEW* = last 24 h · *old data* = source failed this run (kept max 7 days).

## On your PC (optional)
`python radar.py --test` · `python radar.py --audit` · `python radar.py --source WFP` · `python radar.py --install` (Claude Code)
