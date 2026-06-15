"""Dacia Türkiye fiyat listesi scraper.

Dacia'nın fiyat listesi, Renault ile aynı altyapıyı (best.renault.com.tr)
kullanır ve aynı tablo yapısına sahiptir: her model için bir fiyat tablosu
+ bir "Opsiyonlar" tablosu. Dacia fiyat listesi sayfası
(https://www.dacia.com.tr/dacia-fiyat-listesi.html) içeriği bir iframe
üzerinden `best.renault.com.tr/dacia/fiyat-listesi/?kat=all` adresinden yükler;
bu scraper iframe URL'sini doğrudan gezer.

Sayfa yapısı (tespit edilen):
  - Tek sayfa, tüm modeller (Sandero, Sandero Stepway, Logan, Jogger).
  - Her model için iki tablo:
      1) Fiyat tablosu: kolonlar =
         [versiyon, (2025 Model Tavsiye Edilen)?, 2026 Model Tavsiye Edilen]
         Bazı modellerde yalnızca 2026 kolonu bulunur (Stepway, Logan, Jogger);
         Sandero'da hem 2025 hem 2026 kolonu vardır. En yeni yıl (2026) tercih
         edilir; 2025 kolonu yok sayılır.
      2) Opsiyonlar tablosu: başlığı "Opsiyonlar" olur.
         İlk satır = donanım seviyesi kısa adı (örn. "Essential"),
         diğer satırlar = "opsiyon_adi | fiyat".
  - Model adı tablodan önce gelen <h2> elementindedir (örn. "YENİ SANDERO").
  - Fiyatlar ₺ sembolü ile; para_birimi="₺" kullanılır.
  - Kampanyalı fiyat kavramı yoktur (tavsiye_edilen_fiyat her zaman dolu,
    kampanyali_fiyat her zaman None).
"""
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

import config
from Models.vehicle_price_record import VehiclePriceRecord
from Scrapers.base_scraper import BaseScraper
from Services.logger import get_logger

logger = get_logger(__name__)

# Dacia fiyat listesi — Renault altyapısı üzerinde ayrı bir alt yol.
# ana sayfadaki iframe'in src niteliğiyle aynı URL.
DACIA_PRICE_URL = "https://best.renault.com.tr/dacia/fiyat-listesi/?kat=all"

# Opsiyon tablosu tanıma — tablodan önce gelen başlık
OPTION_HEADER_TEXT = "Opsiyonlar"

# Fiyat hücresi tespiti için alt sınır (TL)
PRICE_MIN_NUMERIC = 1000


class DaciaScraper(BaseScraper):
    """Dacia fiyat listesini kazar (tek sayfa, tüm modeller)."""

    @property
    def brand_name(self) -> str:
        return "Dacia"

    def scrape(self) -> List[VehiclePriceRecord]:
        records, err = self.safe_run(
            logger, "Dacia", self._scrape_page, DACIA_PRICE_URL
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
                logger.info(f"[Dacia] sayfa yükleniyor: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)

                if not self._wait_for_tables(page):
                    logger.warning("[Dacia] tablo bulunamadı")
                    return []

                html = page.content()
                records = self._parse_html(html, url)
                logger.info(f"[Dacia] {len(records)} kayıt çıkarıldı")
                return records
            finally:
                browser.close()

    def _wait_for_tables(self, page: Page) -> bool:
        """Sayfada en az bir tablonun render olmasını bekler."""
        try:
            page.wait_for_selector("table", timeout=15000)
            page.wait_for_timeout(2000)
            return True
        except PlaywrightTimeout:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # HTML parse
    # ------------------------------------------------------------------ #

    def _parse_html(self, html: str, url: str) -> List[VehiclePriceRecord]:
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
        current_model = ""
        # versiyon -> (tam_fiyat, donanim) — opsiyon eşleştirme için
        version_map: List[Tuple[str, str, str]] = []

        for table in tables:
            header_text = self._previous_heading_text(table)
            if not header_text:
                continue

            if OPTION_HEADER_TEXT.lower() in header_text.lower():
                self._parse_option_table(
                    table, current_model, version_map, url, records
                )
            else:
                model_name = self._clean_model_name(header_text)
                if model_name:
                    current_model = model_name
                    version_map = self._parse_price_table(
                        table, current_model, url, records
                    )

        return records

    def _previous_heading_text(self, table: Tag) -> str:
        """Tablodan önce gelen en yakın başlık elementinin metnini döndür."""
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
        records: List[VehiclePriceRecord],
    ) -> List[Tuple[str, str, str]]:
        """Fiyat tablosunu parse eder.

        Bazı Dacia tabloları birden çok yıl kolonu içerebilir (2025 + 2026).
        En yeni yıl (2026 > 2025 > yılsız) seçilir ve yalnızca o kolondaki
        fiyatlar kullanılır.

        Dönüş: [(versiyon, tavsiye_fiyati, donanim)] — opsiyon eşleştirme için.
        """
        rows = table.find_all("tr")
        if not rows:
            return []

        # Header satırından her fiyat kolonu için yıl çıkar
        header_row = rows[0]
        headers = [c.get_text(" ", strip=True) for c in header_row.find_all(["td", "th"])]
        col_years = self._classify_year_columns(headers)
        target_year = self._pick_target_year(col_years)

        version_map: List[Tuple[str, str, str]] = []

        for row in rows[1:]:
            cells = [c for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            # İlk kolon = versiyon adı
            versiyon = cell_texts[0]
            if not versiyon or self._is_header_row(cell_texts):
                continue

            # target_year'a uyan kolondaki fiyatı al
            price = self._pick_year_price(cell_texts, col_years, target_year)
            if not price:
                continue

            formatted = VehiclePriceRecord.format_price(price)
            # Donanım seviyesi = versiyonun ilk kelimesi (örn. "essential TCe 100" -> "essential")
            donanim = versiyon.split()[0] if versiyon.split() else versiyon
            version_map.append((versiyon, price, donanim))

            records.append(VehiclePriceRecord(
                marka=self.brand_name,
                model=model,
                versiyon=versiyon,
                model_yili=target_year,
                arac_tipi="Binek",
                tavsiye_edilen_fiyat=formatted,
                para_birimi="₺",
                kaynak_url=url,
            ))

        return version_map

    @staticmethod
    def _classify_year_columns(headers: List[str]) -> List[Optional[int]]:
        """Her kolon için model yılını döndürür.

        İlk kolon (versiyon) None olur. Fiyat kolonlarındaki yıl:
          - '2026 model' -> 2026
          - '2025 model' -> 2025
          - yıl bulunamazsa None (yılsız tek kolonlu tablolar için güvenlik).

        Kabul edilen biçimler: "2026 Model Tavsiye Edilen Anahtar Teslim ..."
        """
        meta: List[Optional[int]] = []
        for h in headers:
            lower = h.lower()
            year: Optional[int] = None
            # En yüksek yıla eşleş (2026 > 2025) — tek başlıkta ikisi olmaz ama güvenlik için
            for y in (2027, 2026, 2025, 2024):
                if str(y) in lower:
                    year = y
                    break
            meta.append(year)
        return meta

    @staticmethod
    def _pick_target_year(col_years: List[Optional[int]]) -> Optional[int]:
        """En yüksek yıl önceliğini döndür: 2026 > 2025 > None."""
        years = [y for y in col_years if y is not None]
        if not years:
            return None
        return max(years)

    @staticmethod
    def _pick_year_price(
        cell_texts: List[str],
        col_years: List[Optional[int]],
        target_year: Optional[int],
    ) -> Optional[str]:
        """target_year'a uyan kolondaki ilk fiyatı döndür.

        Eğer target_year None ise (yılsız tablo), son fiyat hücresini döndürür
        (Renault eski davranışıyla uyumlu).
        """
        if target_year is None:
            # Son dolu fiyat hücresi
            for c in reversed(cell_texts[1:]):
                if DaciaScraper._is_price_cell(c):
                    return c
            return None

        # Hedef yıla uyan kolon(lar)daki fiyat
        for i in range(1, len(cell_texts)):
            if i >= len(col_years):
                break
            if col_years[i] != target_year:
                continue
            if DaciaScraper._is_price_cell(cell_texts[i]):
                return cell_texts[i]
        return None

    @staticmethod
    def _is_price_cell(text: str) -> bool:
        """Hücre metni fiyat mı."""
        if not text:
            return False
        if "₺" in text or "tl" in text.lower():
            return True
        cleaned = text.replace(".", "").replace(",", "").replace(" ", "").lower()
        cleaned = cleaned.replace("tl", "").replace("₺", "")
        return cleaned.isdigit() and int(cleaned) >= PRICE_MIN_NUMERIC

    # ------------------------------------------------------------------ #
    # Opsiyon tablosu parse
    # ------------------------------------------------------------------ #

    def _parse_option_table(
        self,
        table: Tag,
        model: str,
        version_map: List[Tuple[str, str, str]],
        url: str,
        records: List[VehiclePriceRecord],
    ) -> None:
        """Opsiyon tablosunu parse eder.

        Opsiyon tablosu yapısı (Renault ile aynı):
          - İlk veri satırı = donanım seviyesi kısa adı (örn. "Essential")
          - Sonraki satırlar = "opsiyon_adi | fiyat"
          - Her donanım seviyesi, fiyat tablosundaki versiyon adının ilk
            kelimesiyle eşleştirilir.
        """
        rows = table.find_all("tr")
        current_donanim = ""

        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue

            first_cell = cells[0]
            # Uyarı/not satırını atla (ikon + açıklama içerir, fiyatı yok)
            if not first_cell or self._is_disclaimer_row(first_cell):
                continue

            price = self._extract_price_from_cells(cells[1:])

            if price is None:
                # Fiyatsız satır — donanım seviyesi adı
                if first_cell:
                    current_donanim = first_cell.strip()
                continue

            # Opsiyon satırı — bu donanım seviyesine uyan tüm versiyonlara bağla
            matched = self._match_versions_by_donanim(current_donanim, version_map)
            if not matched:
                continue

            for versiyon, tavsiye_fiyati, _ in matched:
                records.append(VehiclePriceRecord(
                    marka=self.brand_name,
                    model=model,
                    versiyon=versiyon,
                    model_yili=None,
                    arac_tipi="Binek",
                    tavsiye_edilen_fiyat=VehiclePriceRecord.format_price(tavsiye_fiyati),
                    para_birimi="₺",
                    opsiyon_adi=first_cell,
                    opsiyon_fiyati=VehiclePriceRecord.format_price(price),
                    kaynak_url=url,
                ))

    @staticmethod
    def _match_versions_by_donanim(
        donanim: str, version_map: List[Tuple[str, str, str]]
    ) -> List[Tuple[str, str, str]]:
        """Donanım seviyesi adına göre eşleşen versiyonları döndür.

        Eşleştirme (öncelik sırasıyla):
          1) Tam eşleşme (donanim == versiyon'un ilk kelimesi, büyük/küçük duyarsız)
          2) Versiyon adı donanım seviyesi ile başlıyorsa
          3) Donanım seviyesi versiyon adı içinde geçiyorsa
        """
        if not donanim:
            return []
        sn = donanim.strip().lower()
        # 1) İlk kelimeye göre tam eşleşme
        matches = []
        for versiyon, fiyat, don in version_map:
            first_word = versiyon.split()[0].lower() if versiyon.split() else ""
            if first_word == sn:
                matches.append((versiyon, fiyat, don))
        if matches:
            return matches
        # 2) Versiyon, donanım seviyesi ile başlıyorsa
        for versiyon, fiyat, don in version_map:
            if versiyon.strip().lower().startswith(sn):
                matches.append((versiyon, fiyat, don))
        if matches:
            return matches
        # 3) Donanım seviyesi versiyon içinde geçiyorsa
        for versiyon, fiyat, don in version_map:
            if sn in versiyon.strip().lower():
                matches.append((versiyon, fiyat, don))
        return matches

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
            "versiyon",
            "version",
        ]
        if not cells:
            return False
        joined = " ".join(cells).lower()
        return any(m in joined for m in header_markers)

    @staticmethod
    def _is_disclaimer_row(text: str) -> bool:
        """Satırın uyarı/not satırı mı (örn. ikon + temsilidir açıklaması)."""
        if not text:
            return True
        lower = text.lower()
        return "temsilidir" in lower or "aksesuarlar" in lower

    @staticmethod
    def _extract_price_from_cells(cells: List[str]) -> Optional[str]:
        """Hücre listesindeki son fiyat değerini döndürür."""
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
    def _clean_model_name(raw: str) -> str:
        """Model başlığını temizler.

        Örnekler:
          'YENİ SANDERO'      -> 'Sandero'
          'YENİ SANDERO STEPWAY' -> 'Sandero Stepway'
          'YENİ LOGAN'        -> 'Logan'
          'YENİ JOGGER'       -> 'Jogger'
        Başındaki 'YENİ' / 'NEW' öneki kaldırılır; kalan kelimeler başlık
        düzeltmesiyle (Title Case) birleştirilir.
        """
        name = raw.strip()
        prefixes = ("YENİ ", "YENI ", "NEW ", "YENİ-", "YENI-")
        upper = name.upper()
        for pfx in prefixes:
            if upper.startswith(pfx):
                name = name[len(pfx):]
                break
        name = name.strip()
        # Title Case: her kelimenin ilk harfi büyük
        return " ".join(w.capitalize() for w in name.split()) if name else name
