# Araç Fiyat Listesi Toplama Uygulaması

Türkiye'deki otomobil markalarının resmi web sitelerinden güncel fiyat
listelerini çeker ve Excel dosyaları olarak dışa aktarır.

## Gereksinimler (sıfırdan kurulum)

Yeni bir PC'de projeyi çalıştırmak için önce şu programları kur:

### 1. Python 3.10+ (ZORUNLU)

- **https://www.python.org/downloads/** adresinden Python 3.10 veya daha yeni
  bir sürümü indir.
- Kurulumda **"Add Python to PATH"** kutusunu MUTLAKA işaretle. Bu çok önemli;
  işaretlemezsen `python` komutu çalışmaz.
- Kurulumdan sonra komut satırında (CMD veya PowerShell) doğrula:
  ```bash
  python --version
  ```
  `Python 3.10.x` (veya üzeri) görmelisin.

### 2. Git (ZORUNLU — kodu GitHub'dan çekmek için)

- **https://git-scm.com/download/win** adresinden indir ve kur.
- Varsayılan ayarlarla kur; hiçbir seçeneği değiştirmene gerek yok.
- Doğrula:
  ```bash
  git --version
  ```

### 3. Playwright Tarayıcısı (ZORUNLU — scraper bununla sayfaları açar)

Renault ve Opel siteleri JavaScript ile fiyat tablolarını render ediyor;
bu yüzden gerçek bir tarayıcı (Chromium) kullanıyoruz. Playwright bunu
otomatik yönetir. Kurulumu aşağıdaki "Kurulum" bölümünde.

> **Not:** Visual Studio Code kullanıyorsan Python eklentisini kurman
> önerilir ama zorunlu değil. Komut satırından da çalıştırabilirsin.

---

## Kurulum (GitHub'dan çekip çalıştırma)

Komut satırını aç (CMD, PowerShell veya Git Bash) ve şu adımları sırayla izle:

### Adım 1 — Projeyi GitHub'dan çek

İstediğin klasöre git (örn. Masaüstü):
```bash
cd "%USERPROFILE%\Desktop"
```

Projeyi klonla:
```bash
git clone https://github.com/aliyavuztt/arac-fiyat-listesi-script.git
```

Proje klasörüne gir:
```bash
cd arac-fiyat-listesi-script
```

### Adım 2 — Sanal ortam oluştur (ÖNERİLİR)

Bu adım ZORUNLU değildir ama önerilir. Sistem genelindeki Python paketlerini
kirletmemek için projeye özel bir ortam oluşturur:

```bash
python -m venv .venv
```

Sanal ortamı aktif et:
- **CMD:** `.venv\Scripts\activate.bat`
- **PowerShell:** `.venv\Scripts\Activate.ps1`
- **Git Bash:** `source .venv/Scripts/activate`

Aktif olunca komut satırının başında `(.venv)` yazar. Bundan sonraki tüm
`pip` komutları yalnızca bu projeye kurulur.

> **PowerShell "execution policy" hatası alırsan:** PowerShell'i yönetici
> olarak açıp `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` çalıştır.

### Adım 3 — Python bağımlılıklarını kur

```bash
pip install -r requirements.txt
```

Bu komut `playwright`, `openpyxl`, `beautifulsoup4`, `lxml` paketlerini kurar.

### Adım 4 — Playwright tarayıcısını kur (Chromium)

```bash
python -m playwright install chromium
```

Bu komut Chromium tarayıcısını indirir (~150 MB). Playwright bunu başlıksız
(headless) modda kullanır. İlk kurulumda biraz sürer; sabırla bekle.

### Adım 5 — Doğrula (her şey hazır mı?)

```bash
python -c "import playwright, openpyxl, bs4, lxml; print('OK')"
```

`OK` yazıyorsa hazır.

## Çalıştırma

```bash
python main.py
```

Çıktılar `docs/` klasöründe oluşur:

- `docs/Renault_2026-06-15_2129.xlsx` — yalnızca Renault modelleri
- `docs/Opel_2026-06-15_2129.xlsx` — yalnızca Opel modelleri
- `docs/AllBrands_2026-06-15_2129.xlsx` — tüm markaların birleşik listesi

Dosya adındaki tarih-saat, çalıştırma anını gösterir. Her çalıştırmada
yeni dosya oluşur; böylece geçmiş fiyat listeleri birikir.

Tarih-saat eklemek istemiyorsan `config.py` içinde
`USE_TIMESTAMP_IN_FILENAME = False` yap; bu durumda dosya adları sabit
olur (`Renault.xlsx`, `AllBrands.xlsx`) ve her çalıştırmada üzerine yazılır.

## Excel Kolonları

| Marka | Model | Versiyon | ModelYili | AracTipi | TavsiyeEdilenFiyat | KampanyaliFiyat | ParaBirimi | OpsiyonAdi | OpsiyonFiyati | KaynakUrl | VeriTarihi |

- **TavsiyeEdilenFiyat:** Anahtar teslim liste fiyatı (her zaman dolu).
  Fiyat gerçek sayıdır (örn. `2221000`); Excel binlik ayırıcı ile görüntüler
  (`2.221.000`). SUM/Alt+Toplam çalışır.
- **KampanyaliFiyat:** Varsa kampanyalı fiyat, yoksa boş. Yalnızca Opel'de
  kullanılır; Renault'da bu kolon her zaman boştur. Gerçek sayı.
- **ParaBirimi:** Renault için `₺`, Opel için `TL`. Fiyat kolonlarında sembol
  olmadığı için para birimi buradan okunur.
- Her opsiyon ayrı bir satır olarak yazılır. Opsiyonu olmayan araçlar tek
  satır olarak kaydedilir (OpsiyonAdi/OpsiyonFiyati boş).
- Aynı aracın birden fazla fiyatı (örn. farklı donanım seviyeleri) varsa her
  fiyat ayrı satır olur.
- Aynı aracın farklı model yılları (MY25/MY26) ayrı satır olur; en yeni yıl
  tercih edilir.

## Mimari

```
main.py              # Giriş noktası, orchestrasyon
config.py            # URL'ler, marka kaydı, ayarlar
Models/              # VehiclePriceRecord veri modeli
Scrapers/            # Marka bazlı scraper'lar (BaseScraper sözleşmesi)
Services/            # ExcelExportService, logger
docs/                # Çıktı klasörü (otomatik oluşur)
```

## Yeni Marka Ekleme

1. `Scrapers/<marka>_scraper.py` oluştur ve `BaseScraper`'dan miras al:

   ```python
   from Scrapers.base_scraper import BaseScraper
   from Models.vehicle_price_record import VehiclePriceRecord
   from Services.logger import get_logger

   logger = get_logger(__name__)

   class ToyotaScraper(BaseScraper):
       @property
       def brand_name(self) -> str:
           return "Toyota"

       def scrape(self) -> list[VehiclePriceRecord]:
           # markaya özel kazıma mantığı
           ...
   ```

2. `config.py` içindeki `BRAND_SCRAPERS` listesine ekle:

   ```python
   BRAND_SCRAPERS = [
       "Scrapers.renault_scraper.RenaultScraper",
       "Scrapers.toyota_scraper.ToyotaScraper",   # yeni satır
   ]
   ```

Başka dosyaya dokunmaya gerek yok — hata izolasyonu, Excel çıktısı ve
konsol raporu otomatik gelir.

## Hata Yönetimi

- Bir markanın sayfası erişilemezse uygulama durmaz; hata loglanır.
- Başarısız marka diğer markaların işlenmesini engellemez.
- Eksik veri işlemi kesmez.

## Sorun Giderme

**Hiç kayıt gelmiyorsa:** `config.py` içinde `HEADLESS = False` yapıp
sayfayı görsel olarak inceleyin. Renault tablo yapısını değiştirmiş olabilir.

**Playwright hatası:** `python -m playwright install chromium` komutunu
tekrar çalıştırın.

**Bağımlılık eksik:** `pip install -r requirements.txt` ile yeniden kurun.
