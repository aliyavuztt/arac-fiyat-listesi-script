"""Toyota Türkiye fiyat listesi scraper.

Toyota fiyat listesi tek bir sayfada render edilir:
    https://turkiye.toyota.com.tr/middle/fiyat-listesi/

Sayfa yapısı (tespit edilen):
  - Tek sayfa, tüm modeller. Her model bir <h2> başlığıyla ayrılır
    (Corolla, Corolla Hybrid, Corolla Cross Hybrid, Toyota C-HR Hybrid,
     Corolla Hatchback Hybrid, Yaris Cross Hybrid, Yaris Hybrid,
     Land Cruiser Prado, Hilux, Proace City, Proace City Cargo,
     Proace Verso, Proace Cargo, Proace Max, Proace Max Kamyonet).
  - Her model için BİRDEN FAZLA tablo bulunur:
      1) Ana fiyat tablosu (yatay): ilk hücre "Versiyon" başlığıdır.
         Kolonlar = [Versiyon,
                     Liste/Hybrid'e Özel ÖTV Oranı fiyatı,   -> tavsiye_edilen_fiyat
                     Kampanyalı Fiyatlar*,                    -> kampanyali_fiyat
                     (opsiyonel) Özel müşteri kolonu]         -> yok sayılır
         Hibrit olmayan modellerde 2. kolon "Liste Fiyatları",
         hibrit modellerde "Hybrid'e Özel ÖTV Oranı ile Fiyat" olur; ikisi de
         anahtar teslim liste fiyatıdır.
      2) ÖTV Muafiyetli tablo (2 kolonlu): "ÖTV Muafiyetli Müşterilere Özel"
         tek fiyat kolonu -> YOK SAYILIR (özel engelli ÖTV muafiyeti; ana
         liste fiyatı modeline uymaz).
      3) Renk farkı tablosu (table-detail-renk sınıfı): metalik/sedefli renk
         farkı tek satırı -> YOK SAYILIR (versiyona özel opsiyon değil).
      4) Dikey (mobil) tablolar: anahtar fiyatlara göre satır satır dizilmiş
         tekrar kopyası -> YOK SAYILIR (ilk hücresi "Versiyon" değil, doğrudan
         versiyon adıdır; bu sayede kendiliğinden elenir).
  - Fiyatlar "TL" biçiminde (örn. "2.207.000 TL"). para_birimi = "TL".
  - Kampanyalı fiyat "-" ise kampanyali_fiyat = None.
  - Land Cruiser Prado için iki ana tablo bulunur (aynı versiyon adı, farklı
    fiyat). Versiyon adına göre dedup uygulanır; ilk görülen fiyat korunur.
  - Model yılı, sayfa altındaki "... 2026 itibariyle geçerli ..." notundan
    çıkarılır (örn. 2026).
"""
import re
from typing import Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

import config
from Models.vehicle_price_record import VehiclePriceRecord
from Scrapers.base_scraper import BaseScraper
from Services.logger import get_logger

logger = get_logger(__name__)

TOYOTA_PRICE_URL = "https://turkiye.toyota.com.tr/middle/fiyat-listesi/"

# Fiyat hücresi tespiti için alt sınır (TL)
PRICE_MIN_NUMERIC = 1000

# Renk farkı tablosu CSS sınıfı
RENK_TABLE_CLASS = "table-detail-renk"

# ÖTV muafiyetli tabloları eleyen başlık anahtarı (büyük/küçük duyarsız)
OTV_MUAFIYETLI_KEY = "ötv muafiyetli"

# Geçerlilik/yıl notundan model yılını çıkaran desen: "2026 itibariyle geçerli"
YEAR_RE = re.compile(r"(20\d{2})\s+itibariyle\s+geçerli", re.IGNORECASE)

# Araç tipi: listede olmayan modeller "Binek" kabul edilir.
# Hilux (pikap) ve Proace ailesi (ticari van/kamyonet) -> Ticari.
TICARI_MODELS: Set[str] = {
    "Hilux",
    "Proace City",
    "Proace City Cargo",
    "Proace Verso",
    "Proace Cargo",
    "Proace Max",
    "Proace Max Kamyonet",
}

# Model başlığı olmayan <h2>leri eleme (çerez/gizlilik penceresi)
NON_MODEL_H2_HINTS = ("deneyim", "gizlilik", "tercih")


class ToyotaScraper(BaseScraper):
    """Toyota fiyat listesini kazar (tek sayfa, tüm modeller)."""

    @property
    def brand_name(self) -> str:
        return "Toyota"

    def scrape(self) -> List[VehiclePriceRecord]:
        records, err = self.safe_run(
            logger, "Toyota", self._scrape_page, TOYOTA_PRICE_URL
        )
        if err:
            return []
        return records or []

    # ------------------------------------------------------------------ #
    # Sayfa yükleme
    # ------------------------------------------------------------------ #

    def _scrape_page(self, url: str) -> List[VehiclePriceRecord]:
        """Fiyat listesi sayfasını yükle ve kazar."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.HEADLESS)
            page = browser.new_page()
            try:
                logger.info(f"[Toyota] sayfa yükleniyor: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)

                if not self._wait_for_tables(page):
                    logger.warning("[Toyota] tablo bulunamadı")
                    return []

                html = page.content()
                records = self._parse_html(html, url)
                logger.info(f"[Toyota] {len(records)} kayıt çıkarıldı")
                return records
            finally:
                browser.close()

    def _wait_for_tables(self, page: Page) -> bool:
        """Tablolar DOM'a eklenene ve fiyatlar render olana kadar bekler.

        Toyota'da tablolar görünür (visible) değil; bu yüzden 'state=attached'
        ile varlığı beklenir, ardından fiyat hücrelerinin dolması için kısa bir
        süre daha beklenir.
        """
        try:
            page.wait_for_selector("table", state="attached", timeout=20000)
        except PlaywrightTimeout:
            return False
        except Exception:
            return False

        # Fiyatların render olmasını bekle: en az bir td içinde "TL" + 6+ rakam
        try:
            page.wait_for_function(
                "() => [...document.querySelectorAll('td')].some(e => "
                "/\\d{6,}.*TL|TL.*\\d{6,}/.test(e.textContent))",
                timeout=15000,
            )
        except Exception:
            # JS bekleme başarısız olursa sabit bekleyip devam et
            page.wait_for_timeout(3000)
        return True

    # ------------------------------------------------------------------ #
    # HTML parse
    # ------------------------------------------------------------------ #

    def _parse_html(self, html: str, url: str) -> List[VehiclePriceRecord]:
        """Sayfadaki her modelin ana fiyat tablosunu işler."""
        soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []

        model_year = self._extract_year(html)
        # model <h2> elementi -> normalize model adı (sıralı)
        h2_to_model: Dict[int, str] = {}
        for h2 in soup.find_all("h2"):
            name = self._clean_model_name(h2.get_text(strip=True))
            if name and self._is_model_heading(name):
                h2_to_model[id(h2)] = name

        records: List[VehiclePriceRecord] = []
        seen: Set[Tuple[str, str]] = set()  # (model, versiyon) dedup

        for table in tables:
            h2 = table.find_previous("h2")
            if h2 is None:
                continue
            model = h2_to_model.get(id(h2))
            if not model:
                continue

            if not self._is_main_price_table(table):
                continue

            arac_tipi = "Ticari" if model in TICARI_MODELS else "Binek"
            self._parse_price_table(
                table, model, arac_tipi, model_year, url, records, seen
            )

        return records

    @staticmethod
    def _extract_year(html: str) -> Optional[int]:
        """Geçerlilik notundan model yılını döndürür (örn. 2026). Bulamazsa None."""
        m = YEAR_RE.search(html)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _is_model_heading(name: str) -> bool:
        """<h2> bir model başlığı mı (çerez/gizlilik penceresi değil)."""
        lower = name.lower()
        return not any(h in lower for h in NON_MODEL_H2_HINTS)

    @staticmethod
    def _is_main_price_table(table: Tag) -> bool:
        """Tablo ana yatay fiyat tablosu mu.

        Kriterler:
          - 'table-detail-renk' sınıfı DEĞİL (renk farkı tablosu),
          - başlık satırının ilk hücresi 'Versiyon',
          - başlık 'ötv muafiyetli' İÇERMEZ (özel engelli ÖTV tablosu).
        Böylece 2-kolonlu ÖTV tabloları, renk tabloları ve dikey (mobil)
        tekrar tabloları elenir.
        """
        classes = table.get("class", []) or []
        if RENK_TABLE_CLASS in classes:
            return False

        rows = table.find_all("tr")
        if not rows:
            return False
        header_cells = rows[0].find_all(["td", "th"])
        if not header_cells:
            return False

        first = header_cells[0].get_text(strip=True).lower()
        if first != "versiyon":
            return False

        header_text = " ".join(
            c.get_text(" ", strip=True) for c in header_cells
        ).lower()
        if OTV_MUAFIYETLI_KEY in header_text:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Fiyat tablosu parse
    # ------------------------------------------------------------------ #

    def _parse_price_table(
        self,
        table: Tag,
        model: str,
        arac_tipi: str,
        model_year: Optional[int],
        url: str,
        records: List[VehiclePriceRecord],
        seen: Set[Tuple[str, str]],
    ) -> None:
        """Ana fiyat tablosundaki her versiyon için bir kayıt üret.

        Kolon eşlemi (3 ve 4 kolonlu tablolar için ortak):
          [0] Versiyon
          [1] Liste/Hybrid anahtar teslim fiyatı -> tavsiye_edilen_fiyat
          [2] Kampanyalı fiyat                  -> kampanyali_fiyat ('-' ise None)
          [3] (varsa) özel müşteri kolonu       -> yok sayılır
        """
        rows = table.find_all("tr")
        for row in rows[1:]:  # başlık satırını atla
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 3:
                continue

            versiyon = self._clean_versiyon(cells[0])
            if not versiyon:
                continue

            # Aynı (model, versiyon) ilk görülende korunur (Land Cruiser Prado
            # gibi çift ana tablo durumunda tekrarı engeller).
            key = (model, versiyon)
            if key in seen:
                continue

            tavsiye = VehiclePriceRecord.format_price(cells[1])
            if tavsiye is None:
                continue  # anahtar teslim fiyatı yoksa satır anlamsız

            kampanyali = VehiclePriceRecord.format_price(cells[2])

            seen.add(key)
            records.append(VehiclePriceRecord(
                marka=self.brand_name,
                model=model,
                versiyon=versiyon,
                model_yili=model_year,
                arac_tipi=arac_tipi,
                tavsiye_edilen_fiyat=tavsiye,
                kampanyali_fiyat=kampanyali,
                para_birimi="TL",
                kaynak_url=url,
            ))

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean_model_name(raw: str) -> str:
        """Model başlığını temizler.

        'Toyota C-HR Hybrid' -> 'C-HR Hybrid'. Öndeki 'Toyota ' öneki kaldırılır;
        kalan metin olduğu gibi korunur (C-HR, e-CVT gibi kısaltmalar bozulmasın).
        """
        name = raw.strip()
        if name.upper().startswith("TOYOTA "):
            name = name[len("TOYOTA "):]
        return name.strip()

    @staticmethod
    def _clean_versiyon(raw: str) -> str:
        """Versiyon adından sondaki dipnot işaretlerini (*) temizler.

        Örn. '1.8 Hybrid Flame e-CVT***' -> '1.8 Hybrid Flame e-CVT'
        """
        return re.sub(r"\s*\*+\s*$", "", raw).strip()
