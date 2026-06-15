"""Araç fiyat kaydı veri modeli."""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class VehiclePriceRecord:
    """Excel çıktısındaki tek bir satır.

    Her opsiyon için ayrı bir kayıt oluşturulur. Opsiyon yoksa araç bilgileri
    tek satır olarak yazılır ve opsiyon alanları boş bırakılır.

    Fiyat alanları:
      - tavsiye_edilen_fiyat: Anahtar teslim liste fiyatı (int, her zaman dolu olmalı)
      - kampanyali_fiyat: Varsa kampanyalı fiyat (int), yoksa None
      - para_birimi: "₺" (Renault) | "TL" (Opel) | None
      - opsiyon_fiyati: Opsiyon varsa fiyatı (int, aynı para birimi)

    Fiyatlar gerçek sayı (int) olarak saklanır; Excel'de SUM/Alt+Toplam
    çalışır. Binlik ayırıcı Excel'in hücre biçimiyle gösterilir.
    """
    marka: str
    model: str
    versiyon: str
    model_yili: Optional[int] = None
    arac_tipi: str = "Binek"
    tavsiye_edilen_fiyat: Optional[int] = None
    kampanyali_fiyat: Optional[int] = None
    para_birimi: Optional[str] = None
    opsiyon_adi: Optional[str] = None
    opsiyon_fiyati: Optional[int] = None
    kaynak_url: str = ""
    veri_tarihi: str = field(default_factory=lambda: date.today().isoformat())

    @staticmethod
    def excel_headers() -> List[str]:
        """Excel sayfasındaki sabit kolon sırası."""
        return [
            "Marka", "Model", "Versiyon", "ModelYili", "AracTipi",
            "TavsiyeEdilenFiyat", "KampanyaliFiyat", "ParaBirimi",
            "OpsiyonAdi", "OpsiyonFiyati", "KaynakUrl", "VeriTarihi",
        ]

    def to_row(self) -> list:
        """Excel satırına çevir (excel_headers sırasıyla)."""
        return [
            self.marka,
            self.model,
            self.versiyon,
            self.model_yili,
            self.arac_tipi,
            self.tavsiye_edilen_fiyat,
            self.kampanyali_fiyat,
            self.para_birimi,
            self.opsiyon_adi,
            self.opsiyon_fiyati,
            self.kaynak_url,
            self.veri_tarihi,
        ]

    @staticmethod
    def format_price(raw: Optional[str]) -> Optional[int]:
        """Ham fiyat metninden saf sayı çıkar: '2.221.000 TL' -> 2221000.

        Excel'de SUM/Alt+Toplam çalışması için fiyatlar gerçek sayı olmalı.
        Binlik ayırıcı Excel'in hücre biçimiyle gösterilir (format '#,##0').

        Kabul edilen girdiler:
          - '2.221.000 TL', '₺1.830.000', '1.535.000', '1535000'
          - '-', 'yok', '' -> None

        Dönüş: int (örn. 2221000) ya da None (geçersiz/boş girdi için).
        """
        if not raw:
            return None
        text = raw.strip()
        if not text:
            return None
        # Boş/işaretsiz değerler
        if text in ("-", "—", "–", "yok", "YOK", "Yok"):
            return None
        # Para birimi sembollerini ve harflerini temizle
        cleaned = text.replace("₺", "").replace("TL", "").replace("tl", "")
        cleaned = cleaned.strip()
        # Sayıyı yakala: 1.535.000  veya  1535000  veya  1,535,000
        digits_and_sep = re.sub(r"[^\d.,]", "", cleaned)
        if not digits_and_sep:
            return None
        # Nokta/virgülü kaldır -> saf rakam
        digits_only = digits_and_sep.replace(".", "").replace(",", "")
        if not digits_only.isdigit():
            return None
        return int(digits_only)
