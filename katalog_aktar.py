"""
katalog_aktar.py — Teknorot katalog çıktısını veritabanına aktarır.

    python katalog_aktar.py katalog.xlsx
    python katalog_aktar.py katalog.csv

NASIL DOSYA ELDE EDİLİR
    teknorot.com/katalog adresinde marka / model / ürün grubu filtrele,
    istediğin ürünleri karşılaştırma listesine ekle, listeyi Excel veya
    PDF olarak dışa aktar. Excel dosyasını bu scripte ver.

NE YAPAR
    Parça kodu, ürün adı, kategori, marka, model gibi GERÇEK bilgileri
    dosyadan okur. Stok adedi, fiyat, kritik seviye ve depo lokasyonu
    katalogda yok — bunları gerçekçi aralıklarda üretir.

    Yani ürün bilgisi gerçek, stok bilgisi temsilî olur. Staj sunumunda
    tam olarak söylemen gereken cümle de bu.

xlsx okumak için openpyxl gerekirdi ama kuramıyoruz. Sorun değil:
bir .xlsx dosyası zaten ZIP'lenmiş XML'den ibaret. zipfile ve
xml.etree Python'un içinde geldiği için elle çözüyoruz.
"""

import csv
import random
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from datetime import datetime

from db import get_connection, init_db
from seed import WAREHOUSES, generate_movements

random.seed(42)

# Dosyadaki başlık -> bizim kolon adımız.
# Site farklı dilde veya biraz farklı başlıkla çıktı verirse buraya
# yeni karşılık eklemen yeterli, kodun geri kalanı değişmez.
BASLIK_ESLEME = {
    "teknorot ref": "part_code",
    "teknorot ref no": "part_code",
    "ürün kodu": "part_code",
    "urun kodu": "part_code",
    "ref": "part_code",
    "oem kodu": "oem_code",
    "oem no": "oem_code",
    "oem": "oem_code",
    "ürün grubu": "category",
    "urun grubu": "category",
    "grup": "category",
    "marka": "vehicle_make",
    "model": "vehicle_model",
    "pozisyon": "position",
    "ürün adı": "name",
    "urun adi": "name",
}


# ---------------------------------------------------------------------------
# XLSX okuma
# ---------------------------------------------------------------------------

def _sutun_no(hucre_ref):
    """'B7' -> 2. Hücrenin kaçıncı kolonda olduğunu bulur.

    Excel boş hücreleri dosyaya hiç yazmaz. Yani bir satırda A, C, D
    varsa B atlanmıştır. Kolon sırasını saymakla değil, hücrenin kendi
    adresinden okumak zorundayız.
    """
    harfler = "".join(ch for ch in hucre_ref if ch.isalpha())
    numara = 0
    for ch in harfler:
        numara = numara * 26 + (ord(ch.upper()) - 64)
    return numara


def xlsx_oku(yol):
    """Bir .xlsx dosyasının ilk sayfasını satır listesi olarak döndürür."""
    with zipfile.ZipFile(yol) as arsiv:
        # Excel metinleri tekrar etmesin diye ortak bir havuzda tutar.
        # Hücre "3 numaralı metin" der, gerçek metin bu dosyadadır.
        paylasilan = []
        if "xl/sharedStrings.xml" in arsiv.namelist():
            kok = ElementTree.fromstring(arsiv.read("xl/sharedStrings.xml"))
            for si in kok.findall("{*}si"):
                # Zengin metinde parçalar ayrı <t> etiketlerinde durur,
                # hepsini birleştiriyoruz.
                paylasilan.append("".join(t.text or "" for t in si.findall(".//{*}t")))

        sayfa = ElementTree.fromstring(arsiv.read("xl/worksheets/sheet1.xml"))

        satirlar = []
        for row in sayfa.findall(".//{*}row"):
            hucreler = {}
            for c in row.findall("{*}c"):
                ref = c.get("r", "")
                tip = c.get("t")

                if tip == "inlineStr":
                    deger = "".join(t.text or "" for t in c.findall(".//{*}t"))
                else:
                    v = c.find("{*}v")
                    if v is None or v.text is None:
                        continue
                    if tip == "s":
                        # Paylaşılan metin havuzundan numarayla çek
                        deger = paylasilan[int(v.text)]
                    else:
                        deger = v.text

                hucreler[_sutun_no(ref)] = deger.strip()

            if hucreler:
                en_sag = max(hucreler)
                # Boş hücreleri "" ile doldurup düz listeye çevir
                satirlar.append([hucreler.get(i, "") for i in range(1, en_sag + 1)])

        return satirlar


def csv_oku(yol):
    """CSV dosyasını satır listesi olarak döndürür."""
    # utf-8-sig: Excel'in dosya başına koyduğu görünmez işareti yutar.
    # Olmazsa ilk başlık "ï»¿Teknorot Ref" diye okunur ve eşleşmez.
    with open(yol, encoding="utf-8-sig", newline="") as f:
        ornek = f.read(2048)
        f.seek(0)
        # Ayraç noktalı virgül mü virgül mü? Sniffer tahmin etsin.
        try:
            lehce = csv.Sniffer().sniff(ornek, delimiters=";,\t")
        except csv.Error:
            lehce = csv.excel
            lehce.delimiter = ";"
        return [[h.strip() for h in satir] for satir in csv.reader(f, lehce)]


# ---------------------------------------------------------------------------
# Ayrıştırma
# ---------------------------------------------------------------------------

def urunleri_cikar(satirlar):
    """Ham satırlardan ürün sözlükleri üretir."""
    if not satirlar:
        raise SystemExit("Dosya boş görünüyor.")

    # Başlık satırı ilk satır olmayabilir — site çıktısında üstte
    # boş satır veya başlık metni olabilir. "Teknorot Ref" geçen ilk
    # satırı başlık kabul ediyoruz.
    baslik_index = None
    for i, satir in enumerate(satirlar[:15]):
        kucuk = [h.lower() for h in satir]
        if any(h in BASLIK_ESLEME for h in kucuk):
            baslik_index = i
            break

    if baslik_index is None:
        raise SystemExit(
            "Başlık satırı bulunamadı. Dosyada 'Teknorot Ref' veya "
            "'OEM Kodu' gibi bir başlık olmalı."
        )

    basliklar = [h.lower() for h in satirlar[baslik_index]]
    # Hangi kolon numarası bizim hangi alanımıza denk geliyor
    eslesme = {i: BASLIK_ESLEME[h] for i, h in enumerate(basliklar)
               if h in BASLIK_ESLEME}

    if "part_code" not in eslesme.values():
        raise SystemExit("Parça kodu kolonu bulunamadı.")

    urunler = {}
    for satir in satirlar[baslik_index + 1:]:
        kayit = {}
        for i, alan in eslesme.items():
            if i < len(satir):
                kayit[alan] = satir[i]

        kod = kayit.get("part_code", "").strip()
        if not kod:
            continue

        # Aynı parça birden fazla model için listelenmiş olabilir.
        # Stok takibinde bir parça = bir kalem, o yüzden ilkini alıyoruz.
        if kod in urunler:
            continue

        # Ürün adı yoksa parçalardan kur
        ad = kayit.get("name", "").strip()
        if not ad:
            parcalar = [kayit.get("category", ""), kayit.get("position", ""),
                        "-", kayit.get("vehicle_make", ""),
                        kayit.get("vehicle_model", "")]
            ad = " ".join(p for p in parcalar if p).strip(" -")

        urunler[kod] = {
            "part_code": kod,
            "oem_code": kayit.get("oem_code", "") or None,
            "name": ad or kod,
            "category": kayit.get("category", "") or "Diğer",
            "vehicle_make": kayit.get("vehicle_make", "") or None,
            "vehicle_model": kayit.get("vehicle_model", "") or None,
            # --- Katalogda olmayan, üretilen alanlar ---
            "unit_price": round(random.uniform(120, 2400), 2),
            "min_stock": random.choice([10, 15, 20, 25, 30, 40, 50]),
            "location": f"Depo-{random.choice(WAREHOUSES)}-Raf-{random.randint(1, 24):02d}",
        }

    return list(urunler.values())


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

def acilis_hareketleri(ids):
    """Her ürün için tek bir 'Açılış stoğu' girişi üretir.

    Neden 6 aylık sahte geçmiş yerine bu?

    Ürün bilgisi katalogdan geliyor, yani gerçek. Hareket geçmişi ise
    tamamen uydurma olurdu — "12 Mart'ta 40 adet girdi" gibi hiç
    yaşanmamış kayıtlar. İkisi aynı ekranda yan yana durunca birini
    gerçek sanan, diğerini de gerçek sanar.

    Açılış stoğu dürüst bir başlangıç noktası: "sistem bugün devreye
    alındı, başlangıç sayımı bu." Sonraki her hareket senin gerçekten
    yaptığın işlem olur ve geçmiş temiz kalır.
    """
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "product_id": pid,
            "type": "IN",
            "quantity": random.randint(5, 260),
            "created_at": simdi,
            "note": "Açılış stoğu",
        }
        for pid in ids
    ]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit("Kullanım: python katalog_aktar.py <dosya.xlsx|dosya.csv>")

    # "--gecmis" yazılırsa 6 aylık demo hareket geçmişi üretilir.
    # Varsayılan: sadece açılış stoğu.
    demo_gecmis = "--gecmis" in sys.argv
    dosya_arg = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not dosya_arg:
        raise SystemExit("Dosya adı belirtilmedi.")

    yol = Path(dosya_arg[0])
    if not yol.exists():
        raise SystemExit(f"Dosya bulunamadı: {yol}")

    if yol.suffix.lower() == ".xlsx":
        satirlar = xlsx_oku(yol)
    elif yol.suffix.lower() in (".csv", ".txt"):
        satirlar = csv_oku(yol)
    else:
        raise SystemExit("Sadece .xlsx ve .csv destekleniyor.")

    urunler = urunleri_cikar(satirlar)
    if not urunler:
        raise SystemExit("Dosyada ürün satırı bulunamadı.")

    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # Sıra önemli: önce hareketler, sonra ürünler (foreign key yüzünden).
    cur.execute("DELETE FROM stock_movements")
    cur.execute("DELETE FROM products")

    cur.executemany("""
        INSERT INTO products
            (part_code, oem_code, name, category, vehicle_make,
             vehicle_model, unit_price, min_stock, location)
        VALUES
            (:part_code, :oem_code, :name, :category, :vehicle_make,
             :vehicle_model, :unit_price, :min_stock, :location)
    """, urunler)

    # Ürünler eklendi, id'leri geri okuyup hareketleri onlara bağlıyoruz.
    ids = [r["id"] for r in cur.execute("SELECT id FROM products")]
    hareketler = generate_movements(ids) if demo_gecmis else acilis_hareketleri(ids)

    cur.executemany("""
        INSERT INTO stock_movements (product_id, type, quantity, created_at, note)
        VALUES (:product_id, :type, :quantity, :created_at, :note)
    """, hareketler)

    conn.commit()

    kategoriler = {u["category"] for u in urunler}
    markalar = {u["vehicle_make"] for u in urunler if u["vehicle_make"]}

    print(f"{len(urunler)} ürün aktarıldı.")
    print(f"{len(kategoriler)} ürün grubu, {len(markalar)} marka.")
    if demo_gecmis:
        print(f"{len(hareketler)} demo stok hareketi üretildi (6 aylık sahte geçmiş).")
    else:
        print(f"{len(hareketler)} açılış stoğu kaydı oluşturuldu.")
    print("\nÜrün bilgileri katalogdan geldi; stok, fiyat ve lokasyon üretildi.")

    conn.close()


if __name__ == "__main__":
    main()
