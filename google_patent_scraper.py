from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import httpx
from parsel import Selector
import re
import pandas as pd
import asyncio
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urljoin
import logging, time

app = FastAPI()
logger = logging.getLogger("google_patent_scraper")
logging.basicConfig(level=logging.INFO)

# CORS serbest
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === MODELLER ===
class PatentRequest(BaseModel):
    description: str
    session_id: Optional[str] = "default"

class PatentInfo(BaseModel):
    title: str
    abstract: str
    patent_id: str
    link: str
    claims: str
    description: str
    inventor: str
    assignee: str
    classification: str
    citations: str
    date_published: str

class PatentDetailedResponse(BaseModel):
    patents: List[PatentInfo]

# === Health check ===
@app.get("/healthz")
def healthz():
    return {"ok": True}

# === Scraper ===
UA = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

def _txts(nodes):
    return " ".join([re.sub(r"\s+", " ", x).strip() for x in nodes if x and x.strip()])

def run_google_patents_scraper(query, max_results=5):
    log_rows = []
    def log(msg):
        print(msg)
        log_rows.append({"event": msg})

    results = []
    try:
        search_url = f"https://patents.google.com/?q={quote_plus(query)}&tbm=pts"
        log(f"[HTTP] Search: {search_url}")
        r = httpx.get(search_url, headers=UA, timeout=60)
        if r.status_code != 200:
            log(f"[ERROR] Search failed {r.status_code}")
            return []

        sel = Selector(r.text)
        articles = sel.css("article.result.style-scope.search-result-item")
        log(f"[HTTP] Found {len(articles)} articles")
        for a in articles[:max_results]:
            href = a.css("a#link::attr(href)").get("") or ""
            title = (a.css("a#link::text").get("") or "").strip()
            abstract = (a.css("div.abstract::text").get("") or "").strip()
            link = urljoin("https://patents.google.com", href) if href.startswith("/") else href
            m = re.search(r"/patent/([A-Z]{2}\d+[A-Z0-9]*)", link or "")
            patent_id = m.group(1) if m else ""

            if not link:
                continue

            log(f"[HTTP] Detail: {link}")
            d = httpx.get(link, headers=UA, timeout=60)
            if d.status_code != 200:
                log(f"[WARN] Detail failed {d.status_code} for {link}")
                continue
            ds = Selector(d.text)

            full_abs = _txts(ds.css("section#abstract ::text").getall()) or abstract
            claims = _txts(ds.css("section#claims ::text").getall())
            description = _txts(ds.css("#descriptionText ::text, section#description ::text").getall())
            inventor = _txts(ds.xpath(
                "//dl[contains(@class,'important-people')]"
                "//dt[contains(translate(., 'INVETOR', 'invETOR'), 'inventor')]/following-sibling::dd[1]//text()"
            ).getall())
            assignee = _txts(ds.xpath(
                "//dl[contains(@class,'important-people')]"
                "//dt[contains(translate(., 'ASSIGNE', 'assigne'), 'assignee')]/following-sibling::dd[1]//text()"
            ).getall())
            classification = _txts(ds.css("classification-viewer ::text").getall())

            # citations
            cits = []
            rows = ds.css("div.responsive-table div.tr")
            for row in rows:
                cols = [t.strip() for t in row.css("span.td ::text").getall() if t.strip()]
                if cols:
                    cits.append(" | ".join(cols))
            citations = "\n".join(cits)

            # timeline
            timeline = []
            evs = ds.css("div.application-timeline div.event")
            for ev in evs:
                date = _txts(ev.css("div[date] ::text").getall())
                tt = _txts(ev.css("div.flex.title ::text").getall())
                if date or tt:
                    timeline.append(f"{date} — {tt}")
            date_published = "; ".join(timeline)

            results.append({
                "title": title,
                "abstract": full_abs,
                "patent_id": patent_id,
                "link": link,
                "claims": claims,
                "description": description,
                "inventor": inventor,
                "assignee": assignee,
                "classification": classification,
                "citations": citations,
                "date_published": date_published,
            })
            time.sleep(0.6)  # polite delay

    finally:
        pd.DataFrame(log_rows).to_excel("patent_scraper_log.xlsx", index=False)
        log("[INFO] Log file saved to patent_scraper_log.xlsx")

    return results

# === Endpoint ===
@app.post("/get_patents_detailed", response_model=PatentDetailedResponse)
async def get_patents_detailed(request: PatentRequest):
    loop = asyncio.get_event_loop()
    patents = await loop.run_in_executor(
        ThreadPoolExecutor(max_workers=1),
        run_google_patents_scraper,
        request.description,
        5,
    )
    logger.info(f"Stored {len(patents)} patents for session: {request.session_id}")
    return {"patents": patents}
