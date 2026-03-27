"""
Job Lead Pipeline — No API Required
Sources: LinkedIn RSS, Indeed RSS, Remotive (free), Adzuna (free 10k/mo)
Output:  Scored + prioritized leads → CSV + optional Google Sheets push

Usage:
    pip install requests beautifulsoup4 feedparser pandas
    python job_lead_pipeline.py
"""

import feedparser
import requests
import pandas as pd
import hashlib
import json
import time
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────
# CONFIG — edit these to target your niche
# ─────────────────────────────────────────────

KEYWORDS = ["CTO", "VP Engineering", "Head of Engineering",
            "software architect", "technical lead", "engineering manager"]

TARGET_INDUSTRIES = ["fintech", "healthtech", "saas", "logistics",
                     "edtech", "proptech", "legal tech"]

SCORING_RULES = {
    # Title signals (buying intent — they're building a team = need dev services)
    "title_senior":    {"keywords": ["CTO", "VP", "Head", "Director", "Chief"], "score": 30},
    "title_tech":      {"keywords": ["Engineering", "Technical", "Software", "Platform"], "score": 15},
    # Company size signals (sweet spot: 20–500 employees)
    "size_mid":        {"keywords": ["51-200", "201-500", "11-50"], "score": 20},
    # Urgency signals in job description
    "urgency":         {"keywords": ["immediately", "urgent", "asap", "fast-growing",
                                     "rapidly", "scaling", "series a", "series b"], "score": 25},
    # Tech stack signals (your speciality — edit to match yours)
    "stack_match":     {"keywords": ["react", "node", "python", "django", "fastapi",
                                     "kubernetes", "aws", "typescript"], "score": 20},
    # Red flags (lower score)
    "large_corp":      {"keywords": ["fortune 500", "global enterprise", "10,000+"], "score": -20},
    "low_budget":      {"keywords": ["volunteer", "unpaid", "equity only"], "score": -50},
}

OUTPUT_CSV = "job_leads_scored.csv"


# ─────────────────────────────────────────────
# STAGE 1: EXTRACT — pull from free sources
# ─────────────────────────────────────────────

def uid(url):
    """Stable dedup ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def fetch_linkedin_rss(keyword, location="worldwide"):
    """LinkedIn public job RSS — no login required."""
    kw = keyword.replace(" ", "%20")
    url = (f"https://www.linkedin.com/jobs/search/?keywords={kw}"
           f"&location={location}&f_TPR=r86400&trk=public_jobs_jobs-search-bar_search-submit")
    # LinkedIn doesn't provide RSS directly; use the jobs JSON endpoint
    api_url = (f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
               f"?keywords={kw}&location={location}&start=0")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
    jobs = []
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.find_all("div", class_="base-card"):
                title_el = card.find("h3", class_="base-search-card__title")
                company_el = card.find("h4", class_="base-search-card__subtitle")
                location_el = card.find("span", class_="job-search-card__location")
                link_el = card.find("a", class_="base-card__full-link")
                date_el = card.find("time")
                if title_el and company_el:
                    jobs.append({
                        "id": uid(link_el["href"]) if link_el else uid(str(title_el)),
                        "source": "linkedin",
                        "title": title_el.get_text(strip=True),
                        "company": company_el.get_text(strip=True),
                        "location": location_el.get_text(strip=True) if location_el else "",
                        "url": link_el["href"].split("?")[0] if link_el else "",
                        "posted_at": date_el.get("datetime", "") if date_el else "",
                        "description": "",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    })
    except Exception as e:
        print(f"  [LinkedIn] {keyword}: {e}")
    return jobs


def fetch_indeed_rss(keyword, location=""):
    """Indeed RSS feed — completely free, no API key."""
    kw = keyword.replace(" ", "+")
    url = f"https://www.indeed.com/rss?q={kw}&l={location}&sort=date&fromage=1"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
    jobs = []
    try:
        feed = feedparser.parse(url, request_headers=headers)
        for entry in feed.entries:
            jobs.append({
                "id": uid(entry.get("link", entry.get("title", ""))),
                "source": "indeed",
                "title": entry.get("title", ""),
                "company": entry.get("author", ""),
                "location": "",
                "url": entry.get("link", ""),
                "posted_at": entry.get("published", ""),
                "description": BeautifulSoup(
                    entry.get("summary", ""), "html.parser"
                ).get_text()[:500],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        print(f"  [Indeed] {keyword}: {e}")
    return jobs


def fetch_remotive(keyword):
    """Remotive free API — remote tech jobs, no key needed."""
    url = "https://remotive.com/api/remote-jobs"
    jobs = []
    try:
        resp = requests.get(url, params={"search": keyword, "limit": 50}, timeout=10)
        if resp.status_code == 200:
            for j in resp.json().get("jobs", []):
                jobs.append({
                    "id": uid(str(j.get("id", j.get("url", "")))),
                    "source": "remotive",
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "Remote"),
                    "url": j.get("url", ""),
                    "posted_at": j.get("publication_date", ""),
                    "description": BeautifulSoup(
                        j.get("description", ""), "html.parser"
                    ).get_text()[:500],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
    except Exception as e:
        print(f"  [Remotive] {keyword}: {e}")
    return jobs


def fetch_adzuna(keyword, country="us"):
    """
    Adzuna free API — 10,000 calls/month free.
    Register at: https://developer.adzuna.com (free, instant)
    Replace app_id and app_key below with yours.
    Leave blank to skip this source.
    """
    APP_ID = ""   # <- paste your free Adzuna app_id here
    APP_KEY = ""  # <- paste your free Adzuna app_key here
    if not APP_ID:
        return []
    kw = keyword.replace(" ", "%20")
    url = (f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
           f"?app_id={APP_ID}&app_key={APP_KEY}&results_per_page=20"
           f"&what={kw}&max_days_old=1")
    jobs = []
    try:
        resp = requests.get(url, timeout=10)
        for j in resp.json().get("results", []):
            jobs.append({
                "id": uid(j.get("redirect_url", j.get("title", ""))),
                "source": "adzuna",
                "title": j.get("title", ""),
                "company": j.get("company", {}).get("display_name", ""),
                "location": j.get("location", {}).get("display_name", ""),
                "url": j.get("redirect_url", ""),
                "posted_at": j.get("created", ""),
                "description": j.get("description", "")[:500],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        print(f"  [Adzuna] {keyword}: {e}")
    return jobs


# ─────────────────────────────────────────────
# STAGE 2: NORMALIZE — deduplicate + clean
# ─────────────────────────────────────────────

def normalize(raw_jobs):
    seen = set()
    clean = []
    for job in raw_jobs:
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        job["title"] = job["title"].strip()
        job["company"] = job["company"].strip()
        job["description"] = re.sub(r"\s+", " ", job.get("description", "")).strip()
        clean.append(job)
    print(f"  Normalized: {len(raw_jobs)} raw → {len(clean)} unique jobs")
    return clean


# ─────────────────────────────────────────────
# STAGE 3: SCORE — buying signal strength
# ─────────────────────────────────────────────

def score_job(job):
    text = (job["title"] + " " + job["company"] + " " + job["description"]).lower()
    score = 0
    matched = []

    for rule_name, rule in SCORING_RULES.items():
        for kw in rule["keywords"]:
            if kw.lower() in text:
                score += rule["score"]
                matched.append(rule_name)
                break  # one match per rule is enough

    # Bonus: industry match
    for ind in TARGET_INDUSTRIES:
        if ind.lower() in text:
            score += 15
            matched.append(f"industry:{ind}")
            break

    # Clamp 0–100
    score = max(0, min(100, score))
    return score, list(set(matched))


def score_all(jobs):
    for job in jobs:
        job["score"], job["signals"] = score_job(job)
        job["signals"] = ", ".join(job["signals"])
    return jobs


# ─────────────────────────────────────────────
# STAGE 4: PRIORITIZE — rank and output
# ─────────────────────────────────────────────

def prioritize(jobs, top_n=20):
    df = pd.DataFrame(jobs)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["tier"] = df["score"].apply(
        lambda s: "HOT" if s >= 60 else ("WARM" if s >= 35 else "COLD")
    )
    cols = ["rank", "tier", "score", "title", "company", "location",
            "source", "url", "posted_at", "signals"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\n  Saved {len(df)} leads to {OUTPUT_CSV}")
    top = df[df["tier"].isin(["HOT", "WARM"])].head(top_n)
    return df, top


# ─────────────────────────────────────────────
# OPTIONAL: Push top leads to Google Sheets
# ─────────────────────────────────────────────

def push_to_google_sheets(df, webhook_url):
    """
    Paste your Google Apps Script webhook URL here.
    See: https://developers.google.com/apps-script/guides/web
    Free — no API key, no billing.
    """
    if not webhook_url:
        return
    payload = df.head(20).to_dict(orient="records")
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        print(f"  Sheets push: {r.status_code}")
    except Exception as e:
        print(f"  Sheets push failed: {e}")


# ─────────────────────────────────────────────
# OPTIONAL: Slack alert for HOT leads
# ─────────────────────────────────────────────

def slack_alert(hot_leads, slack_webhook_url):
    """
    Free Slack incoming webhook.
    Create at: https://api.slack.com/messaging/webhooks
    """
    if not slack_webhook_url or hot_leads.empty:
        return
    lines = [f"*{row.title}* @ {row.company} — score {row.score} [{row.source}]\n{row.url}"
             for _, row in hot_leads.head(5).iterrows()]
    payload = {"text": f":fire: *Top job leads today*\n\n" + "\n\n".join(lines)}
    try:
        requests.post(slack_webhook_url, json=payload, timeout=10)
        print("  Slack alert sent.")
    except Exception as e:
        print(f"  Slack alert failed: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run():
    SHEETS_WEBHOOK = ""   # <- paste Google Apps Script URL (optional)
    SLACK_WEBHOOK  = ""   # <- paste Slack incoming webhook URL (optional)

    print("=== Job Lead Pipeline (No API) ===\n")
    all_jobs = []

    for kw in KEYWORDS:
        print(f"Fetching: {kw}")
        all_jobs += fetch_linkedin_rss(kw)
        all_jobs += fetch_indeed_rss(kw)
        all_jobs += fetch_remotive(kw)
        all_jobs += fetch_adzuna(kw)
        time.sleep(2)  # polite delay between sources

    print(f"\nStage 2: Normalizing {len(all_jobs)} raw jobs...")
    jobs = normalize(all_jobs)

    print("Stage 3: Scoring...")
    jobs = score_all(jobs)

    print("Stage 4: Prioritizing...")
    df, hot = prioritize(jobs)

    print(f"\n--- Results ---")
    print(f"HOT  (60+): {len(df[df.tier=='HOT'])}")
    print(f"WARM (35+): {len(df[df.tier=='WARM'])}")
    print(f"COLD (<35): {len(df[df.tier=='COLD'])}")
    print(f"\nTop 5 leads:")
    for _, r in hot.head(5).iterrows():
        print(f"  [{r.score:>3}] {r.title} @ {r.company} ({r.source})")

    push_to_google_sheets(hot, SHEETS_WEBHOOK)
    slack_alert(hot, SLACK_WEBHOOK)

    print(f"\nDone. Full results in: {OUTPUT_CSV}")
    return df


if __name__ == "__main__":
    run()
