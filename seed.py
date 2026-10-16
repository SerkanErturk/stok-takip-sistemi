"""
seed.py — Örnek (sahte) veri üretir.

Gerçek şirket verisi olmadığı için kendi veri setimizi üretiyoruz.
Bu dosyayı SADECE BİR KERE çalıştırıyorsun. Sonrasında stok.db dosyası
elinde kalıyor ve uygulama onu kullanıyor.

Üretilen veri gerçekçi: Teknorot'un ürün gamı direksiyon ve süspansiyon
parçaları (rot, rotil, salıncak, denge çubuğu...), araç listesi de
Türkiye'deki yaygın ticari araç ve binek modelleri.
"""

import random
from datetime import datetime, timedelta

from db import get_connection, init_db

# random.seed(42): rastgeleliği "sabitler". Scripti tekrar çalıştırınca
# aynı veriyi üretir. Hata ayıklarken çok işine yarar — dün gördüğün hata
# bugün de aynı veriyle karşına çıkar.
random.seed(42)

# --- Ürün grupları ---
# İsimler ve ağırlıklar teknorot.com katalog sayfasındaki gerçek ürün
# gruplarından ve yanlarındaki ürün adetlerinden alındı. Ağırlık ne kadar
# yüksekse o gruptan o kadar çok kalem üretilir — böylece dağılım gerçek
# katalogdakine benzer, salıncak ve rotbaşı ağırlıkta olur.
# (kategori, isim şablonu, ağırlık, min fiyat, max fiyat)
CATEGORIES = [
    ("Rotilli Salıncak",   "Rotilli Salıncak",   4707,  680, 2400),
    ("Rotbaşı",            "Rotbaşı",            3693,  180,  620),
    ("Salıncak Burcu",     "Salıncak Burcu",     2741,   90,  310),
    ("Denge Kolu",         "Denge Kolu",         2234,  260,  740),
    ("Rot Mili",           "Rot Mili",           2075,  320,  980),
    ("Rotilsiz Salıncak",  "Rotilsiz Salıncak",  1667,  540, 1900),
    ("Rotil",              "Rotil",              1325,  240,  870),
    ("Z-Rot",              "Z-Rot",              1100,  150,  480),
    ("Rotil Kiti",         "Rotil Kiti",         1018,  380, 1150),
    ("Denge Kolu Burcu",   "Denge Kolu Burcu",    831,   70,  260),
    ("Rot Takımı",         "Rot Takımı",          687,  520, 1600),
    ("Salıncak Tamir Kiti","Salıncak Tamir Kiti", 427,  210,  780),
    ("Rot Mili Kiti",      "Rot Mili Kiti",       134,  460, 1300),
    ("Salıncak Burç Kiti", "Salıncak Burç Kiti",  129,  180,  540),
    ("Viraj Lastiği",      "Viraj Lastiği",       119,   60,  220),
    ("Salıncak Kiti",      "Salıncak Kiti",        92,  820, 2600),
    ("Motor Takozu",       "Motor Takozu",         88,  240,  900),
    ("Z-Rot Burcu",        "Z-Rot Burcu",          69,   50,  190),
    ("Ara Rot",            "Ara Rot",              60,  340, 1050),
    ("Şanzıman Takozu",    "Şanzıman Takozu",      44,  220,  820),
    ("Amortisör Tablası",  "Amortisör Tablası",    39,  190,  680),
    ("Torsiyon Burcu",     "Torsiyon Burcu",       39,   80,  280),
    ("Deve Boynu",         "Deve Boynu",           32,  460, 1500),
    ("Körük",              "Körük",                22,   70,  240),
    ("Aks Burcu",          "Aks Burcu",            15,  110,  380),
]

# --- Araçlar: (marka, kod ön eki, ağırlık, [modeller]) ---
# Ağırlıklar yine katalogdaki gerçek marka adetleri.
# Kod ön eki gerçek Teknorot formatını taklit ediyor (örn. CI-621 gibi);
# gerçek kodlar için katalog dışa aktarımını kullan.
VEHICLES = [
    ("Ford",       "FD", 1969, ["Transit", "Transit Custom", "Focus", "Connect", "Ranger", "Fiesta", "Kuga"]),
    ("Mercedes",   "ME", 1663, ["Sprinter", "Vito", "Atego", "C Serisi", "E Serisi", "Actros"]),
    ("Volkswagen", "VW", 1487, ["Transporter", "Caddy", "Passat", "Golf", "Crafter", "Polo", "Tiguan"]),
    ("BMW",        "BM", 1425, ["3 Serisi", "5 Serisi", "1 Serisi", "X3", "X5"]),
    ("Toyota",     "TO", 1231, ["Corolla", "Hilux", "Yaris", "RAV4", "Auris"]),
    ("Audi",       "AU", 1146, ["A3", "A4", "A6", "Q5", "Q7"]),
    ("Nissan",     "NI", 1109, ["Qashqai", "Navara", "Micra", "Primastar", "X-Trail"]),
    ("Renault",    "RE", 1018, ["Clio", "Megane", "Kangoo", "Master", "Symbol", "Trafic", "Captur"]),
    ("Hyundai",    "HY",  899, ["Accent", "i20", "i30", "Tucson", "H100"]),
    ("Honda",      "HO",  823, ["Civic", "CR-V", "Jazz", "Accord"]),
    ("Kia",        "KI",  747, ["Ceed", "Sportage", "Rio", "Picanto"]),
    ("Opel",       "OP",  743, ["Astra", "Corsa", "Vivaro", "Combo", "Insignia"]),
    ("Peugeot",    "PE",  728, ["Partner", "Boxer", "301", "Expert", "208", "3008"]),
    ("Fiat",       "FI",  713, ["Doblo", "Egea", "Ducato", "Linea", "Fiorino", "Punto"]),
    ("Volvo",      "VO",  602, ["S60", "XC60", "V40", "FH"]),
    ("Citroen",    "CI",  590, ["Berlingo", "Jumper", "Jumpy", "C3", "C4", "C-Elysee", "Nemo"]),
    ("Seat",       "SE",  449, ["Leon", "Ibiza", "Toledo", "Ateca"]),
    ("Skoda",      "SK",  352, ["Octavia", "Fabia", "Superb", "Rapid"]),
    ("Dacia",      "DA",  169, ["Duster", "Logan", "Dokker", "Sandero", "Lodgy"]),
    ("Iveco",      "IV",   38, ["Daily", "Eurocargo"]),
]

# Parçanın araç üstündeki konumu — aynı parça sağ/sol farklı stok kalemi.
POSITIONS = ["Ön Sağ", "Ön Sol", "Arka Sağ", "Arka Sol", "Ön Alt", "Ön Üst", ""]

# Depo lokasyonları
WAREHOUSES = ["A", "B", "C"]

# Hareket açıklamaları. Teknorot bir ÜRETİCİ: bu tablodaki ürünleri
# kendisi imal ediyor, satın almıyor. Bu yüzden girişler üretimden,
# çıkışlar sevkiyattan kaynaklanıyor — "tedarikçi teslimatı" gibi bir
# açıklama buraya uymaz. (Hammadde tarafı ayrı bir konu: çelik, dövme
# parça satın alınır ama o kalemler bu tabloda yok.)
# (açıklama, ağırlık) — ağırlık yüksekse o açıklama daha sık seçilir.
# Eşit olasılıkla seçseydik "sayım eksiği" ve "hatalı ürün ayrımı" en sık
# çıkış sebebi olurdu; gerçek bir üretim deposunda çıkışların büyük
# kısmı sevkiyattır, hurda ve sayım farkı istisnadır. Veri gerçekçi
# görünmüyorsa dayandığı oranlar yanlıştır.
MOVEMENT_NOTES_IN = [
    ("Üretimden giriş", 45),
    ("Üretim partisi tamamlandı", 30),
    ("Fason üretim girişi", 12),
    ("İade kabul", 6),
    ("Depo transferi", 5),
    ("Sayım fazlası", 2),
]
MOVEMENT_NOTES_OUT = [
    ("Müşteri sevkiyatı", 40),
    ("Bayi sevkiyatı", 28),
    ("Yurt dışı sevkiyat", 20),
    ("Numune gönderimi", 6),
    ("Hatalı ürün ayrımı", 4),
    ("Sayım eksiği", 2),
]


def _aciklama_sec(secenekler):
    """Ağırlıklı açıklama seçimi. random.choices bir LİSTE döndürür,
    o yüzden sondaki [0] ile tek elemanı alıyoruz."""
    return random.choices(
        [ad for ad, _ in secenekler],
        weights=[agirlik for _, agirlik in secenekler],
    )[0]


def generate_products(count=180):
    """count adet ürün sözlüğü üretir."""
    products = []
    used_codes = set()  # set kullanıyoruz çünkü "içinde var mı" kontrolü çok hızlı

    # random.choices(liste, weights=...) ağırlıklı seçim yapar:
    # ağırlığı yüksek olan daha sık seçilir. Düz random.choice() her şeyi
    # eşit olasılıkla seçerdi ve katalogda 15 çeşit olan "Aks Burcu" ile
    # 4707 çeşit olan "Rotilli Salıncak" aynı sıklıkta çıkardı.
    kategori_agirliklari = [c[2] for c in CATEGORIES]
    marka_agirliklari = [v[2] for v in VEHICLES]

    while len(products) < count:
        category, name_tpl, _, price_min, price_max = random.choices(
            CATEGORIES, weights=kategori_agirliklari)[0]
        make, prefix, _, models = random.choices(
            VEHICLES, weights=marka_agirliklari)[0]
        model = random.choice(models)
        position = random.choice(POSITIONS)

        # Parça kodu benzersiz olmalı (tabloda UNIQUE kısıtı var).
        # Format gerçek Teknorot kodlarını taklit ediyor: marka ön eki + numara.
        # Çakışırsa döngü başa dönüp yenisini dener.
        part_code = f"{prefix}-{random.randint(100, 9999)}"
        if part_code in used_codes:
            continue
        used_codes.add(part_code)

        # İsmi parça parça kur, boş parçaları at.
        # " ".join(...) -> parçaları aralarında boşlukla birleştirir.
        name_parts = [name_tpl, position, "-", make, model]
        name = " ".join(p for p in name_parts if p)

        products.append({
            "part_code": part_code,
            "oem_code": f"{random.randint(1000000, 9999999)}",
            "name": name,
            "category": category,
            "vehicle_make": make,
            "vehicle_model": model,
            "unit_price": round(random.uniform(price_min, price_max), 2),
            "min_stock": random.choice([10, 15, 20, 25, 30, 40, 50]),
            "location": f"Depo-{random.choice(WAREHOUSES)}-Raf-{random.randint(1, 24):02d}",
        })

    return products


def generate_movements(product_ids, months_back=6):
    """
    Her ürün için gerçekçi bir hareket geçmişi üretir.

    Mantık: önce bir açılış girişi yap, sonra araya karışık giriş/çıkışlar
    ekle. Çıkış miktarı o anki stoktan fazla olamaz — yoksa negatif stok
    çıkar ve veri anlamsız olur.
    """
    movements = []
    now = datetime.now()

    for pid in product_ids:
        # Açılış stoğu: bazı ürünler bol, bazıları az olsun ki
        # kritik stok uyarısı sayfası boş kalmasın.
        opening = random.choice([8, 12, 18, 25, 40, 60, 90, 150, 220])
        current = opening

        start = now - timedelta(days=months_back * 30)
        movements.append({
            "product_id": pid,
            "type": "IN",
            "quantity": opening,
            "created_at": start.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "Açılış stoğu",
        })

        # Bu ürün için kaç hareket olacak
        for _ in range(random.randint(2, 12)):
            # Geçmişten bugüne rastgele bir tarih
            day_offset = random.randint(1, months_back * 30)
            when = now - timedelta(
                days=day_offset,
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            )

            # %55 çıkış, %45 giriş — stok zamanla erisin
            if random.random() < 0.55 and current > 1:
                qty = random.randint(1, max(1, current // 2))
                current -= qty
                movements.append({
                    "product_id": pid,
                    "type": "OUT",
                    "quantity": qty,
                    "created_at": when.strftime("%Y-%m-%d %H:%M:%S"),
                    "note": _aciklama_sec(MOVEMENT_NOTES_OUT),
                })
            else:
                qty = random.randint(5, 80)
                current += qty
                movements.append({
                    "product_id": pid,
                    "type": "IN",
                    "quantity": qty,
                    "created_at": when.strftime("%Y-%m-%d %H:%M:%S"),
                    "note": _aciklama_sec(MOVEMENT_NOTES_IN),
                })

    return movements


# Uydurma müşteriler. Gerçek firma isimleri kullanılmadı; tür ve şehir
# dağılımı bir yedek parça üreticisinin müşteri profiline benzer olsun
# diye seçildi: ağırlık bayilerde, birkaç OEM ve ihracat müşterisi.
MUSTERILER = [
    ("Anadolu Otomotiv Yedek Parça Ltd. Şti.", "bayi", "Konya", "Türkiye", "1234567890", "0332 000 00 01"),
    ("Marmara Oto Ticaret A.Ş.", "bayi", "İstanbul", "Türkiye", "2345678901", "0212 000 00 02"),
    ("Ege Parça Dağıtım Ltd. Şti.", "bayi", "İzmir", "Türkiye", "3456789012", "0232 000 00 03"),
    ("Başkent Ağır Vasıta Servis", "bayi", "Ankara", "Türkiye", "4567890123", "0312 000 00 04"),
    ("Çukurova Otomotiv Sanayi", "bayi", "Adana", "Türkiye", "5678901234", "0322 000 00 05"),
    ("Karadeniz Ticari Araç Servisi", "bayi", "Samsun", "Türkiye", "6789012345", "0362 000 00 06"),
    ("Doğu Otomotiv Ekipman", "oem", "Bursa", "Türkiye", "7890123456", "0224 000 00 07"),
    ("Nord Auto Parts GmbH", "ihracat", "Hamburg", "Almanya", None, "+49 40 000 00 08"),
    ("Balkan Spare Parts D.O.O.", "ihracat", "Belgrad", "Sırbistan", None, "+381 11 000 09"),
    ("Gulf Automotive Trading LLC", "ihracat", "Dubai", "BAE", None, "+971 4 000 00 10"),
]


def seed_musteriler(conn):
    """Müşterileri ekler."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM musteriler")
    if cur.fetchone()[0]:
        return
    cur.executemany(
        "INSERT INTO musteriler (unvan, tur, sehir, ulke, vergi_no, telefon) "
        "VALUES (?, ?, ?, ?, ?, ?)", MUSTERILER)
    conn.commit()
    print(f"{len(MUSTERILER)} müşteri eklendi.")


def seed_users():
    """Demo kullanıcılarını oluşturur.

    Parolalar burada düz metin yazılı ama veritabanına özetlenerek
    gidiyor — add_user işi kendisi hallediyor. Bu bir öğrenme
    projesi olduğu için parolalar basit ve README'de yazılı;
    gerçek bir sistemde ilk girişte değiştirme zorunluluğu olurdu.
    """
    from queries import add_user, list_users

    if list_users():
        print("Kullanıcılar zaten var, atlanıyor.")
        return

    demo = [
        ("depo",  "Depo Görevlisi",   "depo",     "depo123"),
        ("satis", "Satış Sorumlusu",  "satis",    "satis123"),
        ("admin", "Sistem Yöneticisi", "yonetici", "admin123"),
    ]
    for kullanici_adi, ad_soyad, rol, parola in demo:
        add_user(kullanici_adi, ad_soyad, rol, parola)

    print(f"{len(demo)} kullanıcı oluşturuldu (depo / satis / admin).")


def seed():
    init_db()
    seed_users()
    conn = get_connection()
    cur = conn.cursor()

    # Önce temizle — scripti ikinci kez çalıştırdığında veri ikiye katlanmasın.
    # Sıra önemli: önce hareketler, sonra ürünler (foreign key yüzünden).
    cur.execute("DELETE FROM stock_movements")
    cur.execute("DELETE FROM belge_satirlari")
    cur.execute("DELETE FROM belgeler")
    cur.execute("DELETE FROM products")
    seed_musteriler(conn)
    musteri_idler = [r["id"] for r in cur.execute("SELECT id FROM musteriler")]

    products = generate_products(180)

    # DİKKAT: Değerleri asla f-string ile SQL'in içine gömme.
    # Yanlış:  f"INSERT INTO products VALUES ('{name}')"   <- SQL injection
    # Doğru:   "... VALUES (?, ?, ?)" ve değerleri ayrı parametre olarak ver.
    # Soru işaretlerini SQLite kendisi güvenli şekilde yerleştirir.
    cur.executemany("""
        INSERT INTO products
            (part_code, oem_code, name, category, vehicle_make,
             vehicle_model, unit_price, min_stock, location)
        VALUES
            (:part_code, :oem_code, :name, :category, :vehicle_make,
             :vehicle_model, :unit_price, :min_stock, :location)
    """, products)

    # Ürünler eklendi, şimdi veritabanının verdiği id'leri geri okuyoruz.
    # Hareketleri bu id'lere bağlayacağız.
    product_ids = [row["id"] for row in cur.execute("SELECT id FROM products")]

    movements = generate_movements(product_ids)

    # --- Geçmiş hareketleri belgelere bağla ---
    # Artık her hareketin bir belgesi olmalı. Geçmiş veriyi belgesiz
    # bırakırsak sistem kendi kuralını ilk günden çiğnemiş olur:
    # ekranda "belgesiz hareket" diye bir şey görünür ve kimse nereden
    # geldiğini açıklayamaz.
    #
    # Açılış stokları tek bir Açılış Fişine, sonraki hareketler ise
    # ay bazında gruplanmış üretim/satış belgelerine bağlanıyor.
    depo_id = cur.execute(
        "SELECT id FROM users WHERE kullanici_adi = 'depo'").fetchone()["id"]
    satis_id = cur.execute(
        "SELECT id FROM users WHERE kullanici_adi = 'satis'").fetchone()["id"]

    # Hareketleri (tip, ay) çiftine göre grupla
    gruplar = {}
    for m in movements:
        if m["note"] == "Açılış stoğu":
            anahtar = ("acilis", "")
        else:
            ay = m["created_at"][:7]            # "2026-03"
            anahtar = (m["type"], ay)
        gruplar.setdefault(anahtar, []).append(m)

    sayaclar = {"acilis": 0, "uretim": 0, "satis": 0}
    belge_sayisi = 0

    for (anahtar_tip, ay), grup in sorted(gruplar.items(), key=lambda g: (g[0][1], g[0][0])):
        if anahtar_tip == "acilis":
            tip, kullanici = "acilis", depo_id
            aciklama = "Sistem devreye alma — başlangıç sayımı"
        elif anahtar_tip == "IN":
            tip, kullanici = "uretim", depo_id
            aciklama = f"{ay} dönemi üretim girişleri"
        else:
            tip, kullanici = "satis", satis_id
            aciklama = f"{ay} dönemi sevkiyatları"

        sayaclar[tip] += 1
        on_ek = {"acilis": "AF", "uretim": "ÜF", "satis": "SF"}[tip]
        yil = grup[0]["created_at"][:4]
        belge_no = f"{on_ek}-{yil}-{sayaclar[tip]:04d}"

        # Satış faturaları müşterisiz olamaz — geçmiş veride de olmamalı.
        musteri = random.choice(musteri_idler) if tip == "satis" else None

        cur.execute(
            "INSERT INTO belgeler "
            "(belge_no, tip, tarih, user_id, musteri_id, aciklama, durum) "
            "VALUES (?, ?, ?, ?, ?, ?, 'onayli')",
            (belge_no, tip, grup[0]["created_at"], kullanici, musteri, aciklama))
        belge_id = cur.lastrowid
        belge_sayisi += 1

        for m in grup:
            m["belge_id"] = belge_id
            cur.execute(
                "INSERT INTO belge_satirlari "
                "(belge_id, product_id, miktar, birim_fiyat) VALUES (?, ?, ?, ?)",
                (m["product_id"], ) and (belge_id, m["product_id"], m["quantity"],
                 cur.execute("SELECT unit_price FROM products WHERE id = ?",
                             (m["product_id"],)).fetchone()[0]))

    cur.executemany("""
        INSERT INTO stock_movements
            (product_id, type, quantity, created_at, note, belge_id)
        VALUES (:product_id, :type, :quantity, :created_at, :note, :belge_id)
    """, movements)

    conn.commit()

    # Özet bilgi bas — script çalıştı mı, ne üretti, göz kararı anlaşılsın.
    total_stock = cur.execute("""
        SELECT SUM(CASE WHEN type = 'IN' THEN quantity ELSE -quantity END)
        FROM stock_movements
    """).fetchone()[0]

    print(f"{len(products)} ürün eklendi.")
    print(f"{belge_sayisi} belge oluşturuldu.")
    print(f"{len(movements)} stok hareketi eklendi (hepsi bir belgeye bağlı).")
    print(f"Depodaki toplam adet: {total_stock}")

    conn.close()


if __name__ == "__main__":
    seed()
