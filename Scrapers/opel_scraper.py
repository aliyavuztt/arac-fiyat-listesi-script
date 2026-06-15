"""Opel Türkiye fiyat listesi scraper.

Fiyat listeleri fiatlisteleri.opel.com.tr alt alanında, her model için
ayrı bir '/arac/<model-kodu>' sayfası olarak render edilir.

Sayfa yapısı (tespit edilen):
  - Her model sayfasında iki ana tablo:
      1) Fiyat tablosu: kolonlar =
         [Motor / Şanzıman, Donanım,
          Tavsiye Edilen Anahtar Teslim Fiyatı (MY25),
          Kampanyalı Fiyat* (MY25),
          Tavsiye Edilen Anahtar Teslim Fiyatı (MY26),
          Kampanyalı Fiyat* (MY26)]
         Her satır = bir versiyon (motor+donanım). Birçok satırın yalnızca
         MY26 kolonları dolu olur; MY25 kolonları boş bırakılır.
         Aynı hücrede newline ile ayrılmış birden çok fiyat olabilir.
      2) Opsiyonlar tablosu: kolonlar =
         [Opsiyonlar, Donanım Seviyesi, Tavsiye Edilen Opsiyon Fiyatı**]
         Bir hücrede birden çok opsiyon adı/fiyatı yeni satırla ayrılır.
  - Model adı <h1> içinde yer alır.
"""
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

import config
from Models.vehicle_price_record import VehiclePriceRecord
from Scrapers.base_scraper import BaseScraper
from Services.logger import get_logger

logger = get_logger(__name__)

# Model kodu -> (gosterilen ad, arac tipi)
# 'tum-araclar' sayfasindaki siralama ve kategorizasyona gore Manuel dolduruldu.
OPEL_MODELS: Dict[str, Tuple[str, str]] = {
    # Binek
    "corsa": ("Corsa", "Binek"),
    "corsa-e": ("Corsa Elektrik", "Binek"),
    "yeni-frontera-hybrid": ("Frontera", "Binek"),
    "frontera-elektrik": ("Frontera Elektrik", "Binek"),
    "yeni-mokka": ("Mokka", "Binek"),
    "mokka-elektrik": ("Mokka GSE", "Binek"),
    "astra": ("Yeni Astra", "Binek"),
    "yeni-grandland": ("Grandland", "Binek"),
    "yeni-grandland-elektrik": ("Grandland Elektrik", "Binek"),
    # Ticari
    "combo": ("Combo", "Ticari"),
    "combo-cargo": ("Combo Cargo", "Ticari"),
    "combo-elektrik": ("Combo Elektrik", "Ticari"),
    "zafira-life": ("Zafira", "Ticari"),
    "vivaro-cargo": ("Vivaro Cargo", "Ticari"),
    "vivaro-kamyonet": ("Vivaro Kamyonet", "Ticari"),
    "vivaro-city-van": ("Vivaro City Van", "Ticari"),
    "movano": ("Movano", "Ticari"),
    "movano-minibus": ("Movano Minibüs", "Ticari"),
}

BASE_ARAC_URL = "https://fiyatlisteleri.opel.com.tr/arac/{code}"

# Fiyat hücresi tespiti için alt sınır (TL)
PRICE_MIN_NUMERIC = 1000


class OpelScraper(BaseScraper):
    """Opel binek + ticari modellerinin fiyat listelerini kazar."""

    @property
    def brand_name(self) -> str:
        return "Opel"

    def scrape(self) -> List[VehiclePriceRecord]:
        all_records: List[VehiclePriceRecord] = []
        for code, (display_name, arac_tipi) in OPEL_MODELS.items():
            url = BASE_ARAC_URL.format(code=code)
            records, err = self.safe_run(
                logger, f"Opel {display_name}", self._scrape_model, url, display_name, arac_tipi
            )
            if err:
                continue
            if records:
                all_records.extend(records)
        return all_records

    # ------------------------------------------------------------------ #
    # Sayfa yükleme
    # ------------------------------------------------------------------ #

    def _scrape_model(
        self, url: str, model_name: str, arac_tipi: str
    ) -> List[VehiclePriceRecord]:
        """Tek bir modelin fiyat sayfasını yükle ve kazar."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.HEADLESS)
            page = browser.new_page()
            try:
                logger.info(f"[Opel {model_name}] sayfa yükleniyor: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)

                # Çerez banner'ını kapat (JS engellemesin)
                self._dismiss_cookie_banner(page)

                if not self._wait_for_price_table(page):
                    logger.warning(f"[Opel {model_name}] fiyat tablosu bulunamadı")
                    return []

                html = page.content()
                records = self._parse_html(html, url, model_name, arac_tipi)
                logger.info(f"[Opel {model_name}] {len(records)} kayıt çıkarıldı")
                return records
            finally:
                browser.close()

    def _dismiss_cookie_banner(self, page: Page) -> None:
        """Opel çerez banner'ını kapatmaya çalışır (aynı sayfada kalmak için)."""
        for sel in [
            "#_psaihm_id_accept_all_btn",
            "a:has-text('HEPSİNİ KABUL ET')",
            "button:has-text('HEPSİNİ KABUL ET')",
            "a:has-text('KABUL ET')",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1000)
                    return
            except Exception:
                continue

    def _wait_for_price_table(self, page: Page) -> bool:
        """Fiyat tablosunun render olmasını bekler."""
        try:
            page.wait_for_selector("table", timeout=15000)
            page.wait_for_timeout(2500)
            return True
        except PlaywrightTimeout:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # HTML parse
    # ------------------------------------------------------------------ #

    def _parse_html(
        self, html: str, url: str, model_name: str, arac_tipi: str
    ) -> List[VehiclePriceRecord]:
        """Model sayfasındaki fiyat ve opsiyon tablolarını işler."""
        soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []

        records: List[VehiclePriceRecord] = []

        # Fiyat tablosunu bul: "Motor" ve "Donanım" içeren tablo
        price_table = self._find_price_table(tables)
        # Opsiyon tablosunu bul: başlığında "Opsiyon" geçen tablo
        option_table = self._find_option_table(tables)

        # Versiyon -> (tavsiye_fiyati, donanim) haritası; opsiyon eşleştirme için
        version_map: List[Tuple[str, str, str]] = []  # (versiyon, tavsiye_fiyati, donanim)

        if price_table is not None:
            version_map = self._parse_price_table(
                price_table, model_name, arac_tipi, url, records
            )

        if option_table is not None:
            self._parse_option_table(
                option_table, version_map, model_name, arac_tipi, url, records
            )

        return records

    def _find_price_table(self, tables: List[Tag]) -> Optional[Tag]:
        """Başlık satırı 'Motor' ve 'Donanım' içeren tabloyu döndür."""
        for t in tables:
            header_text = " ".join(
                c.get_text(" ", strip=True)
                for c in t.find_all(["th"])
            ).lower()
            if "motor" in header_text and "donanım" in header_text:
                return t
        return None

    def _find_option_table(self, tables: List[Tag]) -> Optional[Tag]:
        """Başlık satırı 'Opsiyon' içeren (ama 'Motor' içermeyen) tablo."""
        for t in tables:
            header_text = " ".join(
                c.get_text(" ", strip=True)
                for c in t.find_all(["th"])
            ).lower()
            if "opsiyon" in header_text and "motor" not in header_text:
                return t
        return None

    # ------------------------------------------------------------------ #
    # Fiyat tablosu parse
    # ------------------------------------------------------------------ #

    def _parse_price_table(
        self,
        table: Tag,
        model: str,
        arac_tipi: str,
        url: str,
        records: List[VehiclePriceRecord],
    ) -> List[Tuple[str, str, str]]:
        """Fiyat tablosunu parse eder.

        En yeni model yılının (MY26 > MY25) tavsiye edilen ve kampanyalı
        fiyatlarını çıkarır. Her tavsiye fiyatı için ayrı bir satır oluşturur;
        varsa kampanyalı fiyat eşleştirilir.

        Dönüş: [(versiyon, tavsiye_fiyati, donanim)] — opsiyon eşleştirme için.
        """
        rows = table.find_all("tr")
        if not rows:
            return []

        # Başlık satırından kolon meta verisini çıkar
        header_row = rows[0]
        headers = [c.get_text(" ", strip=True) for c in header_row.find_all(["th", "td"])]
        # (yil, fiyat_turu) — ilk iki kolon (motor, donanim) None kalır
        col_meta: List[Optional[Tuple[Optional[int], str]]] = self._classify_columns(headers)

        # En iyi yıl önceliğini bul (MY26 > MY25 > yılsız)
        target_year = self._pick_target_year(col_meta)

        version_map: List[Tuple[str, str, str]] = []

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            # İlk iki kolon (motor/sanzıman + donanım) tek satırlık metin
            motor = cells[0].get_text(" ", strip=True)
            donanim = cells[1].get_text(" ", strip=True)
            if not motor:
                continue

            versiyon = f"{motor} / {donanim}" if donanim else motor

            # Fiyat kolonlarından tavsiye ve kampanyalı listelerini çıkar
            tavsiye_list, kampanyali_list = self._extract_row_prices(
                cells, col_meta, target_year
            )

            if not tavsiye_list:
                # Fiyatsız satır (örn. gelmeyen model yılı) — atla
                continue

            # Her tavsiye fiyatı için bir satır; kampanyalı varsa eşleştir
            for i, tavsiye in enumerate(tavsiye_list):
                kampanyali = kampanyali_list[i] if i < len(kampanyali_list) else None
                version_map.append((versiyon, tavsiye, donanim))
                records.append(VehiclePriceRecord(
                    marka=self.brand_name,
                    model=model,
                    versiyon=versiyon,
                    model_yili=target_year,
                    arac_tipi=arac_tipi,
                    tavsiye_edilen_fiyat=VehiclePriceRecord.format_price(tavsiye),
                    kampanyali_fiyat=VehiclePriceRecord.format_price(kampanyali),
                    para_birimi="TL",
                    kaynak_url=url,
                ))

        return version_map

    @staticmethod
    def _classify_columns(headers: List[str]) -> List[Optional[Tuple[Optional[int], str]]]:
        """Her kolon için (yil, fiyat_turu) belirle.

        İlk iki kolon (Motor, Donanım) None döner. Fiyat kolonları için:
          - 'tavsiye' içeren + 'my26' -> (2026, 'tavsiye')
          - 'kampanya' içeren + 'my26' -> (2026, 'kampanyali')
          - yıl yoksa (None, 'tavsiye'|'kampanyali')

        'tavsiye' ve 'kampanya' aynı başlıkta yoksa None.
        """
        meta: List[Optional[Tuple[Optional[int], str]]] = []
        for h in headers:
            lower = h.lower()
            # Yıl tespiti
            year: Optional[int] = None
            for prefix, full in [("my2026", 2026), ("my2025", 2025),
                                  ("my26", 2026), ("my25", 2025)]:
                if prefix in lower:
                    year = full
                    break
            # Tür tespiti
            if "kampanya" in lower:
                tur = "kampanyali"
            elif "tavsiye" in lower:
                tur = "tavsiye"
            else:
                # Fiyat kolonu değil
                meta.append(None)
                continue
            meta.append((year, tur))
        return meta

    @staticmethod
    def _pick_target_year(
        col_meta: List[Optional[Tuple[Optional[int], str]]]
    ) -> Optional[int]:
        """En iyi yıl önceliğini döndür: MY26 > MY25 > None."""
        has_2026 = any(m and m[0] == 2026 for m in col_meta)
        if has_2026:
            return 2026
        has_2025 = any(m and m[0] == 2025 for m in col_meta)
        if has_2025:
            return 2025
        return None

    @staticmethod
    def _extract_row_prices(
        cells: List[Tag],
        col_meta: List[Optional[Tuple[Optional[int], str]]],
        target_year: Optional[int],
    ) -> Tuple[List[str], List[str]]:
        """Bir satırdaki tavsiye ve kampanyalı fiyat listelerini döndürür.

        Fiyat kolonları (cells[2:] ile col_meta[2:] hizalıdır). Yalnızca
        target_year'a uyan kolonlardaki fiyatları toplar. Bir hücrede
        newline ile ayrılmış birden çok fiyat olabilir.

        Dönüş: (tavsiye_fiyatlar, kampanyali_fiyatlar) — ham fiyat string'leri.
        """
        tavsiye_list: List[str] = []
        kampanyali_list: List[str] = []
        # cells[0] ve cells[1] motor/donanim; fiyat kolonları 2'den başlar
        for i in range(2, len(cells)):
            if i >= len(col_meta):
                break
            m = col_meta[i]
            if m is None:
                continue
            year, tur = m
            if year != target_year:
                continue
            cell_text = cells[i].get_text("\n", strip=True)
            prices = OpelScraper._extract_prices(cell_text)
            if tur == "tavsiye":
                tavsiye_list.extend(prices)
            elif tur == "kampanyali":
                kampanyali_list.extend(prices)
        return tavsiye_list, kampanyali_list

    # ------------------------------------------------------------------ #
    # Opsiyon tablosu parse
    # ------------------------------------------------------------------ #

    def _parse_option_table(
        self,
        table: Tag,
        version_map: List[Tuple[str, str, str]],
        model: str,
        arac_tipi: str,
        url: str,
        records: List[VehiclePriceRecord],
    ) -> None:
        """Opsiyon tablosunu parse eder ve uygun versiyonlara bağlar.

        Opsiyon tablosu yapısı:
          [Opsiyonlar, Donanım Seviyesi, Tavsiye Edilen Opsiyon Fiyatı**]
        Bir hücrede birden çok opsiyon adı/fiyatı olabilir (newline ile ayrılır).
        """
        rows = table.find_all("tr")
        if len(rows) < 2:
            return

        for row in rows[1:]:  # başlık atla
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            option_names_raw = cells[0].get_text("\n", strip=True)
            donanim_seviyesi = cells[1].get_text(" ", strip=True)
            option_prices_raw = cells[2].get_text("\n", strip=True)

            option_names = [n.strip() for n in option_names_raw.split("\n") if n.strip()]
            option_prices = [p.strip() for p in option_prices_raw.split("\n") if p.strip()]

            # Opsiyon adı ve fiyat sayıları eşleşmezse, tek tek eşleştirmeyi dene
            for i, name in enumerate(option_names):
                price = option_prices[i] if i < len(option_prices) else None
                if not price:
                    # İlk fiyatı kullan
                    price = option_prices[0] if option_prices else None
                if not price:
                    continue

                # Bu opsiyonu "Donanım Seviyesi"ne uyan versiyonlara bağla
                matched_versions = self._match_versions_by_donanim(
                    donanim_seviyesi, version_map
                )
                if not matched_versions:
                    # Tüm versiyonlara bağla ("Tüm Donanımlar" durumu)
                    if "tüm" in donanim_seviyesi.lower():
                        matched_versions = version_map
                if not matched_versions:
                    continue

                for versiyon, tavsiye_fiyati, _ in matched_versions:
                    records.append(VehiclePriceRecord(
                        marka=self.brand_name,
                        model=model,
                        versiyon=versiyon,
                        model_yili=None,
                        arac_tipi=arac_tipi,
                        tavsiye_edilen_fiyat=VehiclePriceRecord.format_price(tavsiye_fiyati),
                        para_birimi="TL",
                        opsiyon_adi=name,
                        opsiyon_fiyati=VehiclePriceRecord.format_price(price),
                        kaynak_url=url,
                    ))

    @staticmethod
    def _match_versions_by_donanim(
        donanim_seviyesi: str, version_map: List[Tuple[str, str, str]]
    ) -> List[Tuple[str, str, str]]:
        """Donanım seviyesi metnine göre eşleşen versiyonları döndür.

        'Tüm Donanımlar' -> tüm versiyonlar.
        'GS' -> yalnızca GS donanımı olanlar.
        """
        if not donanim_seviyesi:
            return []
        seviye = donanim_seviyesi.strip()
        if "tüm" in seviye.lower():
            return list(version_map)
        # Tam eşleşme veya içerme
        matches = []
        for versiyon, fiyat, donanim in version_map:
            if not donanim:
                continue
            if seviye.lower() == donanim.strip().lower():
                matches.append((versiyon, fiyat, donanim))
        if matches:
            return matches
        # Kısmi eşleşme (örn. "GS Line" hem "GS" hem "GS Line" donanımlarına)
        for versiyon, fiyat, donanim in version_map:
            if not donanim:
                continue
            if seviye.lower() in donanim.strip().lower():
                matches.append((versiyon, fiyat, donanim))
        return matches

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_prices(cell_text: str) -> List[str]:
        """Hücre metnindeki TÜM fiyat değerlerini ayrı listele.

        Hücre içinde birden fazla fiyat olabilir (örn. '2.221.000 TL\\n2.342.000 TL').
        Bunları newline'a göre bölerek tek tek döndürür.

        Kabul edilen tekil fiyat biçimleri:
          '1.535.000 TL', '₺1.535.000', '1535000', '1.535.000'

        Dönüş: geçerli fiyat string'lerinin listesi (boş olabilir).
        """
        if not cell_text:
            return []
        # Newline ile ayrılmış parçaları al; tek hücrede çoklu fiyat desteği
        parts = [p.strip() for p in cell_text.split("\n")]
        results: List[str] = []
        for part in parts:
            if not part:
                continue
            if "₺" in part or "tl" in part.lower():
                results.append(part)
                continue
            # Saf sayısal değer mi (1.000.000+)
            cleaned = part.replace(".", "").replace(",", "").replace(" ", "").lower()
            cleaned = cleaned.replace("tl", "").replace("₺", "")
            if cleaned.isdigit() and int(cleaned) >= PRICE_MIN_NUMERIC:
                results.append(part)
        return results

    @staticmethod
    def _extract_price(cell_text: str) -> Optional[str]:
        """Hücre metnindeki ilk fiyat değerini döndür (geriye dönük uyumluluk).

        Birden fazla fiyat varsa yalnızca ilkini verir. Tek fiyat gerektiren
        yerlerde (örn. opsiyon fiyatları) kullanılır.
        """
        prices = OpelScraper._extract_prices(cell_text)
        return prices[0] if prices else None
