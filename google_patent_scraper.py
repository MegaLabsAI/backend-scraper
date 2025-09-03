from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi.middleware.cors import CORSMiddleware
import re
import pandas as pd
from fastapi import Request
import uuid
import logging
import time
import re
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import os
import httpx
from parsel import Selector
from urllib.parse import quote_plus, urljoin


app = FastAPI()
session_patent_data = {}  # ⬅️ keep at module level (global memory)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

def extract_patent_id(article, href, abstract):
    # 1. Try to extract from href (e.g. /patent/US20130269543A1/en)
    m = re.search(r'/patent/([A-Z]{2}\d+[A-Z0-9]*)', href)
    if m:
        return m.group(1)
    # 2. Try hidden spans (sometimes used for the patent ID)
    try:
        hidden_spans = article.find_elements(By.CSS_SELECTOR, "span[style*='display: none']")
        for sp in hidden_spans:
            text = sp.text.strip()
            m = re.match(r'^[A-Z]{2}\d+[A-Z0-9]*$', text)
            if m:
                return text
    except Exception:
        pass
    # 3. Try to find patent ID in abstract (rare, but fallback)
    m = re.search(r'([A-Z]{2}\d+[A-Z0-9]*)', abstract)
    if m:
        return m.group(1)
    return ""


# patent scraped
def run_google_patents_scraper(query, max_results=2):
    results = []
    log_rows = []
    def log(msg):
        print(msg)
        log_rows.append({"event": msg})

    log("[INFO] Starting Google Patents scraper...")

    # --- 1) Selenium'ı dene; olmazsa HTTP fallback ---
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1366,2400")
        options.add_argument("--lang=en-US,en;q=0.9")
        options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        driver = webdriver.Chrome(options=options)  # burada patlarsa except'e düşer
        wait = WebDriverWait(driver, 10)
    except Exception as boot_err:
        log(f"[INFO] Selenium not available on this host -> HTTP fallback. Reason: {boot_err}")
        results = _scrape_http_fallback(query, max_results, log)
        # fallback temiz kapanış ve log kaydı
        log(f"\n[INFO] Scraping finished (fallback), total results: {len(results)}")
        try:
            pd.DataFrame(log_rows).to_excel("patent_scraper_log.xlsx", index=False)
            log("[INFO] Log file saved to patent_scraper_log.xlsx")
        except Exception as e:
            log(f"[INFO] Skipping Excel log: {e}")
        return results

    # --- 2) Selenium ile devam (senin mevcut akışın) ---
    try:
        search_url = f"https://patents.google.com/?q={quote_plus(query)}"
        log(f"[INFO] Navigating to search URL: {search_url}")
        driver.get(search_url)

        wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, 'article.result.style-scope.search-result-item')))
        articles = driver.find_elements(By.CSS_SELECTOR, 'article.result.style-scope.search-result-item')
        log(f"[INFO] Found {len(articles)} articles on search results page.")

        # -------- PASS 1: SEARCH PAGE SCRAPING --------
        search_results = []
        for article in articles[:max_results]:
            try:
                link_elem = article.find_element(By.CSS_SELECTOR, 'a#link')
                href = link_elem.get_attribute("href") or ""
                if href.startswith('/patent/'):
                    detail_link = "https://patents.google.com" + href
                elif href.startswith('http'):
                    detail_link = href
                else:
                    detail_link = ""
                title = link_elem.text.strip()
                log(f"[DEBUG] Title: {title}")
                log(f"[DEBUG] href: {href}")
            except Exception as e:
                title, detail_link = "", ""
                log(f"[WARN] Could not extract title/link: {e}")

            try:
                abstract_elem = article.find_element(By.CSS_SELECTOR, 'div.abstract')
                abstract = abstract_elem.text.strip()
                log(f"[DEBUG] Abstract: {abstract[:60]}...")
            except Exception as e:
                abstract = ""
                log(f"[WARN] Could not extract abstract: {e}")

            patent_id = extract_patent_id(article, href, abstract)
            if patent_id:
                detail_link = f"https://patents.google.com/patent/{patent_id}/en"
                log(f"[DEBUG] Patent ID: {patent_id}")
                log(f"[DEBUG] Patent Link: {detail_link}")
            else:
                detail_link = href
                log(f"[WARN] Could not extract patent ID from link or abstract. Fallback link: {detail_link}")

            search_results.append({
                "title": title,
                "abstract": abstract,
                "patent_id": patent_id,
                "link": detail_link,
            })

        # -------- PASS 2: DETAILS PAGE SCRAPING --------
        for res in search_results:
            link = res['link']
            claims = description = inventor = assignee = classification = citations = date_published = ""

            try:
                log(f"[INFO] Navigating to detail page: {link}")
                driver.get(link)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

                # === Full abstract ===
                try:
                    full_abs_elem = driver.find_element(By.CSS_SELECTOR, "section#abstract")
                    full_abstract = full_abs_elem.text.strip()
                    if full_abstract:
                        res["abstract"] = full_abstract
                        log(f"[DEBUG] Full abstract (detail page): {len(full_abstract)} chars")
                except Exception as e:
                    log(f"[WARN] Could not extract full abstract from detail page: {e}")

                # === Claims ===
                try:
                    claims_section = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "section#claims")))
                    try:
                        patent_text_elem = claims_section.find_element(By.TAG_NAME, "patent-text")
                        claims = patent_text_elem.text.strip()
                    except Exception:
                        claims = claims_section.text.strip()
                    log(f"[DEBUG] Claims len: {len(claims)}")
                except Exception as e:
                    log(f"[WARN] Could not extract claims: {e}")

                # === Description ===
                try:
                    try:
                        desc_elem = wait.until(EC.presence_of_element_located((By.ID, "descriptionText")))
                        description = desc_elem.text.strip()
                    except Exception:
                        desc_section = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "section#description")))
                        try:
                            patent_text_elem = desc_section.find_element(By.TAG_NAME, "patent-text")
                            description = patent_text_elem.text.strip()
                        except Exception:
                            description = desc_section.text.strip()
                    log(f"[DEBUG] Description len: {len(description)}")
                except Exception as e:
                    log(f"[WARN] Could not extract description: {e}")

                # === Inventor / Assignee ===
                try:
                    imp_dl = driver.find_element(By.CSS_SELECTOR, "dl.important-people.style-scope.patent-result")
                    children = imp_dl.find_elements(By.XPATH, "./*")
                    inventor_list, assignee, current_label = [], "", ""
                    for el in children:
                        tag = el.tag_name.lower()
                        text = el.text.strip()
                        if tag == "dt":
                            current_label = text.lower()
                        elif tag == "dd":
                            if "inventor" in current_label:
                                inventor_list.append(text)
                            elif "assignee" in current_label:
                                assignee = text
                    inventor = ", ".join(inventor_list)
                    log(f"[DEBUG] Inventor: {inventor} | Assignee: {assignee}")
                except Exception as e:
                    log(f"[WARN] Could not extract inventor/assignee: {e}")

                # === Classification ===
                try:
                    try:
                        more_button = driver.find_element(By.XPATH, "//div[contains(text(),'more classifications')]")
                        driver.execute_script("arguments[0].click();", more_button)
                        time.sleep(1)
                    except Exception:
                        pass

                    js_script = """
                    const viewer = document.querySelector('classification-viewer');
                    if (!viewer || !viewer.shadowRoot) return '';
                    const trees = viewer.shadowRoot.querySelectorAll('classification-tree');
                    return Array.from(trees).map(e => e.textContent.trim()).filter(Boolean).join('\\n');
                    """
                    classification = driver.execute_script(js_script)
                    if not classification.strip():
                        raise Exception("shadowRoot extraction failed")

                except Exception as e:
                    try:
                        outer_html = driver.execute_script("""
                            const el = document.querySelector('classification-viewer');
                            return el ? el.outerHTML : '';
                        """)
                        matches = re.findall(r'<classification-tree[^>]*>(.*?)</classification-tree>', outer_html, re.DOTALL)
                        classification_list = [re.sub('<[^<]+?>', '', m).strip() for m in matches if m.strip()]
                        rows, buffer_code = [], None
                        for line in classification_list:
                            line = re.sub(r'\s+', ' ', line).replace('\xa0',' ').strip()
                            if re.match(r'^[A-Z]{1,4}[0-9]{0,4}[A-Z]?[0-9]{0,4}/?[0-9]*$', line):
                                buffer_code = line
                            elif buffer_code and line and not re.match(r'^[A-Z]{1,10}$', line):
                                rows.append((buffer_code, line)); buffer_code = None
                            else:
                                buffer_code = None
                        classification = "\n".join([f"{c} — {d}" for c,d in rows]) if rows else ""
                    except Exception as e2:
                        log(f"[WARN] Classification fallback failed: {e2}")
                        classification = ""

                # === Citations ===
                try:
                    citations_div = driver.find_element(By.CSS_SELECTOR, 'div.responsive-table.style-scope.patent-result')
                    citation_rows = citations_div.find_elements(By.CSS_SELECTOR, "div.tr.style-scope.patent-result")
                    citations_list = []
                    for row in citation_rows:
                        cols = row.find_elements(By.CSS_SELECTOR, "span.td.style-scope.patent-result")
                        text = " | ".join([col.text.strip() for col in cols if col.text.strip()])
                        if text:
                            citations_list.append(text)
                    citations = "\n".join(citations_list)
                except Exception as e:
                    citations = ""
                    log(f"[WARN] Could not extract citations: {e}")

                # === Timeline ===
                try:
                    event_divs = driver.find_elements(By.CSS_SELECTOR, "div.event.layout.horizontal.style-scope.application-timeline")
                    timeline_items = []
                    for ev in event_divs:
                        date_text = ""
                        title_text = ""
                        try:
                            date_text = ev.find_element(By.CSS_SELECTOR, "div[date]").text.strip()
                        except:
                            pass
                        try:
                            title_text = ev.find_element(By.CSS_SELECTOR, "div.flex.title").text.strip()
                        except:
                            pass
                        if date_text or title_text:
                            timeline_items.append(f"{date_text} — {title_text}")
                    date_published = "; ".join(timeline_items)
                except Exception as e:
                    date_published = ""
                    log(f"[WARN] Could not extract timeline events: {e}")

            except Exception as e:
                log(f"[WARN] Failed to extract details from {link}: {e}")

            out = res.copy()
            out.update({
                "claims": claims,
                "description": description,
                "inventor": inventor,
                "assignee": assignee,
                "classification": classification,
                "citations": citations,
                "date_published": date_published,
            })
            results.append(out)

    finally:
        log(f"\n[INFO] Scraping finished, total results: {len(results)}")
        try:
            pd.DataFrame(log_rows).to_excel("patent_scraper_log.xlsx", index=False)
            log("[INFO] Log file saved to patent_scraper_log.xlsx")
        except Exception as e:
            log(f"[INFO] Skipping Excel log: {e}")
        try:
            driver.quit()
        except Exception:
            pass

    return results


UA = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

def _txts(nodes):
    return " ".join([re.sub(r"\s+", " ", x).strip() for x in nodes if x and x.strip()])

def _scrape_http_fallback(query, max_results, log):
    results = []
    try:
        search_url = f"https://patents.google.com/?q={quote_plus(query)}"
        log(f"[FALLBACK] HTTP GET {search_url}")
        r = httpx.get(search_url, headers=UA, timeout=30)
        if r.status_code != 200:
            log(f"[FALLBACK][ERROR] search status {r.status_code}")
            return []
        sel = Selector(r.text)
        articles = sel.css('article.result.style-scope.search-result-item')
        log(f"[FALLBACK] Found {len(articles)} results")
        for a in articles[:max_results]:
            href = a.css("a#link::attr(href)").get("") or ""
            title = (a.css("a#link::text").get("") or "").strip()
            abstract = (a.css("div.abstract::text").get("") or "").strip()
            link = urljoin("https://patents.google.com", href) if href.startswith("/") else href
            m = re.search(r'/patent/([A-Z]{2}\d+[A-Z0-9]*)', link or "")
            patent_id = m.group(1) if m else ""
            if not link:
                continue
            log(f"[FALLBACK] HTTP GET {link}")
            d = httpx.get(link, headers=UA, timeout=30)
            if d.status_code != 200:
                log(f"[FALLBACK][WARN] detail status {d.status_code} for {link}")
                continue
            ds = Selector(d.text)
            full_abs = _txts(ds.css("section#abstract ::text").getall())
            if full_abs: abstract = full_abs
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
            rows = ds.css("div.responsive-table div.tr")
            cits = []
            for row in rows:
                cols = [t.strip() for t in row.css("span.td ::text").getall() if t.strip()]
                if cols: cits.append(" | ".join(cols))
            citations = "\n".join(cits)
            evs = ds.css("div.application-timeline div.event")
            timeline = []
            for ev in evs:
                date = _txts(ev.css("div[date] ::text").getall())
                tt = _txts(ev.css("div.flex.title ::text").getall())
                if date or tt: timeline.append(f"{date} — {tt}")
            date_published = "; ".join(timeline)
            results.append({
                "title": title,
                "abstract": abstract,
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
    except Exception as e:
        log(f"[FALLBACK][ERROR] {e}")
    return results



@app.post("/get_patents_detailed", response_model=PatentDetailedResponse)
async def get_patents_detailed(request: PatentRequest):
    loop = asyncio.get_event_loop()
    patents = await loop.run_in_executor(
        ThreadPoolExecutor(max_workers=1),
        run_google_patents_scraper,
        request.description,  # query
        2                     # max_results
    )

    # ✅ Store scraped results in memory by session_id
    try:
        df = pd.DataFrame(patents)
        session_patent_data[request.session_id] = df
        logger.info(f"Stored {len(df)} patents for session: {request.session_id}")
    except Exception as e:
        logger.warning(f"Failed to store session data: {e}")
    return {"patents": patents}




