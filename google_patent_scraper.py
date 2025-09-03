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
    
    log_rows = []  # <---- store logs here!

    def log(msg):
        print(msg)
        log_rows.append({"event": msg})

    log("[INFO] Starting Google Patents scraper...")
    from selenium.webdriver.chrome.service import Service
    options = webdriver.ChromeOptions()

    options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")  # ✅ Chromium yolu
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")

    service = Service(os.getenv("CHROMEDRIVER", "/usr/bin/chromedriver"))  # ✅ chromedriver yolu
    driver = webdriver.Chrome(service=service, options=options)
    wait = WebDriverWait(driver, 10)

    try:
        # 1. Open search page
        from urllib.parse import quote_plus
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
            # Title & Link
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

            # Abstract
            try:
                abstract_elem = article.find_element(By.CSS_SELECTOR, 'div.abstract')
                abstract = abstract_elem.text.strip()
                log(f"[DEBUG] Abstract: {abstract[:60]}...")
            except Exception as e:
                abstract = ""
                log(f"[WARN] Could not extract abstract: {e}")

            # Universal Patent ID extraction
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
        results = []
        for res in search_results:
            link = res['link']
            claims = description = inventor = assignee = classification = citations = date_published = ""

            try:
                log(f"[INFO] Navigating to detail page: {link}")
                driver.get(link)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

                # === Full abstract from detail page ===
                try:
                    full_abs_elem = driver.find_element(By.CSS_SELECTOR, "section#abstract")
                    full_abstract = full_abs_elem.text.strip()
                    if full_abstract:
                        res["abstract"] = full_abstract
                        log(f"[DEBUG] Full abstract (detail page): {len(full_abstract)} chars")
                except Exception as e:
                    log(f"[WARN] Could not extract full abstract from detail page: {e}")


                # === Claims ===
                claims = ""
                try:
                    try:
                        claims_section = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "section#claims")))
                        try:
                            patent_text_elem = claims_section.find_element(By.TAG_NAME, "patent-text")
                            claims = patent_text_elem.text.strip()
                            log(f"[DEBUG] Claims found (by section/patent-text): {len(claims)} chars")
                        except Exception as e_tag:
                            claims = claims_section.text.strip()
                            log(f"[DEBUG] Claims found (by section): {len(claims)} chars")
                    except Exception as e_sec:
                        claims = ""
                        log(f"[WARN] Could not extract claims by section selector: {e_sec}")
                except Exception as e:
                    claims = ""
                    log(f"[WARN] Could not extract claims at all: {e}")

                description = ""
                try:
                    # Wait for either 'descriptionText' or 'section#description' to be present
                    wait = WebDriverWait(driver, 10)
                    # Wait for either element to be present (use whichever appears first)
                    try:
                        desc_elem = wait.until(EC.presence_of_element_located((By.ID, "descriptionText")))
                        description = desc_elem.text.strip()
                        log(f"[DEBUG] Description found (by ID): {len(description)} chars")
                    except Exception as e_id:
                        log(f"[WARN] Could not extract description by ID: {e_id}")
                        try:
                            desc_section = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "section#description")))
                            try:
                                patent_text_elem = desc_section.find_element(By.TAG_NAME, "patent-text")
                                description = patent_text_elem.text.strip()
                                log(f"[DEBUG] Description found (by section/patent-text): {len(description)} chars")
                            except Exception as e_tag:
                                description = desc_section.text.strip()
                                log(f"[DEBUG] Description found (by section): {len(description)} chars")
                        except Exception as e_sec:
                            description = ""
                            log(f"[WARN] Could not extract description by section selector: {e_sec}")
                except Exception as e:
                    description = ""
                    log(f"[WARN] Could not extract description at all: {e}")

               # Inventor & Assignee
                try:
                    imp_dl = driver.find_element(By.CSS_SELECTOR, "dl.important-people.style-scope.patent-result")
                    children = imp_dl.find_elements(By.XPATH, "./*")

                    inventor_list = []
                    assignee = ""

                    current_label = ""

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
                    inventor = ""
                    assignee = ""
                    log(f"[WARN] Could not extract inventor/assignee: {e}")


                classification = ""

                try:
                    # 1️⃣ View more classifications butonuna tıkla (varsa)
                    try:
                        more_button = driver.find_element(By.XPATH, "//div[contains(text(),'more classifications')]")
                        driver.execute_script("arguments[0].click();", more_button)
                        time.sleep(1)
                        log("[DEBUG] Clicked 'View more classifications'")
                    except Exception as e:
                        log(f"[INFO] No 'View more classifications' button: {e}")

                    # 2️⃣ Yöntem 1: JS ile shadowRoot içeriğini almayı dene
                    js_script = """
                    const viewer = document.querySelector('classification-viewer');
                    if (!viewer) return '[DEBUG] viewer not found';
                    if (!viewer.shadowRoot) return '[DEBUG] shadowRoot not accessible';

                    const trees = viewer.shadowRoot.querySelectorAll('classification-tree');
                    if (!trees.length) return '[DEBUG] No classification-tree found';

                    return Array.from(trees).map(e => e.textContent.trim()).filter(Boolean).join('\\n');
                    """
                    classification = driver.execute_script(js_script)
                    #log(f"[DEBUG] JS classification result:\n{classification}")

                    # Eğer shadowRoot erişimi başarısızsa fallback yap
                    if "[DEBUG]" in classification or not classification.strip():
                        raise Exception("JS-based extraction failed, trying outerHTML fallback...")

                except Exception as e:
                    log(f"[INFO] JS extraction failed: {e}")

                    # 3️⃣ Yöntem 2: outerHTML ile DOM'dan çekip regex ile ayıkla
                    try:
                        outer_html = driver.execute_script("""
                            const el = document.querySelector('classification-viewer');
                            return el ? el.outerHTML : '[viewer not found]';
                        """)
                        log(f"[DEBUG] Outer HTML fetched.")

                        matches = re.findall(r'<classification-tree[^>]*>(.*?)</classification-tree>', outer_html, re.DOTALL)
                        classification_list = [re.sub('<[^<]+?>', '', m).strip() for m in matches if m.strip()]
                        classification = "\n".join(classification_list)

                        # ✅ Buraya kod-parçala-ve-listele kısmı
                        rows = []
                        buffer_code = None

                        for line in classification_list:
                            line = re.sub(r'\s+', ' ', line).replace('\xa0', ' ').strip()
                            # Kod formatında mı?
                            if re.match(r'^[A-Z]{1,4}[0-9]{0,4}[A-Z]?[0-9]{0,4}/?[0-9]*$', line):
                                buffer_code = line
                            elif buffer_code and line and not re.match(r'^[A-Z]{1,10}$', line):
                                # Koddan sonra gelen açıklamayı eşleştir
                                rows.append((buffer_code, line))
                                buffer_code = None  # sıfırla
                            else:
                                buffer_code = None  # eşleşmediyse geç

                        # Logla
                        for i, (code, desc) in enumerate(rows, 1):
                            log(f"[CLASS {i}] {code} — {desc}")
                        # ✅ BURASI EKLENSİN
                        if rows:
                            classification = "\n".join([f"{code} — {desc}" for code, desc in rows])

                    except Exception as e:
                        log(f"[ERROR] Could not extract classification from outerHTML: {e}")
                        classification = ""

                # 🎯 Sonuç
               # if classification:
                #    log(f"[RESULT] Final classification result:\n{classification}")
                #else:
                 #   log("[WARN] No classification data could be extracted.")
  


                # Citations
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
                    log(f"[DEBUG] Citations found: {len(citations_list)} items")
                except Exception as e:
                    citations = ""
                    log(f"[WARN] Could not extract citations: {e}")
                    

                # === Date Published (all timeline events as single string) ===
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

                    date_published = "; ".join(timeline_items)  # tek string halinde
                    log(f"[DEBUG] Date Published (timeline): {len(timeline_items)} events")
                except Exception as e:
                    date_published = ""
                    log(f"[WARN] Could not extract timeline events: {e}")

            except Exception as e:
                log(f"[WARN] Failed to extract details from {link}: {e}")

            # Merge all fields for final output
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
        log_file = "patent_scraper_log.xlsx"
        pd.DataFrame(log_rows).to_excel(log_file, index=False)
        log(f"[INFO] Log file saved to {log_file}")
        driver.quit()
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




