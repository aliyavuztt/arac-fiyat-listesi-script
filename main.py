"""Araç Fiyat Listesi Toplama Uygulaması — Giriş noktası.

Kullanım:
    python main.py
"""
import importlib
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

import config
from Models.vehicle_price_record import VehiclePriceRecord
from Scrapers.base_scraper import BaseScraper
from Services.excel_export_service import ExcelExportService
from Services.logger import get_logger

logger = get_logger("main")


def output_filename(base: str) -> str:
    """Çıktı dosya adını config.USE_TIMESTAMP_IN_FILENAME'e göre oluşturur.

    base 'Renault' ise:
      - True  -> 'Renault_2026-06-15_2129.xlsx'
      - False -> 'Renault.xlsx'
    """
    if config.USE_TIMESTAMP_IN_FILENAME:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        return f"{base}_{stamp}.xlsx"
    return f"{base}.xlsx"


def load_scrapers() -> List[BaseScraper]:
    """config.BRAND_SCRAPERS içindeki scraper'ları örnekler."""
    scrapers: List[BaseScraper] = []
    for dotted_path in config.BRAND_SCRAPERS:
        module_path, class_name = dotted_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            scrapers.append(cls())
            logger.info(f"Scraper yüklendi: {class_name}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Scraper yüklenemedi ({dotted_path}): {e}")
    return scrapers


def run_brand(scraper: BaseScraper) -> Tuple[str, List[VehiclePriceRecord]]:
    """Tek bir scraper'ı güvenli çalıştırır. Hiçbir zaman exception fırlatmaz."""
    name = scraper.brand_name
    try:
        records = scraper.scrape()
        logger.info(f"[{name}] tamamlandı — {len(records)} kayıt")
        return name, records
    except Exception as e:  # noqa: BLE001 - scraper sözleşmeyi ihlal etse bile devam et
        logger.error(f"[{name}] KRİTİK scraper hatası: {e}")
        return name, []


def print_summary(
    brand_results: Dict[str, int],
    total: int,
    elapsed: float,
    output_dir: str,
):
    """Konsola çalıştırma özeti yazdırır."""
    print("\n" + "=" * 60)
    print("  SCRAPING TAMAMLANDI")
    print("=" * 60)
    for brand, count in brand_results.items():
        status = "OK" if count > 0 else "BAŞARISIZ/BOŞ"
        print(f"  {brand:<20s} {count:>6d} kayıt   [{status}]")
    print("-" * 60)
    print(f"  {'TOPLAM':<20s} {total:>6d} kayıt")
    print(f"  Süre:     {elapsed:.1f} sn")
    print(f"  Çıktı:    {output_dir}")
    print("=" * 60)


def main():
    start = time.time()
    logger.info("=== Araç Fiyat Listesi Toplama Uygulaması ===")

    scrapers = load_scrapers()
    if not scrapers:
        logger.error("Hiç scraper yüklenemedi. Çıkılıyor.")
        sys.exit(1)

    # Tüm markaları kazı (bir markanın hatası diğerini engellemez)
    all_records: List[VehiclePriceRecord] = []
    brand_results: Dict[str, int] = {}

    for scraper in scrapers:
        name, records = run_brand(scraper)
        # Aynı isimde birden fazla scraper olursa toplamını biriktir
        brand_results[name] = brand_results.get(name, 0) + len(records)
        all_records.extend(records)

    # Excel çıktıları
    excel_svc = ExcelExportService(config.DOCS_DIR)

    # Marka bazlı dosyalar
    by_brand: Dict[str, List[VehiclePriceRecord]] = defaultdict(list)
    for rec in all_records:
        by_brand[rec.marka].append(rec)

    for brand, recs in by_brand.items():
        try:
            path = excel_svc.export(output_filename(brand), recs)
            logger.info(f"{path} yazıldı ({len(recs)} satır)")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[{brand}] Excel yazımı başarısız: {e}")

    # Birleşik dosya
    if all_records:
        try:
            combined_path = excel_svc.export(output_filename("AllBrands"), all_records)
            logger.info(f"{combined_path} yazıldı ({len(all_records)} satır)")
        except Exception as e:  # noqa: BLE001
            logger.error(f"AllBrands Excel yazımı başarısız: {e}")
    else:
        logger.warning("Hiç kayıt kazılamadı — AllBrands.xlsx yazılmadı.")

    elapsed = time.time() - start
    total = len(all_records)
    print_summary(brand_results, total, elapsed, config.DOCS_DIR)


if __name__ == "__main__":
    main()
