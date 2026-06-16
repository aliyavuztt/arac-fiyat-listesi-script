"""Merkezi yapılandırma."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")

# Playwright ayarları
HEADLESS = True
PAGE_TIMEOUT_MS = 30_000  # Sayfa yükleme zaman aşımı (ms)

# Çıktı dosyası adına çalıştırma tarih-saatini ekle.
# True  -> Renault_2026-06-15_2129.xlsx (her çalıştırmada yeni dosya, tarihçe birikir)
# False -> Renault.xlsx (her zaman aynı dosya, üzerine yazılır)
USE_TIMESTAMP_IN_FILENAME = True

# Marka kaydı — yeni marka eklemek için buraya bir satır ekleyin.
# Örnek: "Scrapers.toyota_scraper.ToyotaScraper"
BRAND_SCRAPERS = [
    "Scrapers.renault_scraper.RenaultScraper",
    "Scrapers.opel_scraper.OpelScraper",
    "Scrapers.dacia_scraper.DaciaScraper",
    "Scrapers.toyota_scraper.ToyotaScraper",
]
