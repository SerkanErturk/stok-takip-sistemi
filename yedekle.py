"""
yedekle.py — Veritabanının tarihli kopyasını alır.

    python yedekle.py                    yedekler/ klasörüne
    python yedekle.py D:\\yedek           başka bir klasöre
    python yedekle.py --liste            mevcut yedekleri gösterir
    python yedekle.py --geri 2026-07-31  yedekten geri döner

NEDEN GEREKLİ
    Tüm veri tek bir stok.db dosyasında. O dosya bozulursa ürünler,
    belgeler, kullanıcılar — hepsi gider. Yedeği olmayan tek dosya,
    tek arıza noktasıdır.

NEDEN DOSYAYI KOPYALAMAK YETMEZ
    Uygulama çalışırken stok.db'yi düz kopyalarsan (Ctrl+C / Ctrl+V)
    yarım yazılmış bir işlemin ortasını yakalayabilirsin. Ortaya
    açılmayan veya tutarsız bir dosya çıkar.

    SQLite'ın kendi yedekleme API'si var: conn.backup(). Veritabanını
    tutarlı bir anda, çalışırken bile güvenle kopyalar. Bu script onu
    kullanıyor.

    WAL modunda ayrıca stok.db-wal ve stok.db-shm dosyaları oluşur;
    backup() bunları da hesaba katar, elle kopyalama katmaz.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from db import DB_PATH

# Kaç yedek saklansın. Fazlası en eskiden başlayarak silinir.
# Sınırsız yedek diski doldurur; hiç silmemek de bir seçim değil.
SAKLANACAK_ADET = 30

VARSAYILAN_KLASOR = Path(__file__).parent / "yedekler"


def yedek_al(hedef_klasor=None):
    """Tutarlı bir yedek alır, yolunu döndürür."""
    if not DB_PATH.exists():
        raise SystemExit(f"Veritabanı bulunamadı: {DB_PATH}")

    klasor = Path(hedef_klasor) if hedef_klasor else VARSAYILAN_KLASOR
    # parents=True: ara klasörler de oluşsun
    # exist_ok=True: klasör zaten varsa hata verme
    klasor.mkdir(parents=True, exist_ok=True)

    # Dosya adı sıralanabilir olsun diye yıl-ay-gün-saat sırasında.
    # "31-07-2026" yazsaydık alfabetik sıralama kronolojik olmazdı.
    damga = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    hedef = klasor / f"stok_{damga}.db"

    # Aynı saniyede iki yedek alınırsa adlar çakışır ve ikincisi
    # birincinin ÜZERİNE yazar. Bu, geri yükleme sırasında veri
    # kaybettirdi: emniyet yedeği, geri yüklenecek dosyayı ezdi.
    # Çakışma varsa sona sayaç ekliyoruz.
    sayac = 2
    while hedef.exists():
        hedef = klasor / f"stok_{damga}_{sayac}.db"
        sayac += 1

    kaynak = sqlite3.connect(DB_PATH)
    yedek = sqlite3.connect(hedef)
    try:
        # backup(): SQLite'ın kendi yedekleme mekanizması.
        # Veritabanı kullanımdayken bile tutarlı bir kopya üretir.
        kaynak.backup(yedek)
    finally:
        yedek.close()
        kaynak.close()

    return hedef


def eskileri_temizle(klasor=None, saklanacak=SAKLANACAK_ADET):
    """En yeni N yedeği bırakır, gerisini siler."""
    klasor = Path(klasor) if klasor else VARSAYILAN_KLASOR
    if not klasor.exists():
        return []

    # sorted() dosya adına göre sıralıyor; adlar tarih sırasında
    # olduğu için sonuç kronolojik. reverse=True -> en yeni başta.
    yedekler = sorted(klasor.glob("stok_*.db"), reverse=True)

    silinenler = []
    for eski in yedekler[saklanacak:]:
        eski.unlink()
        silinenler.append(eski.name)
    return silinenler


def listele(klasor=None):
    klasor = Path(klasor) if klasor else VARSAYILAN_KLASOR
    if not klasor.exists():
        print(f"Yedek klasörü yok: {klasor}")
        return []

    yedekler = sorted(klasor.glob("stok_*.db"), reverse=True)
    if not yedekler:
        print("Henüz yedek alınmamış.")
        return []

    print(f"{len(yedekler)} yedek ({klasor}):\n")
    for y in yedekler:
        boyut = y.stat().st_size / 1024
        tarih = datetime.fromtimestamp(y.stat().st_mtime)
        print(f"  {y.name:32} {boyut:>8.0f} KB   {tarih:%d.%m.%Y %H:%M}")
    return yedekler


def geri_yukle(desen, klasor=None):
    """Yedekten geri döner. Mevcut veritabanını önce yedekler."""
    klasor = Path(klasor) if klasor else VARSAYILAN_KLASOR
    adaylar = sorted(klasor.glob(f"stok_*{desen}*.db"), reverse=True)

    if not adaylar:
        raise SystemExit(f"'{desen}' ile eşleşen yedek yok. "
                         f"Listelemek için: python yedekle.py --liste")

    kaynak = adaylar[0]
    if len(adaylar) > 1:
        print(f"{len(adaylar)} eşleşme var, en yenisi kullanılacak: {kaynak.name}\n")

    # Geri yüklemeden önce mevcut hali yedekle. Yanlış yedeği seçtiysen
    # veya geri dönmek istersen elinde kalsın. Geri yükleme de bir
    # veri kaybı riskidir.
    if DB_PATH.exists():
        oncesi = yedek_al(klasor)
        print(f"Mevcut veritabanı önce yedeklendi: {oncesi.name}")

    hedef = sqlite3.connect(DB_PATH)
    yedek = sqlite3.connect(kaynak)
    try:
        yedek.backup(hedef)
    finally:
        yedek.close()
        hedef.close()

    print(f"Geri yüklendi: {kaynak.name}")


def main():
    argumanlar = sys.argv[1:]

    if "--liste" in argumanlar:
        listele()
        return

    if "--geri" in argumanlar:
        i = argumanlar.index("--geri")
        if i + 1 >= len(argumanlar):
            raise SystemExit("Kullanım: python yedekle.py --geri 2026-07-31")
        geri_yukle(argumanlar[i + 1])
        return

    klasor = argumanlar[0] if argumanlar and not argumanlar[0].startswith("--") else None

    hedef = yedek_al(klasor)
    boyut = hedef.stat().st_size / 1024
    print(f"Yedek alındı: {hedef}  ({boyut:.0f} KB)")

    silinen = eskileri_temizle(klasor)
    if silinen:
        print(f"{len(silinen)} eski yedek silindi (en son {SAKLANACAK_ADET} tanesi tutuluyor).")


if __name__ == "__main__":
    main()
