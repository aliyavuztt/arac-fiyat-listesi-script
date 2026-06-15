"""Tüm marka scraper'ları için temel sözleşme."""
from abc import ABC, abstractmethod
from typing import Callable, List, Tuple, TypeVar

from Models.vehicle_price_record import VehiclePriceRecord

T = TypeVar("T")


class BaseScraper(ABC):
    """Yeni bir marka eklemek için bu sınıftan miras alın.

    Sözleşme:
      - `brand_name`: Marka adı (örn. "Renault").
      - `scrape()`: Bu markaya ait tüm fiyat listesi sayfalarını kazır ve
        VehiclePriceRecord listesi döndürür. Hiçbir zaman exception fırlatmaz;
        sayfa bazlı hataları kendi içinde yakalar. Tam başarısızlıkta boş liste
        döndürebilir.
    """

    @property
    @abstractmethod
    def brand_name(self) -> str:
        """Marka adı (Excel 'Marka' kolonu ve loglar için)."""
        ...

    @abstractmethod
    def scrape(self) -> List[VehiclePriceRecord]:
        """Markaya ait tüm kayıtları döndür."""
        ...

    @staticmethod
    def safe_run(
        logger, action: str, fn: Callable[..., T], *args, **kwargs
    ) -> Tuple[T, Exception]:
        """fn() çalıştırır; başarısızlıkta hatayı loglar ve döner.

        Dönüş: (sonuç, hata). Başarıda hata None olur.
        """
        try:
            return fn(*args, **kwargs), None
        except Exception as e:  # noqa: BLE001 - scraper hatası diğerlerini engellememeli
            logger.error(f"[{action}] başarısız: {e}")
            return None, e
