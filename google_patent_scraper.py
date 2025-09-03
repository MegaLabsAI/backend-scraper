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

def _scrape_google_search(query, max_results, log):
    """Fallback: Google Search üzerinden patent linklerini al."""
    results = []
    search_url = f"https://www.google.com/search?q=site:patents.google.com+{quote_plus(query)}"
    log(f"[FALLBACK] Google Search: {search_url}")
    r = httpx.get(search_url, headers=UA, timeout=60)
    sel = Selector(r.text)
    links = [l for l in sel.css("a::attr(href)").getall() if "/patent/" in l]
    seen = set()
    for link in links:
        if link in seen: 
            continue
        seen.add(link)
        if not link.startswith("http"):
            continue
        log(f"[FALLBACK] Detail: {link}")
        d = httpx.get(link, headers=UA, timeout=60)
        ds = Selector(d.text)
        results.append({
            "title": _txts(ds.css("meta[name='DC.title']::attr(content)").getall()),
            "abstract": _txts(ds.css("section#abstract ::text").getall()),
            "patent_id": link.split("/patent/")[-1].split("/")[0],
            "link": link,
            "claims": _txts(ds.css("section#claims ::text").getall()),
            "description": _txts(ds.css("#descriptionText ::text, section#description ::text").getall()),
            "inventor": _txts(ds.xpath("//dt[contains(.,'Inventor')]/following-sibling::dd[1]//text()").getall()),
            "assignee": _txts(ds.xpath("//dt[contains(.,'Assignee')]/following-sibling::dd[1]//text()").getall()),
            "classification": _txts(ds.css("classification-viewer ::text").getall()),
            "citations": "\n".join([
                " | ".join([t.strip() for t in row.css("span.td ::text").getall() if t.strip()])
                for row in ds.css("div.responsive-table div.tr")
            ]),
            "date_published": "; ".join([
                f"{_txts(ev.css('div[date] ::text').getall())} — {_txts(ev.css('div.flex.title ::text').getall())}"
                for ev in ds.css("div.application-timeline div.event")
            ])
        })
        if len(results) >= max_results:
            break
        time.sleep(1)
    return results

def run_google_patents_scraper(query, max_results=5):
    log_rows = []
    def log(msg):
        print(msg)
        log_rows.append({"event": msg})

    results = []
    try:
        search_url = f"https://patents.google.com/?q=({quote_plus(query)})&oq={quote_plus(query)}+"
        log(f"[HTTP] Search: {search_url}")
        r = httpx.get(search_url, headers=UA, timeout=60)
        sel = Selector(r.text)
        articles = sel.css("article.result.style-scope.search-result-item")
        log(f"[HTTP] Found {len(articles)} articles")

        if len(articles) == 0:
            log("[WARN] No articles found on patents.google.com, using fallback.")
            return _scrape_google_search(query, max_results, log)

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
            ds = Selector(d.text)

            full_abs = _txts(ds.css("section#abstract ::text").getall()) or abstract
            claims = _txts(ds.css("section#claims ::text").getall())
            description = _txts(ds.css("#descriptionText ::text, section#description ::text").getall())
            inventor = _txts(ds.xpath("//dt[contains(.,'Inventor')]/following-sibling::dd[1]//text()").getall())
            assignee = _txts(ds.xpath("//dt[contains(.,'Assignee')]/following-sibling::dd[1]//text()").getall())
            classification = _txts(ds.css("classification-viewer ::text").getall())
            cits = []
            for row in ds.css("div.responsive-table div.tr"):
                cols = [t.strip() for t in row.css("span.td ::text").getall() if t.strip()]
                if cols: cits.append(" | ".join(cols))
            citations = "\n".join(cits)
            timeline = []
            for ev in ds.css("div.application-timeline div.event"):
                date = _txts(ev.css("div[date] ::text").getall())
                tt = _txts(ev.css("div.flex.title ::text").getall())
                if date or tt: timeline.append(f"{date} — {tt}")
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
            time.sleep(0.6)

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
