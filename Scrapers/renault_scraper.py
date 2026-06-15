"""Renault Türkiye fiyat listesi scraper.

Fiyat listeleri bir alt alan (best.renault.com.tr) içinde, her model için
ayrı bir <table> + opsiyonlar <table> olarak render edilir. Bu scraper
alt alan URL'lerini doğrudan gezer.

Sayfa yapısı (tespit edilen):
  - Her model için iki tablo:
      1) Fiyat tablosu: satırlar = "versiyon_adi | fiyat"
         (bazen 3 kolon: versiyon | Tavsiye Edilen | Anahtar Teslim Liste Fiyatı)
      2) Opsiyonlar tablosu: başlığı "Opsiyonlar" olur.
         İlk satır = versiyon adı (örn. "evolution plus"),
         diğer satırlar = "opsiyon_adi | fiyat".
  - Model adı tablodan önce gelen başlık elementindedir
    (h1/h2/h3/h4/strong/b), ör. "YENİ CLIO", "R5 E-TECH ELEKTRİKLİ".
"""
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

import config
from Models.vehicle_price_record import VehiclePriceRecord
from Scrapers.base_scraper import BaseScraper
from Services.logger import get_logger

logger = get_logger(__name__)

# Fiyat listesi alt alanı — her kategori (Binek/Ticari) için ayrı URL
RENAULT_PRICE_URLS = {
    "Binek": "https://best.renault.com.tr/fiyat-listesi/?kat=Binek",
    "Ticari": "https://best.renault.com.tr/fiyat-listesi/?kat=Ticari",
}

# Opsiyon tablosu tanıma — tablodan önce gelen başlık
OPTION_HEADER_TEXT = "Opsiyonlar"

# Fiyat hücresi tespiti için alt sınır (TL)
PRICE_MIN_NUMERIC = 1000


class RenaultScraper(BaseScraper):
    """Renault binek + ticari fiyat listelerini kazar."""

    @property
    def brand_name(self) -> str:
        return "Renault"

    def scrape(self) -> List[VehiclePriceRecord]:
        all_records: List[VehiclePriceRecord] = []
        for arac_tipi, url in RENAULT_PRICE_URLS.items():
            records, err = self.safe_run(
                logger, f"Renault {arac_tipi}", self._scrape_page, url, arac_tipi
            )
            if err:
                continue
            if records:
                all_records.extend(records)
        return all_records

    # ------------------------------------------------------------------ #
    # Sayfa yükleme
    # ------------------------------------------------------------------ #

    def _scrape_page(self, url: str, arac_tipi: str) -> List[VehiclePriceRecord]:
        """Tek bir fiyat listesi sayfasını yükle ve kazar."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.HEADLESS)
            page = browser.new_page()
            try:
                logger.info(f"[Renault {arac_tipi}] sayfa yükleniyor: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)

                # Tabloların render olması için bekle
                if not self._wait_for_tables(page):
                    logger.warning(f"[Renault {arac_tipi}] tablo bulunamadı")
                    return []

                html = page.content()
                records = self._parse_html(html, url, arac_tipi)
                logger.info(f"[Renault {arac_tipi}] {len(records)} kayıt çıkarıldı")
                return records
            finally:
                browser.close()

    def _wait_for_tables(self, page: Page) -> bool:
        """Sayfada en az bir tablonun render olmasını bekler."""
        try:
            page.wait_for_selector("table", timeout=15000)
            # Tabloların tam dolması için ek süre
            page.wait_for_timeout(2000)
            return True
        except PlaywrightTimeout:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # HTML parse
    # ------------------------------------------------------------------ #

    def _parse_html(
        self, html: str, url: str, arac_tipi: str
    ) -> List[VehiclePriceRecord]:
        """Tüm sayfa HTML'ini gezerek tabloları işler.

        Strateji: tabloları sırayla dolaşır; her tablo için tablodan önce gelen
        en yakın başlığı bulur. Başlık 'Opsiyonlar' ise opsiyon tablosu,
        aksi halde model fiyat tablosu olarak işlenir.
        """
        soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []

        records: List[VehiclePriceRecord] = []
        # En son işlenen model ve versiyon->fiyat haritası (opsiyon eşleştirme için)
        current_model = ""
        version_prices: dict[str, str] = {}

        for table in tables:
            header_text = self._previous_heading_text(table)
            if not header_text:
                continue

            if OPTION_HEADER_TEXT.lower() in header_text.lower():
                # Opsiyon tablosu — current_model altındaki versiyonlara bağlanır
                self._parse_option_table(
                    table, current_model, version_prices, url, arac_tipi, records
                )
            else:
                # Fiyat tablosu — yeni model başlangıcı
                model_name = self._clean_model_name(header_text)
                if model_name:
                    current_model = model_name
                    version_prices = self._parse_price_table(
                        table, current_model, url, arac_tipi, records
                    )

        return records

    def _previous_heading_text(self, table: Tag) -> str:
        """Tablodan önce gelen en yakın başlık/strong elementinin metnini döndür."""
        for elem in table.find_all_previous(["h1", "h2", "h3", "h4", "h5", "strong", "b"]):
            txt = elem.get_text(strip=True)
            if txt and 1 < len(txt) <= 80:
                return txt
        return ""

    # ------------------------------------------------------------------ #
    # Fiyat tablosu parse
    # ------------------------------------------------------------------ #

    def _parse_price_table(
        self,
        table: Tag,
        model: str,
        url: str,
        arac_tipi: str,
        records: List[VehiclePriceRecord],
    ) -> dict[str, str]:
        """Fiyat tablosunu parse eder; versiyon->fiyat haritasını döndürür.

        Tablo satır formatı:
          - 2 kolon: versiyon_adi | fiyat
          - 3 kolon: versiyon_adi | tavsiye_edilen_fiyat | anahtar_teslim_fiyat
                     (son dolu kolon fiyat kabul edilir)
        """
        version_prices: dict[str, str] = {}
        rows = table.find_all("tr")

        # İlk satır header olabilir (th içerir veya fiyat olmayan bir satır)
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            # Header satırını atla (örn. "Tavsiye Edilen", "Anahtar Teslim")
            if self._is_header_row(cells):
                continue

            # Versiyon adı = ilk hücre; fiyat = son dolu fiyat hücresi
            versiyon = cells[0]
            price = self._extract_price_from_cells(cells[1:])
            if not versiyon or not price:
                continue

            # Aynı versiyon adını tekrar işleme (güvenlik)
            if versiyon in version_prices:
                continue
            formatted_price = VehiclePriceRecord.format_price(price)
            version_prices[versiyon] = formatted_price

            records.append(VehiclePriceRecord(
                marka=self.brand_name,
                model=model,
                versiyon=versiyon,
                model_yili=self._extract_year_from_header(table),
                arac_tipi=arac_tipi,
                tavsiye_edilen_fiyat=formatted_price,
                para_birimi="₺",
                kaynak_url=url,
            ))

        return version_prices

    def _parse_option_table(
        self,
        table: Tag,
        model: str,
        version_prices: dict[str, str],
        url: str,
        arac_tipi: str,
        records: List[VehiclePriceRecord],
    ):
        """Opsiyon tablosunu parse eder.

        Opsiyon tablosu yapısı:
          - İlk veri satırı = versiyon adı kısa adı (örn. "evolution plus")
          - Sonraki satırlar = "opsiyon_adi | fiyat"
          - Versiyon adı, fiyat tablosundaki tam versiyon adının başlangıcıdır;
            bu nedenle kısmi önek eşleştirmesi yapılır.
        """
        rows = table.find_all("tr")
        current_version = ""
        current_version_price: Optional[str] = None

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            if self._is_header_row(cells):
                continue

            price = self._extract_price_from_cells(cells[1:])
            first_cell = cells[0]

            if price is None:
                # Fiyat yok — bu bir versiyon adı satırı (opsiyon tablosu içinde)
                matched = self._match_version(first_cell, version_prices)
                if matched:
                    current_version, current_version_price = matched
                continue

            # Opsiyon satırı
            if not current_version or not first_cell:
                continue
            records.append(VehiclePriceRecord(
                marka=self.brand_name,
                model=model,
                versiyon=current_version,
                model_yili=None,
                arac_tipi=arac_tipi,
                tavsiye_edilen_fiyat=current_version_price,
                para_birimi="₺",
                opsiyon_adi=first_cell,
                opsiyon_fiyati=VehiclePriceRecord.format_price(price),
                kaynak_url=url,
            ))

    @staticmethod
    def _match_version(
        short_name: str, version_prices: dict[str, str]
    ) -> Optional[tuple[str, str]]:
        """Kısa versiyon adını fiyat tablosundaki tam versiyonla eşleştir.

        Eşleştirme stratejisi (öncelik sırasıyla):
          1) Birebir eşleşme
          2) Tam versiyon adı, kısa ad ile başlıyorsa
          3) Kısa ad, tam versiyon adı içinde geçiyorsa (ilk eşleşme)
        Dönüş: (tam_versiyon_adi, fiyat) veya None.
        """
        if not short_name:
            return None
        sn = short_name.strip().lower()
        # 1) Birebir
        for full, price in version_prices.items():
            if full.strip().lower() == sn:
                return full, price
        # 2) Tam versiyon, kısa ad ile başlıyorsa
        for full, price in version_prices.items():
            if full.strip().lower().startswith(sn):
                return full, price
        # 3) Kısa ad tam versiyon içinde geçiyorsa
        for full, price in version_prices.items():
            if sn in full.strip().lower():
                return full, price
        return None

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_header_row(cells: List[str]) -> bool:
        """Satır header mı (örn. 'Tavsiye Edilen', 'Model Yılı')."""
        header_markers = [
            "tavsiye edilen",
            "anahtar teslim",
            "liste fiyatı",
            "liste fiyati",
            "model yılı",
            "model yili",
            "2026 model",
            "2025 model",
            "versiyon",
            "version",
        ]
        if not cells:
            return False
        joined = " ".join(cells).lower()
        return any(m in joined for m in header_markers)

    @staticmethod
    def _extract_price_from_cells(cells: List[str]) -> Optional[str]:
        """Hücre listesindeki son fiyat değerini döndürür.

        Fiyat tespiti: ₺ içeren veya 1.000+ sayısal değere sahip hücre.
        """
        for c in reversed(cells):
            if not c:
                continue
            if "₺" in c or "tl" in c.lower():
                return c.strip()
            cleaned = c.replace(".", "").replace(",", "").replace(" ", "").lower()
            cleaned = cleaned.replace("tl", "")
            if cleaned.isdigit() and int(cleaned) >= PRICE_MIN_NUMERIC:
                return c.strip()
        return None

    @staticmethod
    def _extract_year_from_header(table: Tag) -> Optional[int]:
        """Tablo header'ından model yılını çıkarır (örn. '2026 Model')."""
        for row in table.find_all("tr"):
            text = row.get_text(" ", strip=True)
            for token in text.split():
                token = token.strip()
                if token.isdigit() and 1990 <= int(token) <= 2100:
                    return int(token)
        return None

    @staticmethod
    def _clean_model_name(raw: str) -> str:
        """Model başlığını temizler.

        Örnekler:
          'YENİ CLIO'      -> 'Clio'   (YENİ öneki kaldırılır, başlık düzeltmesi)
          'R5 E-TECH ...'  -> olduğu gibi bırakılır
        Ön ek olarak kabul edilen kelimeler: YENİ, NEW
        """
        name = raw.strip()
        # Başındaki 'YENİ' / 'NEW' önekini kaldır
        prefixes = ("YENİ ", "YENI ", "NEW ", "YENİ-", "YENI-")
        upper = name.upper()
        for pfx in prefixes:
            if upper.startswith(pfx):
                name = name[len(pfx):]
                break
        return name.strip()
