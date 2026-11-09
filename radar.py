"""Damir Job Radar V11 - one file, Python standard library only. No AI, no packages.

  python radar.py            collect all sources -> site/index.html (web page) + jobs.json (memory) + summary.md
  python radar.py --test     self-tests + benchmark.json (must be 100 %, exit 1 otherwise)
  python radar.py --why "Title" ["text"]   explain one score
  python radar.py --source NAME           collect only sources whose name contains NAME
  python radar.py --audit    check .github/workflows/*.yml for obsolete actions and moving runner labels
  python radar.py --install  create hidden files (.github/workflows/radar.yml, .claude/...)
  python radar.py --guard | --gate        Claude Code hooks (used by .claude/settings.json)
"""
import concurrent.futures as cf, datetime as dt, html, json, os, re, shutil, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request, urllib.robotparser
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
UA = "DamirJobRadar/11 (+personal job search; 4 runs per day)"
MAX_BYTES = 40_000_000           # lesson V12: UNICEF RSS failed at a 3 MB cap
TODAY = dt.date.today()
APPNAME = os.environ.get("RELIEFWEB_APPNAME", "").strip()
BIH = re.compile(r"bosnia|herzegovina|\bbih\b|sarajevo|mostar|banja luka|tuzla|zenica|bihać|bihac|brčko|brcko", re.I)


# ---------------------------------------------------------------- scoring
def _rx(words):
    """Whole-word match; a keyword ending in * is a prefix (građevin* hits građevinski).
    Lesson V8: plain prefix matching made 'intern' hit 'Internal' and wrongly penalised good jobs."""
    parts = sorted((re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) + r"(?!\w)" for w in words),
                   key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")", re.I)


TRACKS = {k: _rx(v) for k, v in CFG["tracks"].items()}
HARD, SOFT = _rx(CFG["hard_negative"]), _rx(CFG["soft_negative"])
BONUS = [(k, v, _rx([k])) for k, v in {**CFG["location_bonus"], **CFG["level_bonus"]}.items() if v]


def score(title, text=""):
    """Keyword match 0-100 -> (score, best track, reasons). Title counts x title_weight."""
    tw, pts, why, best, best_n = CFG["title_weight"], 0, [], "-", 0
    for name, r in TRACKS.items():
        th = {m.lower() for m in r.findall(title)}
        bh = {m.lower() for m in r.findall(text)} - th
        pts += 15 * tw * len(th) + 10 * len(bh)
        why += sorted(th) + sorted(bh)
        if tw * len(th) + len(bh) > best_n:
            best, best_n = name, tw * len(th) + len(bh)
    s, blob = min(pts, 80), f"{title} {text}"
    for k, v, r in BONUS:   # whole words: "bih" must not hit "prohibited"
        if r.search(blob):
            s += v; why.append("+" + k)
    if HARD.search(title) or (HARD.search(text) and not best_n):
        s = 0; why.append("hard-negative")
    elif SOFT.search(title):
        s //= 3; why.append("soft-negative")
    return min(s, 100), best, why


# ---------------------------------------------------------------- eligibility (lesson V15/V16)
INTERNAL = re.compile(r"internal (candidates|applicants) only|only (open to )?internal (candidates|applicants)"
                      r"|tier\s*[0-2](\s*/\s*[0-2])*\s+(candidates\s+)?only", re.I)
NATIONALS = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)\s+nationals\s+only|"
                       r"only\s+(?:open\s+to\s+)?nationals\s+of\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)", re.I)
EU_ONLY = re.compile(r"\bEU(?:/EEA)?\s+(nationals|citizens)\s+only|nationals?\s+of\s+(an?\s+)?EU\s+member\s+state", re.I)
LOCAL = re.compile(r"\b(NO-?[A-D]|NO[A-D]-?\d?|G-?[1-7]|GS-?[1-7]|NPSA|SB-?[1-5]|LICA|national officer|"
                   r"national (individual )?consultan\w*|locally recruited|local recruitment)\b", re.I)


def eligibility(title, text, deadline):
    """-> (status, reason). INELIGIBLE = explicit blocker; CHECK = something unknown; OK = nothing found."""
    blob = f"{title} {text}"
    if INTERNAL.search(blob):
        return "INELIGIBLE", "internal candidates only"
    for m in NATIONALS.finditer(blob):
        who = (m.group(1) or m.group(2) or "").strip()
        if not BIH.search(who) and not re.search(r"bosnian|herzegovinian", who, re.I):
            return "INELIGIBLE", f"{who} nationals only"
    if EU_ONLY.search(blob):
        return "INELIGIBLE", "EU nationals only"
    if LOCAL.search(blob) and not BIH.search(blob):
        return "INELIGIBLE", "local/national post outside BiH"
    if deadline and deadline < TODAY:
        return "INELIGIBLE", "deadline passed"
    if not deadline:
        return "CHECK", "deadline not found - open the vacancy"
    return "OK", "no blocker found in the text"


# ---------------------------------------------------------------- dates
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RX = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ ,-]+(\d{4})"), lambda m: (int(m[3]), MONTHS.get(m[2][:3].lower()), int(m[1]))),
    (re.compile(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})"), lambda m: (int(m[3]), MONTHS.get(m[1][:3].lower()), int(m[2]))),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),          # Oracle: MM/DD/YYYY
    (re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),  # BiH: 21. 11. 2026.
]
DEADLINE_KEY = re.compile(r"(deadline|closing date|closes|apply before|end date|post end date|application deadline|"
                          r"prijava do|rok za prijav\w*|rok prijave|konkurs traje do)\W{0,4}(.{0,40})", re.I)


def parse_date(s):
    for rx, f in DATE_RX:
        m = rx.search(s or "")
        if m:
            try:
                y, mo, d = f(m)
                return dt.date(y, mo, d)
            except (TypeError, ValueError):
                continue
    return None


def all_dates(s):
    out = []
    for rx, f in DATE_RX:
        for m in rx.finditer(s or ""):
            try:
                y, mo, d = f(m)
                out.append(dt.date(y, mo, d))
            except (TypeError, ValueError):
                pass
    return out


def find_deadline(text, last_date=False):
    for m in DEADLINE_KEY.finditer(text or ""):
        d = parse_date(m.group(2))
        if d:
            return d
    if last_date:  # Klix cards show "09.11. – 09.12.2026": the only full date is the closing date
        ds = all_dates(text)
        return max(ds) if ds else None
    return None


# ---------------------------------------------------------------- fetching
_robots = {}


def allowed(url):
    """Lesson V12 (UNjobs): respect robots.txt, never work around it."""
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
        except Exception:
            rp.parse([])  # no robots.txt (or unreachable) = no restriction declared
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def redact(s):
    return s.replace(APPNAME, "***") if APPNAME else s


def get(url, accept="*/*", data=None, headers=None):
    """GET (or POST when data is given). Respects robots.txt; retries once on 5xx/network errors."""
    if not allowed(url):
        raise PermissionError("robots.txt disallows this URL - skipped on purpose")
    for attempt in (1, 2):
        try:
            h = {"User-Agent": UA, "Accept": accept, "Accept-Language": "en,bs;q=0.8", **(headers or {})}
            body_out = None
            if data is not None:
                body_out = json.dumps(data).encode("utf-8")
                h["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=body_out, headers=h)
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("response larger than 40 MB")
                if not body.strip():
                    # Lesson V9 (ReliefWeb): HTTP 202 + empty body = anti-bot challenge. Not bypassed.
                    raise ConnectionRefusedError(f"HTTP {r.status} with empty body - site blocks automated readers")
                return body.decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(5)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def looks_html(body):
    head = body.lstrip()[:500].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<body" in head


def repair_xml(body):
    """Lesson V10/V11 (UNOPS): bare '&', HTML entities and control characters break XML parsing."""
    body = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", body)
    xml_ok = {"amp", "lt", "gt", "quot", "apos"}

    def ent(m):
        if m[1] in xml_ok:
            return m[0]
        u = html.unescape(m[0])
        return "".join(f"&#{ord(c)};" for c in u) if u != m[0] else "&amp;" + m[1] + ";"
    body = re.sub(r"&([a-zA-Z]+);", ent, body)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", body).lstrip("\ufeff \r\n\t")


def parse_feed(body):
    if looks_html(body):
        raise ValueError("got a web page, not a feed (URL changed or access page)")
    root = ET.fromstring(repair_xml(body).encode("utf-8"))
    out = []
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: c for c in it}
        link = f.get("link")
        link = ((link.text or "").strip() or link.get("href", "")) if link is not None else ""
        desc = f.get("description", f.get("summary", f.get("content")))
        out.append({"title": clean(f["title"].text if "title" in f else ""), "link": link.strip(),
                    "text": clean(desc.text if desc is not None else "")[:4000]})
    return out


A_TAG = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def parse_links(body, base, link_has="", min_text=12, same_host=False, need_digit=None):
    """Every link whose text looks like a job title.
    Lesson V10: menu links such as "Građevinarstvo" or "Urban planning" score like jobs. Without a link_has
    pattern, a link must carry a number in its path or query (job ads have IDs; menu links usually do not)."""
    host, out = urllib.parse.urlsplit(base).netloc, []
    need_digit = (not link_has) if need_digit is None else need_digit
    for href, inner in A_TAG.findall(body):
        t, url = clean(inner), urllib.parse.urljoin(base, html.unescape(href))
        if len(t) < min_text or link_has not in url or not url.startswith("http"):
            continue
        if same_host and urllib.parse.urlsplit(url).netloc != host:
            continue
        p = urllib.parse.urlsplit(url)
        if need_digit and not re.search(r"\d{3,}", p.path + "?" + p.query):
            continue
        out.append({"title": t[:200], "link": url, "text": t})
    return out


def oracle(src):
    """Lesson V16: Oracle Recruiting Cloud (UNDP, IOM, Save the Children) - read the site's own public JSON."""
    host, site = src["host"], src["site"]
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    out, offset = [], 0
    while offset < 1000:
        q = (f"?onlyData=true&expand=requisitionList.secondaryLocations"
             f"&finder=findReqs;siteNumber={site},limit=100,offset={offset},sortBy=POSTING_DATES_DESC")
        data = json.loads(get(api + q, "application/json"))
        reqs = (data.get("items") or [{}])[0].get("requisitionList") or []
        for r in reqs:
            out.append({"title": r.get("Title", ""), "id": str(r.get("Id", "")),
                        "link": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r.get('Id')}/",
                        "text": clean(f"{r.get('PrimaryLocation', '')} {r.get('ShortDescriptionStr', '')}")})
        if len(reqs) < 100:
            break
        offset += 100
    return out


def oracle_details(src, job):
    """Closing date + full text, only for jobs that already matched (keeps requests low)."""
    url = (f"https://{src['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
           f"?expand=all&onlyData=true&finder=ById;Id=%22{job['id']}%22,siteNumber={src['site']}")
    d = (json.loads(get(url, "application/json")).get("items") or [{}])[0]
    job["text"] = clean(" ".join(str(d.get(k) or "") for k in
                                 ("PrimaryLocation", "ExternalDescriptionStr", "ExternalQualificationsStr",
                                  "ExternalResponsibilitiesStr")))[:6000]
    end = d.get("ExternalPostedEndDate") or ""
    job["deadline"] = parse_date(end[:10]) if end else None


class NeedsSetup(Exception):
    pass


def reliefweb(src):
    """ReliefWeb's website/RSS answer bots with an empty HTTP 202. The official route is API v2 with an
    approved appname (required since 1 Nov 2025). Request one once, store it as secret RELIEFWEB_APPNAME."""
    if not APPNAME:
        raise NeedsSetup("add repository secret RELIEFWEB_APPNAME (free, request at apidoc.reliefweb.int)")
    p = [("appname", APPNAME), ("limit", "200"), ("sort[]", "date.created:desc"), ("profile", "full")]
    for f in ("title", "url_alias", "url", "date.closing", "country.name", "source.name", "body"):
        p.append(("fields[include][]", f))
    if src.get("query"):
        p.append(("query[value]", src["query"]))
    if src.get("country"):
        p += [("filter[field]", "country.exact")] + [("filter[value][]", c) for c in src["country"]]
    data = json.loads(get("https://api.reliefweb.int/v2/jobs?" + urllib.parse.urlencode(p), "application/json"))
    out = []
    for d in data.get("data", []):
        f = d.get("fields", {})
        place = ", ".join(c.get("name", "") for c in f.get("country") or [])
        org = ", ".join(c.get("name", "") for c in f.get("source") or [])
        end = (f.get("date") or {}).get("closing", "")
        out.append({"title": f.get("title", ""), "link": f.get("url_alias") or f.get("url", ""),
                    "text": clean(f"{place} {org} {f.get('body', '')}")[:5000],
                    "deadline": parse_date(end[:10]) if end else None})
    return out


def workday(src):
    """WFP (Workday): the career site's own public JSON - list, then details only for matching jobs."""
    base = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}"
    out, offset = [], 0
    while offset < 400:
        data = json.loads(get(base + "/jobs", "application/json",
                              {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}))
        posts = data.get("jobPostings") or []
        for p in posts:
            path = p.get("externalPath", "")
            out.append({"title": p.get("title", ""), "path": path,
                        "link": f"https://{src['host']}/en-US/{src['site']}{path}",
                        "text": clean(f"{p.get('locationsText', '')} {' '.join(p.get('bulletFields') or [])}")})
        if len(posts) < 20:
            break
        offset += 20
    return out


def workday_details(src, job):
    base = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}"
    info = json.loads(get(base + job["path"], "application/json")).get("jobPostingInfo") or {}
    job["text"] = clean(f"{info.get('location', '')} {info.get('jobDescription', '')}")[:6000]
    end = info.get("endDate") or ""
    job["deadline"] = parse_date(end[:10]) if end else find_deadline(job["text"])


def csod(src):
    """World Bank (Cornerstone): the career page embeds a short-lived public token that its own job list uses."""
    home = f"https://{src['host']}/ux/ats/careersite/{src['site']}/home?c={src['company']}"
    m = re.search(r'"token"\s*:\s*"([^"]+)"', get(home, "text/html"))
    if not m:
        raise ValueError("career page token not found - page layout changed")
    payload = {"careerSiteId": int(src["site"]), "cultureId": 1, "cultureName": "en-US", "pageNumber": 1,
               "pageSize": 200, "searchText": "", "cities": [], "countryCodes": [], "states": [], "placeID": "",
               "radius": None, "postingsWithinDays": None, "customFieldCheckboxKeys": [],
               "customFieldDropdowns": [], "customFieldRadios": []}
    data = json.loads(get(f"https://{src['host']}/services/x/career-site/v1/search", "application/json", payload,
                          {"Authorization": "Bearer " + m.group(1), "csod-accept-language": "en-US"}))
    out = []
    for r in (data.get("data") or {}).get("requisitions") or []:
        locs = "; ".join(f"{l.get('city') or ''} {l.get('country') or ''}".strip() for l in r.get("locations") or [])
        out.append({"title": r.get("displayJobTitle", ""), "text": locs,
                    "link": f"https://{src['host']}/ux/ats/careersite/{src['site']}/home/requisition/"
                            f"{r.get('requisitionId')}?c={src['company']}"})
    return out


EIB_ROW = re.compile(r"Job Title (.+?) Job ID (\d+) Location (.+?) Job Function .+?Close Date "
                     r"(\d{2}/\d{2}/\d{4}|Open Until Filled)", re.S)


def eib(src):
    """EIB (PeopleSoft): read the server-rendered result list. Dates are DD/MM/YYYY here."""
    text, out = clean(get(src["urls"][0], "text/html")), []
    for title, jid, place, close in EIB_ROW.findall(text):
        d = None
        if close[0].isdigit():
            dd, mm, yy = close.split("/")
            d = dt.date(int(yy), int(mm), int(dd))
        out.append({"title": title.strip(), "text": place, "deadline": d,
                    "link": src["job_link"].format(id=jid)})
    return out


def collect(src):
    """Try each URL of a source until one works (fallback URLs = reliability)."""
    if src["type"] == "oracle":
        return oracle(src), src["host"]
    if src["type"] == "reliefweb":
        return reliefweb(src), "api.reliefweb.int v2"
    if src["type"] == "workday":
        return workday(src), src["host"]
    if src["type"] == "csod":
        return csod(src), src["host"]
    if src["type"] == "eib":
        return eib(src), "erecruitment.eib.org"
    errors = []
    for url in src["urls"]:
        try:
            body = get(url)
            if src["type"] == "rss" or not looks_html(body):
                jobs = parse_feed(body)
            else:
                jobs = parse_links(body, url, src.get("link_has", ""), src.get("min_text", 12),
                                   src.get("same_host", False), src.get("need_digit"))
            if jobs:
                return jobs, url
            errors.append(f"{url}: 0 vacancies recognised")
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError(" | ".join(errors))


# ---------------------------------------------------------------- pipeline
def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def run_source(src):
    jobs, used = collect(src)
    hits = []
    for j in jobs:
        if not j["title"] or not j["link"]:
            continue
        s, track, why = score(j["title"], j["text"])
        if s < CFG["min_score"]:
            continue
        details = {"oracle": oracle_details, "workday": workday_details}.get(src["type"])
        if details and len(hits) < 40:
            try:
                details(src, j)
                s, track, why = score(j["title"], j["text"])
            except Exception:
                j["deadline"] = None
        dl = j.get("deadline") or find_deadline(j["text"], src.get("last_date_is_deadline")) or find_deadline(j["title"])
        status, reason = eligibility(j["title"], j["text"], dl)
        name = src["name"]
        for org, marker in (src.get("tag") or {}).items():   # e.g. UN-Habitat inside the UN Careers feed
            if marker.lower() in f"{j['title']} {j['text']}".lower():
                name = f"{src['name']} · {org}"
        hits.append({"title": j["title"][:200], "link": j["link"], "source": name, "origin": src["name"], "score": s,
                     "track": track, "why": why[:8], "deadline": dl.isoformat() if dl else "",
                     "status": status, "reason": reason, "snippet": j["text"][:300]})
    return len(jobs), used, hits


def load_state():
    try:
        return json.load(open(os.path.join(ROOT, "jobs.json"), encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"jobs": [], "sources": {}}


def radar(only=""):
    state, now = load_state(), dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    old = {j["link"]: j for j in state["jobs"]}
    sources = [s for s in CFG["sources"] if only.lower() in s["name"].lower() and not s.get("off")]
    fresh, health, keep_from_failed = [], {}, set()
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(run_source, s): s for s in sources}
        for f in cf.as_completed(futs):
            s = futs[f]
            prev = state["sources"].get(s["name"], {})
            base = {"items": 0, "matches": 0, "last_ok": prev.get("last_ok", "never"), "manual": s.get("manual", "")}
            try:
                n, used, hits = f.result()
                fresh += hits
                health[s["name"]] = {**base, "status": "OK", "items": n, "matches": len(hits), "last_ok": now, "detail": used}
            except NeedsSetup as e:
                health[s["name"]] = {**base, "status": "SETUP", "detail": str(e)}
            except Exception as e:
                keep_from_failed.add(s["name"])
                health[s["name"]] = {**base, "status": "FAIL", "detail": redact(f"{type(e).__name__}: {str(e)[:300]}")}
    # memory: keep first_seen; keep jobs of FAILED sources max 7 days (lesson V11), marked stale
    jobs, seen_links, seen_keys = [], set(), set()
    for j in sorted(fresh, key=lambda j: -j["score"]):
        key = norm(j["title"]) + "|" + j["deadline"]
        if j["link"] in seen_links or (j["deadline"] and key in seen_keys):
            continue  # duplicate (same vacancy on two sites)
        seen_links.add(j["link"]); seen_keys.add(key)
        j["first_seen"] = old.get(j["link"], {}).get("first_seen", now[:10])
        j["stale"] = False
        jobs.append(j)
    for j in state["jobs"]:
        age = (TODAY - dt.date.fromisoformat(j.get("first_seen", TODAY.isoformat()))).days
        if j.get("origin", j["source"]) in keep_from_failed and j["link"] not in seen_links and age <= 7:
            if not j["deadline"] or dt.date.fromisoformat(j["deadline"]) >= TODAY:
                j["stale"] = True
                jobs.append(j)
    rank = {"OK": 0, "CHECK": 1, "INELIGIBLE": 2}
    jobs.sort(key=lambda j: (rank[j["status"]], -j["score"], j["deadline"] or "9999"))
    state = {"updated": now, "jobs": jobs, "sources": health}
    json.dump(state, open(os.path.join(ROOT, "jobs.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    write_page(state)
    write_summary(state)
    ok = sum(1 for h in health.values() if h["status"] == "OK")
    print(f"{now}: {sum(j['status'] != 'INELIGIBLE' for j in jobs)} jobs to review, {ok}/{len(health)} sources OK")
    for name, h in sorted(health.items()):
        if h["status"] != "OK":
            print(" ", h["status"], name, "-", h["detail"][:160])


# ---------------------------------------------------------------- output
def days_left(d):
    return (dt.date.fromisoformat(d) - TODAY).days if d else None


def write_page(st):
    e = html.escape
    rows = []
    for j in st["jobs"]:
        dl = days_left(j["deadline"])
        new = j["first_seen"] >= (TODAY - dt.timedelta(days=1)).isoformat()
        rows.append(
            f'<tr class="{j["status"]}{" stale" if j.get("stale") else ""}">'
            f'<td><b class="b {j["status"]}">{j["status"]}</b>{" <i>NEW</i>" if new else ""}'
            f'{" <i>old data</i>" if j.get("stale") else ""}</td><td>{j["score"]}</td>'
            f'<td><a href="{e(j["link"])}" target="_blank" rel="noopener">{e(j["title"])}</a>'
            f'<details><summary>why</summary>{e(j["reason"])} · {e(", ".join(j["why"]))}<br>{e(j["snippet"])}</details></td>'
            f'<td>{e(j["source"])}</td><td>{e(j["track"])}</td>'
            f'<td>{e(j["deadline"] or "?")}{f" ({dl} d)" if dl is not None else ""}</td><td>{e(j["first_seen"])}</td></tr>')

    def manual(h):
        return f'<a href="{e(h["manual"])}" target="_blank" rel="noopener">open</a>' if h.get("manual") else ""
    src = "".join(
        f'<tr><td>{e(n)}</td><td class="{h["status"]}">{h["status"]}</td><td>{h["items"]}</td><td>{h["matches"]}</td>'
        f'<td>{e(h["last_ok"])}</td><td>{e(h["detail"][:220])}</td><td>{manual(h)}</td></tr>'
        for n, h in sorted(st["sources"].items()))
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Radar - Damir</title><style>
body{{font:14px system-ui,sans-serif;margin:16px;color:#222}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}th{{background:#f3f3f3;position:sticky;top:0}}
.b{{padding:2px 6px;border-radius:4px;color:#fff;font-size:12px}}.b.OK{{background:#1a7f37}}.b.CHECK{{background:#bf8700}}
.b.INELIGIBLE{{background:#888}}tr.INELIGIBLE{{opacity:.55}}tr.stale{{background:#fff8e1}}i{{color:#0969da;font-size:12px}}
td.OK{{color:#1a7f37}}td.FAIL{{color:#cf222e;font-weight:bold}}td.SETUP{{color:#bf8700;font-weight:bold}}
input{{padding:6px;width:280px}}summary{{cursor:pointer;color:#666}}
</style><h1>Job Radar</h1><p>Updated <b>{e(st["updated"])}</b> · every 6 hours ·
OK = no blocker found · CHECK = verify deadline/eligibility · INELIGIBLE = explicit blocker (greyed, never deleted).
<b>Always confirm deadline and eligibility on the vacancy page.</b></p>
<p><input id="q" placeholder="filter: engineer, sarajevo, UNOPS..." oninput="f()">
<label><input type="checkbox" id="hide" onchange="f()" checked style="width:auto"> hide INELIGIBLE</label></p>
<table id="t"><tr><th>Status</th><th>Match</th><th>Vacancy</th><th>Source</th><th>Track</th><th>Deadline</th><th>Found</th></tr>
{"".join(rows) or '<tr><td colspan=7>No matching vacancies right now.</td></tr>'}</table>
<h2>Source health</h2><table><tr><th>Source</th><th>Status</th><th>Items</th><th>Matches</th><th>Last OK</th><th>Detail</th><th>Manual</th></tr>{src}</table>
<script>function f(){{const q=document.getElementById('q').value.toLowerCase(),h=document.getElementById('hide').checked;
document.querySelectorAll('#t tr:not(:first-child)').forEach(r=>{{r.style.display=(r.innerText.toLowerCase().includes(q)&&!(h&&r.classList.contains('INELIGIBLE')))?'':'none'}})}}f()</script></html>"""
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    open(os.path.join(ROOT, "site", "index.html"), "w", encoding="utf-8").write(page)


def write_summary(st):
    good = [j for j in st["jobs"] if j["status"] != "INELIGIBLE"]
    lines = [f"# Job Radar - {st['updated']}", "", f"**{len(good)} vacancies to review** (ineligible ones are on the web page, greyed).", "",
             "| Status | Match | Vacancy | Source | Deadline |", "|---|---:|---|---|---|"]
    lines += [f"| {j['status']} | {j['score']} | [{j['title'].replace('|', '/')}]({j['link']}) | {j['source']} | {j['deadline'] or '?'} |"
              for j in good[:60]]
    lines += ["", "| Source | Status | Items | Matches | Detail |", "|---|---|---:|---:|---|"]
    lines += [f"| {n} | {h['status']} | {h['items']} | {h['matches']} | {h['detail'][:120].replace('|', '/')} |"
              for n, h in sorted(st["sources"].items())]
    open(os.path.join(ROOT, "summary.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- tests
def test():
    fails, total = [], [0]

    def check(name, cond):
        total[0] += 1
        if not cond:
            fails.append(name)

    rss = '<?xml version="1.0"?><rss><channel><item><title>Civil Engineer & Site Supervisor</title><link>https://x/1</link>' \
          '<description>Deadline: Nov 30, 2026 &nbsp;</description></item></channel></rss>'
    j = parse_feed(rss)
    check("rss bare & and &nbsp; repaired", j and j[0]["title"] == "Civil Engineer & Site Supervisor")
    check("rss deadline", find_deadline(j[0]["text"]) == dt.date(2026, 11, 30))
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Procurement Specialist</title><link href="https://y/1"/></entry></feed>'
    check("atom", parse_feed(atom)[0]["link"] == "https://y/1")
    try:
        parse_feed("<!DOCTYPE html><html><body>login</body></html>"); check("html is not a feed", False)
    except ValueError:
        pass
    h = '<a href="/jobs/123-civil">Civil Engineer, Mostar</a><a href="/about">About us page here</a>'
    check("links", [x["link"] for x in parse_links(h, "https://i.org/s", "/jobs/")] == ["https://i.org/jobs/123-civil"])
    h2 = '<a href="/oglas/90210">Diplomirani inženjer građevine (m/ž) Sarajevo 09.11. – 09.12.2026</a><a href="https://ads.x.com/a">Diplomirani inženjer reklama</a>'
    k = parse_links(h2, "https://posao.klix.ba/oglasi", "", 15, True)
    check("same_host links", len(k) == 1 and k[0]["link"] == "https://posao.klix.ba/oglas/90210")
    check("klix last date = deadline", find_deadline(k[0]["text"], True) == dt.date(2026, 12, 9))
    for s, d in [("Apply Before 11/18/2026", dt.date(2026, 11, 18)), ("Post End date: 19-Nov-2026", dt.date(2026, 11, 19)),
                 ("Closing date: 30 November 2026", dt.date(2026, 11, 30)), ("deadline 2026-12-01", dt.date(2026, 12, 1)),
                 ("Prijava do 21. 11. 2026.", dt.date(2026, 11, 21))]:
        check("date " + s, find_deadline(s) == d)
    fut = TODAY + dt.timedelta(days=10)
    for t, x, want in [("Senior Project Manager", "Internal candidates only", "INELIGIBLE"),
                       ("Logistics Officer (Ukrainian Nationals only)", "", "INELIGIBLE"),
                       ("Engineer (Bosnian nationals only)", "Sarajevo", "OK"),
                       ("National Project Officer (NOB)", "Conakry, Guinea", "INELIGIBLE"),
                       ("National Consultant engineer", "Sarajevo, Bosnia and Herzegovina", "OK"),
                       ("Construction Specialist P-3", "Applications welcome from internal and external candidates", "OK")]:
        check(f"eligibility {t}", eligibility(t, x, fut)[0] == want)
    check("no deadline = CHECK", eligibility("Engineer", "", None)[0] == "CHECK")
    check("past deadline", eligibility("Engineer", "", TODAY - dt.timedelta(days=1))[0] == "INELIGIBLE")
    check("intern is not internal", "soft-negative" not in score("Civil Engineer - internal candidates", "")[2])
    check("bih is a whole word", "+bih" not in score("Civil Engineer", "smoking prohibited")[2])
    check("appname never shown", redact("x") == "x" and (not APPNAME or APPNAME not in redact("u?appname=" + APPNAME)))
    menu = '<a href="/kategorija/gradjevinarstvo">Građevinarstvo i geodezija</a><a href="/oglas/diplomirani-inzenjer-12345">Diplomirani inženjer građevine</a>'
    check("menu links dropped", [x["title"] for x in parse_links(menu, "https://k.ba/oglasi", "", 15, True)] == ["Diplomirani inženjer građevine"])
    page = ("Job Title Associate Water Sector Specialist - based in Luxembourg Job ID 111775 Location LU - Luxembourg "
            "Job Function Managerial & Professional Posted Date 23/10/2026 Close Date 16/11/2026 Job Title Cyber "
            "Specialist Job ID 111803 Location LU - Luxembourg Job Function M Close Date Open Until Filled")
    rows = EIB_ROW.findall(page)
    check("EIB rows + DD/MM dates", len(rows) == 2 and rows[0][1] == "111775" and rows[0][3] == "16/11/2026")
    check("WFP deadline text", find_deadline("DEADLINE FOR APPLICATIONS 15 November 2026-23:59") == dt.date(2026, 11, 15))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "radar.yml"), "w").write("jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/deploy-pages@v5\n")
        open(os.path.join(td, "old.yml"), "w").write("runs-on: ubuntu-latest\n      - uses: actions/upload-artifact@v4\n")
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            found = audit(td)
        check("audit finds 3 issues", len(found) == 3 and all("old.yml" in i for i in found))
    names = [s["name"] for s in CFG["sources"]]
    check("unique source names", len(names) == len(set(names)))
    check("no UNjobs (robots.txt)", not any("unjobs.org" in json.dumps(s) for s in CFG["sources"]))
    check("every source has a manual link", all(s.get("manual") for s in CFG["sources"]))
    check("no ReliefWeb website/RSS (anti-bot)", not any("reliefweb.int/jobs/rss" in json.dumps(s) for s in CFG["sources"]))
    cases = json.load(open(os.path.join(ROOT, "benchmark.json"), encoding="utf-8"))
    bad = [c["title"] for c in cases if (score(c["title"], c["text"])[0] >= CFG["min_score"]) != c["relevant"]]
    print(f"Self-tests {total[0] - len(fails)}/{total[0]} · Benchmark {len(cases) - len(bad)}/{len(cases)}")
    for x in fails + bad:
        print("  FAIL", x)
    sys.exit(1 if fails or bad else 0)


# ---------------------------------------------------------------- Claude Code harness
SETTINGS = {
    "permissions": {
        "allow": ["Read", "Bash(python radar.py:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
                  "Bash(git add:*)", "Bash(git commit:*)", "Bash(git pull:*)"],
        "ask": ["Bash(git push:*)", "Bash(pip install:*)", "Edit(radar.yml)", "Edit(.github/**)", "Edit(benchmark.json)"],
        "deny": ["Read(./.env)", "Read(./.env.*)", "Read(./secrets/**)", "Read(**/*.key)",
                 "Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(rm -rf:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash|Read|Edit|Write", "hooks": [{"type": "command", "command": "python radar.py --guard"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "python radar.py --gate"}]}]}}
REVIEWER = """---
name: reviewer
description: Fresh-eyes reviewer. Use after any CV, cover letter, application answer, or change to config.json/radar.py. Reports; never rewrites.
tools: Read, Grep, Glob
---
Follow the REVIEW section of AGENTS.md exactly. Output an issue table, then PASS or FIX FIRST.
"""
BLOCK = [r"rm\s+-rf", r"git\s+push\s+.*(--force|-f\b)", r"git\s+reset\s+--hard", r"Remove-Item\s+.*-Recurse",
         r"(^|[\\/\s])\.env(\.|\s|$)", r"secrets[\\/]", r"\.key$"]


MIN_MAJOR = {  # first major version that runs on Node 24 (GitHub-owned actions)
    "actions/checkout": 5, "actions/setup-python": 6, "actions/setup-node": 5, "actions/cache": 5,
    "actions/upload-artifact": 6, "actions/download-artifact": 7, "actions/upload-pages-artifact": 5,
    "actions/deploy-pages": 5}


def audit(folder=None):
    """Lists obsolete action versions and moving runner labels in .github/workflows/*.yml."""
    folder = folder or os.path.join(ROOT, ".github", "workflows")
    issues, files = [], sorted(f for f in os.listdir(folder) if f.endswith((".yml", ".yaml"))) if os.path.isdir(folder) else []
    for f in files:
        for n, line in enumerate(open(os.path.join(folder, f), encoding="utf-8"), 1):
            code = line.split("#")[0]
            m = re.search(r"uses:\s*['\"]?([\w.-]+/[\w.-]+)@v?(\d+)", code)
            if m and m.group(1) in MIN_MAJOR and int(m.group(2)) < MIN_MAJOR[m.group(1)]:
                issues.append(f"{f}:{n}: {m.group(1)}@v{m.group(2)} runs on Node 20 -> use v{MIN_MAJOR[m.group(1)]}+")
            if re.search(r"runs-on:.*ubuntu-latest", code):
                issues.append(f"{f}:{n}: ubuntu-latest moves to Ubuntu 26 -> pin ubuntu-24.04")
            if re.search(r"ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION", code):
                issues.append(f"{f}:{n}: Node 20 opt-out no longer works after Node 20 removal -> delete it")
        if f != "radar.yml":
            issues.append(f"{f}: extra workflow - delete it unless you still need it")
    print(f"Workflow audit: {len(files)} file(s): {', '.join(files) or 'none'}")
    print("\n".join("  " + i for i in issues) if issues else "  no obsolete actions, no moving runner labels")
    return issues


def install():
    os.makedirs(os.path.join(ROOT, ".github", "workflows"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "radar.yml"), os.path.join(ROOT, ".github", "workflows", "radar.yml"))
    os.makedirs(os.path.join(ROOT, ".claude", "agents"), exist_ok=True)
    json.dump(SETTINGS, open(os.path.join(ROOT, ".claude", "settings.json"), "w"), indent=2)
    open(os.path.join(ROOT, ".claude", "agents", "reviewer.md"), "w").write(REVIEWER)
    print("wrote .github/workflows/radar.yml, .claude/settings.json, .claude/agents/reviewer.md")


def guard():
    ti = json.load(sys.stdin).get("tool_input", {})
    target = ti.get("command") or ti.get("file_path") or ""
    for p in BLOCK:
        if re.search(p, target, re.I):
            print(f"Blocked: '{target}' matches {p}. Ask Damir first.", file=sys.stderr); sys.exit(2)


def gate():
    if json.load(sys.stdin).get("stop_hook_active"):
        return
    r = subprocess.run([sys.executable, os.path.join(ROOT, "radar.py"), "--test"], capture_output=True, text=True)
    if r.returncode:
        print("Tests failed - fix first:\n" + (r.stdout + r.stderr)[-1500:], file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    a = sys.argv[1:] + ["", ""]
    {"--test": test, "--install": install, "--audit": lambda: sys.exit(1 if audit() else 0), "--guard": guard, "--gate": gate,
     "--why": lambda: print(score(a[1], a[2])), "--source": lambda: radar(a[1])}.get(a[0], radar)()
