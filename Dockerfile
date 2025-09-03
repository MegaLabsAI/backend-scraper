log_rows = []
def log(msg):
    print(msg)
    log_rows.append({"event": msg})

log("[INFO] Starting Google Patents scraper...")

options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--disable-software-rasterizer")

# Chromium yolu (Debian'da /usr/bin/chromium)
chrome_bin = os.getenv("CHROME_BIN", "/usr/bin/chromium")
if os.path.exists(chrome_bin):
    options.binary_location = chrome_bin
else:
    log(f"[WARN] Chrome binary not found at {chrome_bin}; letting Selenium Manager try.")

# Chromedriver yolu (symlink /usr/bin/chromedriver -> /usr/lib/chromium/chromedriver)
driver_path = os.getenv("CHROMEDRIVER", "/usr/bin/chromedriver")

driver = None
try:
    if os.path.exists(driver_path):
        log(f"[INFO] Using chromedriver at {driver_path}")
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        log(f"[WARN] Chromedriver not found at {driver_path}; using Selenium Manager fallback.")
        driver = webdriver.Chrome(options=options)  # fallback
except Exception as e:
    log(f"[ERROR] Failed to start Chrome with explicit path: {e}")
    # Son bir kez daha Selenium Manager'a bırak (bazı ortamlarda ilk deneme patlayabiliyor)
    driver = webdriver.Chrome(options=options)

wait = WebDriverWait(driver, 10)