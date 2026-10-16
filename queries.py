"""
queries.py — Tüm SQL burada. Hiçbir dış kütüphane kullanmaz.

Streamlit'li sürümde bu fonksiyonlar pandas DataFrame döndürüyordu.
Burada bunun yerine SÖZLÜK LİSTESİ döndürüyorlar:

    [{"part_code": "TR-1234", "name": "Rotil...", "stock": 42}, ...]

Yani her satır bir sözlük, her kolon o sözlükte bir anahtar.
pandas'ın yaptığı işin %90'ı zaten buydu; sadece tabloyu ekrana
basma kısmını kendimiz yazacağız.
"""

from datetime import datetime

from db import get_connection

# ---------------------------------------------------------------------------
# Projenin kalbi olan sorgu parçası.
#
# products tablosunda "stock" kolonu YOK. Mevcut stoğu her seferinde
# hareketlerden hesaplıyoruz: girişler artı, çıkışlar eksi.
#
# LEFT JOIN: hiç hareketi olmayan ürün de listede kalsın (stoğu 0 olarak).
# Normal JOIN olsaydı o ürün tamamen kaybolurdu.
# COALESCE(x, 0): x NULL ise 0 döndür.
#
# {where} kısmı .format() ile dolduruluyor — oraya sadece BİZİM yazdığımız
# koşul metni giriyor, kullanıcının yazdığı değerler değil. Değerler her
# zaman ? ile ayrı gidiyor.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# BELGE SABİTLERİ
#
# Dosyanın en üstünde duruyorlar çünkü hem sorgular hem arayüz bunları
# kullanıyor. (Bir kez fonksiyonların arasına koymuştum ve kod düzenlerken
# yanlışlıkla silindi — sabitlerin tepede toplu durması bunu zorlaştırır.)
# ---------------------------------------------------------------------------

# Düzeltme fişinde seçilebilecek sebepler. SERBEST METİN DEĞİL.
#
# Neden liste? Serbest metin bırakınca kullanıcı oraya "bayi satışı"
# yazabiliyor ve satış, düzeltme fişi olarak kaydediliyor. Sonra
# raporlarda satışlar eksik, düzeltmeler şişkin görünür.
#
# İş kuralı serbest metinle korunmaz. Düzeltmenin meşru sebepleri
# sınırlıdır; satış onlardan biri değildir — satışın kendi belgesi var.
#
# Müşteri iadesi de bu listeden çıkarıldı: iadenin müşterisi ve tutarı
# olduğu için kendi belgesi (İade Faturası) var. Burada kalsaydı satış
# sorumlusu kendi müşterisinin iadesini göremezdi.
#
# (sebep_kodu, ekranda_görünen_ad, yön)
DUZELTME_SEBEPLERI = [
    ("sayim_fazlasi", "Sayım fazlası", "IN"),
    ("transfer_giris", "Depo transferi (giriş)", "IN"),
    ("sayim_eksigi", "Sayım eksiği", "OUT"),
    ("fire", "Fire / hurda ayrımı", "OUT"),
    ("numune", "Numune gönderimi", "OUT"),
    ("transfer_cikis", "Depo transferi (çıkış)", "OUT"),
]

DUZELTME_SEBEP_HARITA = {kod: (ad, yon) for kod, ad, yon in DUZELTME_SEBEPLERI}

# (ön_ek, ekranda_görünen_ad, hareket_yönü)
# Düzeltme fişinin yönü sabit değil: sayım fazlası girişse, fire çıkıştır.
# O yüzden None — yön sebebe göre belirleniyor.
BELGE_TIPLERI = {
    "uretim": ("ÜF", "Üretim Fişi", "IN"),
    "satis": ("SF", "Satış Faturası", "OUT"),
    # İade faturası: müşteriden geri dönen mal. Satış faturasının aynısı
    # ama yönü ters — stoğu ARTIRIYOR.
    #
    # Neden düzeltme fişiyle yapılmıyor? Çünkü iade ticari bir işlem:
    # müşterisi var, tutarı var, bir satışa bağlı. Düzeltme fişi ise
    # depo içi bir istisna kaydı (sayım farkı, fire). İkisini aynı
    # belgeye sıkıştırmak, satışın kendi iadesini göremediği bir sistem
    # üretiyordu.
    "iade": ("IF", "İade Faturası", "IN"),
    "sayim": ("SY", "Düzeltme Fişi", None),
    "acilis": ("AF", "Açılış Fişi", "IN"),
}


PRODUCTS_WITH_STOCK = """
    SELECT
        p.id, p.part_code, p.oem_code, p.name, p.category,
        p.vehicle_make, p.vehicle_model, p.unit_price,
        p.min_stock, p.location,
        COALESCE(SUM(
            CASE WHEN m.type = 'IN' THEN m.quantity ELSE -m.quantity END
        ), 0) AS stock
    FROM products p
    LEFT JOIN stock_movements m ON m.product_id = p.id
    {where}
    GROUP BY p.id
"""


def _rows(sql, params=()):
    """SQL çalıştırır, sonucu sözlük listesi olarak döndürür."""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        # conn.row_factory = sqlite3.Row olduğu için satırlar sözlüğe çevrilebiliyor.
        return [dict(row) for row in cursor.fetchall()]
    finally:
        # finally: sorgu hata verse bile bağlantı kapanır.
        conn.close()


def _one(sql, params=()):
    """Tek satır bekleyen sorgular için."""
    rows = _rows(sql, params)
    return rows[0] if rows else None


# --- Okuma -----------------------------------------------------------------

def get_products(search="", category="", vehicle_make="", only_critical=False):
    """Ürünleri stok bilgisiyle getirir. Boş bırakılan filtreler yok sayılır."""
    conditions = []
    params = []

    if search:
        # Tek arama kutusu üç kolona birden baksın.
        # LIKE '%metin%' -> "içinde geçsin" demek.
        conditions.append(
            "(p.part_code LIKE ? OR p.name LIKE ? OR p.oem_code LIKE ?)"
        )
        like = f"%{search}%"
        params += [like, like, like]

    if category:
        conditions.append("p.category = ?")
        params.append(category)

    if vehicle_make:
        conditions.append("p.vehicle_make = ?")
        params.append(vehicle_make)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = PRODUCTS_WITH_STOCK.format(where=where)

    # Kritik filtresi HAVING'e yazılmak zorunda, WHERE'e değil:
    # "stock" bir SUM() sonucu, yani gruplama YAPILDIKTAN SONRA oluşuyor.
    # WHERE gruplamadan önce çalışır, o aşamada stock diye bir şey yok.
    if only_critical:
        sql += " HAVING stock <= p.min_stock"

    sql += " ORDER BY p.part_code"

    # GÜVENLİK: değerleri stringe gömmüyoruz, ? ile gönderiyoruz.
    # Aksi halde arama kutusuna  '; DROP TABLE products; --  yazan biri
    # tabloyu uçurabilirdi. Buna SQL Injection deniyor.
    return _rows(sql, tuple(params))


def get_critical_products():
    """Stoğu kritik seviyeye inmiş veya altına düşmüş ürünler."""
    rows = get_products(only_critical=True)
    # En acili en üstte: stoğun kritik seviyeye oranı en düşük olan.
    rows.sort(key=lambda r: r["stock"] / max(r["min_stock"], 1))
    return rows


def get_product(product_id):
    """Tek ürün, stok bilgisiyle."""
    sql = PRODUCTS_WITH_STOCK.format(where="WHERE p.id = ?")
    return _one(sql, (product_id,))


def get_product_totals(product_id):
    """Ürünün hareket özeti: toplam giriş, toplam çıkış, hareket sayısı.

    Tek sorguda üç değer birden hesaplıyoruz. Üç ayrı sorgu yazmak yerine
    SUM içinde CASE kullanmak yaygın bir kalıp: "şartı sağlayanı topla,
    sağlamayanı 0 say."
    """
    row = _one("""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'IN'  THEN quantity ELSE 0 END), 0) AS giris,
            COALESCE(SUM(CASE WHEN type = 'OUT' THEN quantity ELSE 0 END), 0) AS cikis,
            COUNT(*) AS hareket_sayisi,
            MIN(created_at) AS ilk_hareket,
            MAX(created_at) AS son_hareket
        FROM stock_movements
        WHERE product_id = ?
    """, (product_id,))
    return row or {"giris": 0, "cikis": 0, "hareket_sayisi": 0,
                   "ilk_hareket": None, "son_hareket": None}


def get_product_stock(product_id):
    """Tek bir ürünün anlık stoğu. Çıkış yaparken kontrol için gerekli."""
    row = _one(
        """
        SELECT COALESCE(SUM(
            CASE WHEN type = 'IN' THEN quantity ELSE -quantity END
        ), 0) AS stock
        FROM stock_movements WHERE product_id = ?
        """,
        (product_id,),
    )
    return row["stock"] if row else 0


def get_movements(limit=25, product_id=None):
    """Son stok hareketleri, ürün bilgisiyle birlikte."""
    sql = """
        SELECT m.id, m.created_at, m.type, m.quantity, m.note,
               m.product_id, m.belge_id, p.part_code, p.name
        FROM stock_movements m
        JOIN products p ON p.id = m.product_id
    """
    params = []

    if product_id:
        sql += " WHERE m.product_id = ?"
        params.append(product_id)

    sql += " ORDER BY m.created_at DESC, m.id DESC LIMIT ?"
    params.append(limit)

    return _rows(sql, tuple(params))


def get_filter_options():
    """Filtre kutularını doldurmak için kategori ve marka listesi."""
    categories = [r["category"] for r in _rows(
        "SELECT DISTINCT category FROM products ORDER BY category")]
    makes = [r["vehicle_make"] for r in _rows(
        "SELECT DISTINCT vehicle_make FROM products "
        "WHERE vehicle_make IS NOT NULL ORDER BY vehicle_make")]
    return categories, makes


def get_product_options():
    """Hareket formundaki açılır liste için (id, etiket) çiftleri.

    Etikette mevcut stok da yazıyor: "FD-1054 — Rotil ... (117 adet)"
    Böylece çıkış yaparken listeden seçtiğin anda elinde ne kadar
    olduğunu görüyorsun; ayrıca bir yere bakmak gerekmiyor.

    Stoğu ayrı ayrı sorgulamıyoruz — PRODUCTS_WITH_STOCK tek sorguda
    hepsini getiriyor. 180 ürün için 180 sorgu atmak (buna N+1 problemi
    deniyor) çalışan ama yavaş bir çözüm olurdu.
    """
    rows = _rows(PRODUCTS_WITH_STOCK.format(where="") + " ORDER BY p.part_code")
    return [
        (r["id"], f"{r['part_code']} — {r['name']} ({r['stock']} adet)")
        for r in rows
    ]


# --- Kullanıcılar -------------------------------------------------------

def get_user_by_username(kullanici_adi):
    """Kullanıcı adına göre kullanıcıyı getirir (parola özeti dahil).

    Sadece giriş doğrulaması için kullanılır. Başka yerlerde parola
    özetini taşımamak iyi bir alışkanlık.
    """
    return _one(
        "SELECT * FROM users WHERE kullanici_adi = ? AND aktif = 1",
        (kullanici_adi.strip().lower(),)
    )


def list_users():
    """Tüm kullanıcılar. Parola özeti DIŞARIDA — ekranda gösterilecek veri."""
    return _rows("""
        SELECT id, kullanici_adi, ad_soyad, rol, aktif, olusturma
        FROM users ORDER BY rol, kullanici_adi
    """)


def add_user(kullanici_adi, ad_soyad, rol, parola):
    """Yeni kullanıcı ekler.

    Parolayı burada özetliyoruz; çağıran kodun parolayı özetlemeyi
    hatırlaması gerekmiyor. Güvenlikle ilgili adımları tek yere
    toplamak, birinin unutmasını engellemenin en iyi yolu.
    """
    from auth import ROLLER, parola_ozeti

    kullanici_adi = (kullanici_adi or "").strip().lower()
    ad_soyad = (ad_soyad or "").strip()

    if not kullanici_adi:
        raise ValueError("Kullanıcı adı zorunlu.")
    if " " in kullanici_adi:
        raise ValueError("Kullanıcı adında boşluk olamaz.")
    if not ad_soyad:
        raise ValueError("Ad soyad zorunlu.")
    if rol not in ROLLER:
        raise ValueError("Geçersiz rol.")
    if not parola or len(parola) < 5:
        raise ValueError("Parola en az 5 karakter olmalı.")
    if _one("SELECT id FROM users WHERE kullanici_adi = ?", (kullanici_adi,)):
        raise ValueError(f"'{kullanici_adi}' kullanıcı adı zaten alınmış.")

    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO users
                (kullanici_adi, ad_soyad, rol, parola_ozeti, aktif, olusturma)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (kullanici_adi, ad_soyad, rol, parola_ozeti(parola),
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def set_user_active(user_id, aktif):
    """Kullanıcıyı aktif/pasif yapar."""
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET aktif = ? WHERE id = ?",
                     (1 if aktif else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def set_user_password(user_id, parola):
    """Kullanıcının parolasını değiştirir."""
    from auth import parola_ozeti

    if not parola or len(parola) < 5:
        raise ValueError("Parola en az 5 karakter olmalı.")

    conn = get_connection()
    try:
        conn.execute("UPDATE users SET parola_ozeti = ? WHERE id = ?",
                     (parola_ozeti(parola), user_id))
        conn.commit()
    finally:
        conn.close()


def _lokasyon_parcala(location):
    """'Depo-A-Raf-03' -> ('A', '03')

    Bozuk veya boş lokasyonlarda ('-', '-') döndürür ki program çökmesin.
    Katalogdan aktarılan ürünlerde de aynı biçim kullanılıyor, ama
    ileride elle ürün eklenirse farklı bir şey yazılabilir.
    """
    if not location:
        return "-", "-"
    parcalar = location.split("-")
    # Beklenen: ["Depo", "A", "Raf", "03"] -> 4 parça
    if len(parcalar) < 4:
        return location, "-"
    return parcalar[1], parcalar[3]


def get_warehouse_summary():
    """Depo bazlı özet: çeşit, adet, değer, kritik ürün sayısı."""
    depolar = {}
    for r in get_products():
        depo, _ = _lokasyon_parcala(r["location"])
        g = depolar.setdefault(depo, {"depo": depo, "cesit": 0, "adet": 0,
                                      "deger": 0.0, "kritik": 0, "raflar": set()})
        g["cesit"] += 1
        g["adet"] += r["stock"]
        g["deger"] += r["stock"] * r["unit_price"]
        if r["stock"] <= r["min_stock"]:
            g["kritik"] += 1
        g["raflar"].add(_lokasyon_parcala(r["location"])[1])

    # set'i sayıya çevir — şablonda uzunluğuyla işimiz var, kendisiyle değil
    for g in depolar.values():
        g["raf_sayisi"] = len(g["raflar"])
        del g["raflar"]

    return sorted(depolar.values(), key=lambda g: g["depo"])


def get_shelf_summary(depo):
    """Bir depodaki rafların özeti."""
    raflar = {}
    for r in get_products():
        d, raf = _lokasyon_parcala(r["location"])
        if d != depo:
            continue
        g = raflar.setdefault(raf, {"raf": raf, "cesit": 0, "adet": 0,
                                    "deger": 0.0, "kritik": 0})
        g["cesit"] += 1
        g["adet"] += r["stock"]
        g["deger"] += r["stock"] * r["unit_price"]
        if r["stock"] <= r["min_stock"]:
            g["kritik"] += 1

    return sorted(raflar.values(), key=lambda g: g["raf"])


def get_products_by_location(depo, raf=None, durum=None):
    """Belirli bir depodaki ürünler.

    raf verilirse sadece o raf.
    durum verilirse stok durumuna göre süzer:
        "kritik"  -> stok <= kritik seviye  (acil üretim gerekiyor)
        "sinirda" -> kritik ile 1.5 katı arası (yakında bitecek)

    Süzme ölçütleri arayüzdeki durum etiketleriyle birebir aynı;
    ekranda "Kritik" yazan ürün burada da kritik sayılıyor. Aynı kuralı
    iki yerde farklı yazmak, listenin etiketle çelişmesine yol açar.
    """
    sonuc = []
    for r in get_products():
        d, s = _lokasyon_parcala(r["location"])
        if d != depo:
            continue
        if raf and s != raf:
            continue

        if durum == "kritik" and not r["stock"] <= r["min_stock"]:
            continue
        if durum == "sinirda" and not (
                r["min_stock"] < r["stock"] <= r["min_stock"] * 1.5):
            continue

        sonuc.append(r)

    # Kritik listede en acil olan üstte olsun: stoğun kritik seviyeye
    # oranı en düşük olan ilk sırada.
    if durum:
        sonuc.sort(key=lambda r: r["stock"] / max(r["min_stock"], 1))

    return sonuc


def get_dashboard_stats():
    """Panel üstündeki özet sayılar.

    pandas olmadığı için toplamları Python'da hesaplıyoruz.
    180 satırda bu göz açıp kapayana kadar biter; milyon satırda
    aynı işi SQL'e yaptırmak gerekirdi.
    """
    rows = get_products()
    critical = [r for r in rows if r["stock"] <= r["min_stock"]]

    return {
        "urun_sayisi": len(rows),
        "toplam_adet": sum(r["stock"] for r in rows),
        "stok_degeri": sum(r["stock"] * r["unit_price"] for r in rows),
        "kritik_sayisi": len(critical),
        "tukenen": sum(1 for r in rows if r["stock"] <= 0),
    }


def get_category_summary():
    """Kategori bazlı toplam adet ve değer."""
    rows = get_products()

    # Sözlükte grupla: {"Rotil": {"adet": 0, "deger": 0.0, "cesit": 0}, ...}
    groups = {}
    for r in rows:
        g = groups.setdefault(
            r["category"], {"kategori": r["category"], "cesit": 0,
                            "adet": 0, "deger": 0.0}
        )
        g["cesit"] += 1
        g["adet"] += r["stock"]
        g["deger"] += r["stock"] * r["unit_price"]

    # setdefault: anahtar varsa değerini getir, yoksa önce oluştur.
    # pandas'taki groupby'ın elle yazılmış hali bu.
    return sorted(groups.values(), key=lambda g: g["adet"], reverse=True)


# --- Yazma -----------------------------------------------------------------

# --- Ürün yazma ---------------------------------------------------------

# Formdan gelmesi beklenen alanlar ve ekranda görünen adları.
# Tek yerde tutuyoruz ki doğrulama, INSERT ve UPDATE aynı listeyi kullansın.
URUN_ALANLARI = [
    ("part_code", "Parça Kodu"),
    ("oem_code", "OEM Kodu"),
    ("name", "Ürün Adı"),
    ("category", "Ürün Grubu"),
    ("vehicle_make", "Marka"),
    ("vehicle_model", "Model"),
    ("unit_price", "Birim Fiyat"),
    ("min_stock", "Kritik Seviye"),
    ("location", "Depo Lokasyonu"),
]


def _urun_dogrula(veri, mevcut_id=None):
    """Ürün verisini doğrular ve temizlenmiş sözlük döndürür.

    mevcut_id: güncelleme yapılıyorsa o ürünün id'si. Parça kodu
    benzersizlik kontrolünde "kendisi hariç" demek için gerekli —
    yoksa ürünü kendi koduyla kaydetmeye çalıştığında
    "bu kod kullanılıyor" hatası alırsın.

    Kural ihlalinde ValueError fırlatır.
    """
    temiz = {}

    # strip(): baştaki ve sondaki boşlukları atar. Kullanıcı " TR-100 "
    # yazdıysa kod " TR-100 " olarak kaydedilmesin.
    for alan, _ in URUN_ALANLARI:
        temiz[alan] = (veri.get(alan) or "").strip()

    if not temiz["part_code"]:
        raise ValueError("Parça kodu zorunlu.")
    if not temiz["name"]:
        raise ValueError("Ürün adı zorunlu.")
    if not temiz["category"]:
        raise ValueError("Ürün grubu zorunlu.")

    # Parça kodu benzersiz olmalı (tabloda UNIQUE kısıtı var).
    # Kısıta güvenip hatayı yakalamak da olurdu ama o zaman kullanıcıya
    # "IntegrityError" gösterirdik; kendimiz kontrol edip anlaşılır
    # mesaj vermek daha iyi.
    if mevcut_id:
        cakisma = _one("SELECT id FROM products WHERE part_code = ? AND id != ?",
                       (temiz["part_code"], mevcut_id))
    else:
        cakisma = _one("SELECT id FROM products WHERE part_code = ?",
                       (temiz["part_code"],))
    if cakisma:
        raise ValueError(f"'{temiz['part_code']}' kodu başka bir üründe kullanılıyor.")

    # Sayısal alanlar. Formdan her şey metin olarak gelir.
    # Virgülü noktaya çeviriyoruz: kullanıcı "1.234,50" yazabilir.
    fiyat_metni = temiz["unit_price"].replace(".", "").replace(",", ".")
    try:
        temiz["unit_price"] = round(float(fiyat_metni or 0), 2)
    except ValueError:
        raise ValueError("Birim fiyat sayı olmalı.")
    if temiz["unit_price"] < 0:
        raise ValueError("Birim fiyat negatif olamaz.")

    try:
        temiz["min_stock"] = int(temiz["min_stock"] or 0)
    except ValueError:
        raise ValueError("Kritik seviye tam sayı olmalı.")
    if temiz["min_stock"] < 0:
        raise ValueError("Kritik seviye negatif olamaz.")

    # Boş metin alanlarını None yap — veritabanında "" yerine NULL dursun.
    for alan in ("oem_code", "vehicle_make", "vehicle_model", "location"):
        temiz[alan] = temiz[alan] or None

    return temiz


def add_product(veri):
    """Yeni ürün ekler, eklenen ürünün id'sini döndürür."""
    temiz = _urun_dogrula(veri)

    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO products
                (part_code, oem_code, name, category, vehicle_make,
                 vehicle_model, unit_price, min_stock, location)
            VALUES
                (:part_code, :oem_code, :name, :category, :vehicle_make,
                 :vehicle_model, :unit_price, :min_stock, :location)
        """, temiz)
        conn.commit()
        # lastrowid: az önce eklenen satırın id'si. Ekledikten sonra
        # kullanıcıyı o ürünün sayfasına yönlendirmek için gerekli.
        return cursor.lastrowid
    finally:
        conn.close()


def update_product(product_id, veri):
    """Mevcut ürünü günceller."""
    if not _one("SELECT id FROM products WHERE id = ?", (product_id,)):
        raise ValueError("Ürün bulunamadı.")

    temiz = _urun_dogrula(veri, mevcut_id=product_id)
    temiz["id"] = product_id

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE products SET
                part_code = :part_code, oem_code = :oem_code, name = :name,
                category = :category, vehicle_make = :vehicle_make,
                vehicle_model = :vehicle_model, unit_price = :unit_price,
                min_stock = :min_stock, location = :location
            WHERE id = :id
        """, temiz)
        conn.commit()
    finally:
        conn.close()


def delete_product(product_id):
    """Ürünü siler. Belgede geçiyorsa SİLMEZ.

    Neden reddediyoruz? Ürün bir satış faturasında veya üretim fişinde
    geçiyorsa, silmek o belgenin bir satırını yok etmek demektir.
    Kesilmiş bir fatura eksik kalır, toplamı satırlarıyla tutmaz.
    Geçmiş belge değiştirilemez — bu, sistemin en baştan beri
    koruduğu kural.

    Bu hatayı otomatik testler yakaladı: belge tabloları eklendikten
    sonra silme "FOREIGN KEY constraint failed" ile çöküyordu.
    Kullanıcı anlamsız bir veritabanı hatası görürdü.

    Yanlışlıkla eklenmiş, hiç kullanılmamış ürünler silinebilir.
    Kullanılmış ürünler için doğru çözüm "pasife alma"dır: ürün
    listelerde görünmez olur ama geçmişi bozulmaz. (Henüz eklenmedi.)
    """
    urun = _one("SELECT part_code FROM products WHERE id = ?", (product_id,))
    if not urun:
        raise ValueError("Ürün bulunamadı.")

    belge = _one("""
        SELECT b.belge_no, COUNT(*) AS n
        FROM belge_satirlari s
        JOIN belgeler b ON b.id = s.belge_id
        WHERE s.product_id = ?
    """, (product_id,))

    if belge and belge["n"]:
        raise ValueError(
            f"'{urun['part_code']}' silinemiyor: {belge['n']} belgede geçiyor "
            f"(örn. {belge['belge_no']}). Geçmiş belgeler değiştirilemez."
        )

    conn = get_connection()
    try:
        silinen_hareket = conn.execute(
            "SELECT COUNT(*) AS n FROM stock_movements WHERE product_id = ?",
            (product_id,)).fetchone()["n"]

        # Sıra önemli: önce çocuk kayıtlar, sonra ana kayıt.
        conn.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))

        # İki DELETE tek commit ile kaydediliyor: arada hata çıkarsa
        # hiçbiri yazılmaz, yarım silinmiş ürün kalmaz.
        conn.commit()
        return urun["part_code"], silinen_hareket
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def duzeltme_fisi(user_id, product_id, sebep_kodu, miktar, aciklama=""):
    """Tek kalemlik düzeltme/sayım fişi oluşturur.

    Stok Hareketi ekranı bunu kullanıyor. Neden doğrudan hareket
    yazmıyoruz?

    Aşama 2'de "hiçbir hareket belgesiz oluşmaz" kuralını koyduk ama
    bu ekran onu atlıyordu: elle girilen hareketlerin belge_id'si boş
    kalıyordu. Sistem kendi kuralını çiğniyorsa kural yok demektir.

    Gerçek ERP'lerde de belgesiz stok girişi yoktur. Elle düzeltme
    istisnai bir işlemdir ve kendi belge tipi vardır (sayım fişi,
    düzeltme fişi) — çünkü denetimde en çok bakılan yer orasıdır.
    """
    if sebep_kodu not in DUZELTME_SEBEP_HARITA:
        raise ValueError("Geçerli bir düzeltme sebebi seçilmeli.")

    sebep_adi, yon = DUZELTME_SEBEP_HARITA[sebep_kodu]

    return belge_olustur(
        "sayim", user_id,
        [{"product_id": product_id, "miktar": miktar}],
        aciklama=aciklama, yon=yon, sebep=sebep_kodu,
    )


def belge_tipi_adi(tip):
    return BELGE_TIPLERI.get(tip, ("?", tip, None))[1]


def yeni_belge_no(tip, conn=None):
    """Sıradaki belge numarasını üretir: ÜF-2026-0001

    conn verilmişse o bağlantıyı kullanır. belge_olustur bunu KİLİTLİ
    işleminin içinden çağırıyor; böylece numarayı okumakla kullanmak
    arasına başka kimse giremiyor.

    Kilit olmadan iki kullanıcı aynı anda belge keserse ikisi de aynı
    numarayı okur, ikincisi UNIQUE kısıtına takılır ve kullanıcı
    anlamsız bir veritabanı hatası görür. Bu gerçekten yaşandı:
    üç eşzamanlı satış denemesinin ikisi
    "UNIQUE constraint failed: belgeler.belge_no" ile çöktü.
    """
    on_ek = BELGE_TIPLERI[tip][0]
    yil = datetime.now().year
    desen = f"{on_ek}-{yil}-%"

    kapat = conn is None
    conn = conn or get_connection()
    try:
        row = conn.execute(
            "SELECT belge_no FROM belgeler WHERE belge_no LIKE ? "
            "ORDER BY belge_no DESC LIMIT 1", (desen,)
        ).fetchone()
    finally:
        if kapat:
            conn.close()

    if row:
        # "ÜF-2026-0007" -> 7 -> 8
        son = int(row["belge_no"].rsplit("-", 1)[1])
    else:
        son = 0

    return f"{on_ek}-{yil}-{son + 1:04d}"


def belge_olustur(tip, user_id, satirlar, aciklama="", tarih=None, yon=None,
                  musteri_id=None, sebep=None, kaynak_belge_id=None):
    """Belgeyi ve stok hareketlerini TEK İŞLEMDE oluşturur.

    satirlar: [{"product_id": 5, "miktar": 10, "birim_fiyat": 120.0}, ...]

    Ya hepsi yazılır ya hiçbiri. Belge kaydedilip hareketleri
    yazılmadan hata çıkarsa, ortada dayanaksız bir belge kalırdı —
    ya da tersi, sahipsiz hareketler. Bunu engelleyen şey
    transaction: commit'e kadar hiçbir şey kalıcı değil, arada
    hata olursa rollback ile hepsi geri alınır.
    """
    if tip not in BELGE_TIPLERI:
        raise ValueError("Geçersiz belge tipi.")
    if not satirlar:
        raise ValueError("Belgede en az bir satır olmalı.")

    # Satış faturası müşterisiz kesilemez. Bu kontrol arayüzde de var
    # ama asıl yeri burası: iş kuralı, onu çağıran ekrandan bağımsız
    # olarak geçerli olmalı.
    if tip == "satis":
        if not musteri_id:
            raise ValueError("Satış faturası için müşteri seçilmeli.")
        if not get_musteri(musteri_id):
            raise ValueError("Müşteri bulunamadı.")

    # --- İade faturası kuralları ---
    # İade, havada duran bir kayıt olamaz: hangi satıştan döndüğü belli
    # olmalı. Aksi halde "bu 20 adet nereden geldi" sorusunun cevabı yine
    # kaybolurdu — sistemin en baştaki kuralı bu.
    if tip == "iade":
        if not musteri_id:
            raise ValueError("İade faturası için müşteri seçilmeli.")
        if not kaynak_belge_id:
            raise ValueError("İade, hangi satış faturasına ait olduğu "
                             "belirtilmeden kesilemez.")

        kaynak = get_belge(kaynak_belge_id)
        if not kaynak or kaynak["tip"] != "satis":
            raise ValueError("Kaynak belge bir satış faturası olmalı.")
        if kaynak["durum"] == "iptal":
            raise ValueError(f"{kaynak['belge_no']} iptal edilmiş; "
                             "iptal edilmiş faturadan iade alınamaz.")
        if kaynak["musteri_id"] != musteri_id:
            raise ValueError("İade, faturanın kesildiği müşteriye ait olmalı.")

    # Üretim/satış gibi tiplerin yönü sabit (giriş/çıkış). Sayım fişinde
    # yön duruma göre değişir — sayımda fazla çıkarsa giriş, eksik
    # çıkarsa çıkış olur — o yüzden dışarıdan verilebiliyor.
    yon = yon or BELGE_TIPLERI[tip][2]
    if yon not in ("IN", "OUT"):
        raise ValueError("Hareket yönü belirtilmeli.")

    # --- Doğrulama: yazmaya başlamadan ÖNCE hepsini kontrol et ---
    temiz = []
    gorulen = set()
    for i, satir in enumerate(satirlar, start=1):
        try:
            pid = int(satir["product_id"])
            miktar = int(satir["miktar"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{i}. satır hatalı.")

        if miktar <= 0:
            raise ValueError(f"{i}. satırda miktar sıfırdan büyük olmalı.")

        urun = get_product(pid)
        if not urun:
            raise ValueError(f"{i}. satırdaki ürün bulunamadı.")

        # Aynı ürün iki satırda olursa stok kontrolü yanıltıcı olur:
        # her satır ayrı ayrı geçerli görünür ama toplamı stoğu aşabilir.
        if pid in gorulen:
            raise ValueError(f"'{urun['part_code']}' iki kez eklenmiş. "
                             "Aynı ürünü tek satırda topla.")
        gorulen.add(pid)

        if yon == "OUT" and miktar > urun["stock"]:
            raise ValueError(
                f"'{urun['part_code']}' için yetersiz stok. "
                f"Mevcut: {urun['stock']}, istenen: {miktar}."
            )

        temiz.append({
            "product_id": pid,
            "miktar": miktar,
            "birim_fiyat": float(satir.get("birim_fiyat") or urun["unit_price"]),
        })

    zaman = tarih or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        # ---------------------------------------------------------------
        # BEGIN IMMEDIATE: yazma kilidini HEMEN al.
        #
        # Buraya kadar yaptığımız doğrulama (stok yeterli mi) kilitsizdi
        # ve o yüzden güvenilmez: üç kullanıcı aynı anda okursa üçü de
        # aynı stoğu görür, üçü de "yeterli" der, üçü de yazar.
        # Gerçekten denedik: stok 152'den -121'e düştü.
        #
        # Buna "kontrol et sonra kullan" hatası deniyor (TOCTOU:
        # Time Of Check to Time Of Use). Kontrol ile kullanım arasında
        # dünya değişebilir.
        #
        # Çözüm: kilidi aldıktan SONRA stoğu bir kez daha okuyup
        # doğrulamak. Kilit sırasında başka kimse yazamaz, dolayısıyla
        # bu okuma artık güncelliğini kaybetmez.
        #
        # Normal BEGIN (deferred) yetmez: kilidi ilk YAZMADA alır, yani
        # okuma hâlâ korumasız kalır. IMMEDIATE baştan alır.
        # ---------------------------------------------------------------
        conn.execute("BEGIN IMMEDIATE")

        # --- İade miktarı kontrolü (kilit altında) ---
        # 100 adet satılan bir faturadan 150 adet iade alınamaz.
        # Ayrıca aynı faturadan daha önce iade alınmışsa o da hesaba
        # katılmalı: 100 satıldı, 60 iade edildi, kalan hak 40.
        #
        # Bu kontrol neden BEGIN IMMEDIATE'den SONRA? Bölüm 13'teki
        # yarış durumunun aynısı: iki kişi aynı anda iade kesmeye
        # çalışırsa ikisi de "kalan hak yeterli" görüp toplamı aşabilir.
        # Kilit alındıktan sonra okumak bunu engelliyor.
        if tip == "iade":
            for satir in temiz:
                satilan = conn.execute("""
                    SELECT COALESCE(SUM(s.miktar), 0) AS n
                    FROM belge_satirlari s
                    WHERE s.belge_id = ? AND s.product_id = ?
                """, (kaynak_belge_id, satir["product_id"])).fetchone()["n"]

                if satilan == 0:
                    kod = conn.execute(
                        "SELECT part_code FROM products WHERE id = ?",
                        (satir["product_id"],)).fetchone()["part_code"]
                    raise ValueError(
                        f"'{kod}' bu faturada satılmamış, iade edilemez.")

                # Aynı faturaya daha önce kesilmiş iadeler
                onceki = conn.execute("""
                    SELECT COALESCE(SUM(s.miktar), 0) AS n
                    FROM belge_satirlari s
                    JOIN belgeler b ON b.id = s.belge_id
                    WHERE b.tip = 'iade' AND b.durum = 'onayli'
                      AND b.kaynak_belge_id = ? AND s.product_id = ?
                """, (kaynak_belge_id, satir["product_id"])).fetchone()["n"]

                kalan = satilan - onceki
                if satir["miktar"] > kalan:
                    kod = conn.execute(
                        "SELECT part_code FROM products WHERE id = ?",
                        (satir["product_id"],)).fetchone()["part_code"]
                    raise ValueError(
                        f"'{kod}' için iade edilebilir miktar aşıldı. "
                        f"Faturada {satilan} adet satılmış, {onceki} adet "
                        f"zaten iade edilmiş, kalan hak {kalan} adet."
                    )

        if yon == "OUT":
            for satir in temiz:
                guncel = conn.execute("""
                    SELECT COALESCE(SUM(
                        CASE WHEN type = 'IN' THEN quantity ELSE -quantity END
                    ), 0) AS stok
                    FROM stock_movements WHERE product_id = ?
                """, (satir["product_id"],)).fetchone()["stok"]

                if satir["miktar"] > guncel:
                    kod = conn.execute(
                        "SELECT part_code FROM products WHERE id = ?",
                        (satir["product_id"],)).fetchone()["part_code"]
                    raise ValueError(
                        f"'{kod}' için yetersiz stok. "
                        f"Mevcut: {guncel}, istenen: {satir['miktar']}."
                    )

        belge_no = yeni_belge_no(tip, conn)

        cur = conn.execute(
            "INSERT INTO belgeler "
            "(belge_no, tip, tarih, user_id, musteri_id, sebep, "
            " kaynak_belge_id, aciklama, durum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'onayli')",
            (belge_no, tip, zaman, user_id, musteri_id, sebep,
             kaynak_belge_id, (aciklama or "").strip() or None)
        )
        belge_id = cur.lastrowid

        aciklama_metni = f"{BELGE_TIPLERI[tip][1]} {belge_no}"
        for satir in temiz:
            conn.execute(
                "INSERT INTO belge_satirlari "
                "(belge_id, product_id, miktar, birim_fiyat) VALUES (?, ?, ?, ?)",
                (belge_id, satir["product_id"], satir["miktar"], satir["birim_fiyat"])
            )
            conn.execute(
                "INSERT INTO stock_movements "
                "(product_id, type, quantity, created_at, note, belge_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (satir["product_id"], yon, satir["miktar"], zaman,
                 aciklama_metni, belge_id)
            )

        conn.commit()
        return belge_id, belge_no
    except Exception:
        conn.rollback()   # hata çıktı: yazılanların hepsini geri al
        raise
    finally:
        conn.close()


def get_belge(belge_id):
    """Tek belge, oluşturan kullanıcı bilgisiyle."""
    return _one("""
        SELECT b.*, u.ad_soyad, u.kullanici_adi,
               m.unvan AS musteri_unvan, m.tur AS musteri_tur,
               m.sehir AS musteri_sehir, m.vergi_no AS musteri_vergi_no,
               k.belge_no AS kaynak_belge_no
        FROM belgeler b
        JOIN users u ON u.id = b.user_id
        LEFT JOIN musteriler m ON m.id = b.musteri_id
        LEFT JOIN belgeler k ON k.id = b.kaynak_belge_id
        WHERE b.id = ?
    """, (belge_id,))


def get_belge_satirlari(belge_id):
    """Belgenin kalemleri, ürün bilgisiyle."""
    return _rows("""
        SELECT s.*, p.part_code, p.name, p.category, p.location,
               (s.miktar * s.birim_fiyat) AS tutar
        FROM belge_satirlari s
        JOIN products p ON p.id = s.product_id
        WHERE s.belge_id = ?
        ORDER BY s.id
    """, (belge_id,))


def list_belgeler(tip=None, user_id=None, limit=100,
                  arama="", bas_tarih="", bit_tarih="", durum=""):
    """Belge listesi, satır sayısı ve toplam tutarla birlikte.

    Satır bilgilerini ayrı sorgularla çekmek yerine JOIN + GROUP BY
    kullanıyoruz: 100 belge için 1 sorgu, 101 değil.

    arama: belge numarası, açıklama veya müşteri ünvanında geçen metin
    bas_tarih / bit_tarih: "2026-07-01" biçiminde, ikisi de isteğe bağlı
    durum: "onayli" veya "iptal"
    """
    sql = """
        SELECT b.id, b.belge_no, b.tip, b.tarih, b.aciklama, b.durum, b.sebep,
               u.ad_soyad, m.unvan AS musteri_unvan,
               COUNT(s.id) AS satir_sayisi,
               COALESCE(SUM(s.miktar), 0) AS toplam_miktar,
               COALESCE(SUM(s.miktar * s.birim_fiyat), 0) AS toplam_tutar
        FROM belgeler b
        JOIN users u ON u.id = b.user_id
        LEFT JOIN musteriler m ON m.id = b.musteri_id
        LEFT JOIN belge_satirlari s ON s.belge_id = b.id
    """
    kosullar, params = [], []
    if tip:
        kosullar.append("b.tip = ?")
        params.append(tip)
    if user_id:
        kosullar.append("b.user_id = ?")
        params.append(user_id)
    if durum in ("onayli", "iptal"):
        kosullar.append("b.durum = ?")
        params.append(durum)

    if arama:
        # Tek kutu üç alanda birden arasın: belge no, açıklama, müşteri.
        # Kullanıcı "SF-2026" de yazabilir, "Çukurova" da, "sevkiyat" da.
        kosullar.append("(b.belge_no LIKE ? OR b.aciklama LIKE ? "
                        "OR m.unvan LIKE ?)")
        like = f"%{arama}%"
        params += [like, like, like]

    # Tarih karşılaştırması metin üzerinden yapılıyor. Tarihleri
    # "2026-07-15 09:30:00" biçiminde sakladığımız için bu çalışır:
    # ISO biçiminde alfabetik sıra = kronolojik sıra.
    # "15.07.2026" saklasaydık bu karşılaştırma anlamsız olurdu.
    if bas_tarih:
        kosullar.append("b.tarih >= ?")
        params.append(bas_tarih)
    if bit_tarih:
        # Bitiş tarihi o günün TAMAMINI kapsasın. "2026-07-15" yazan
        # kullanıcı 15 Temmuz'un belgelerini de görmek ister; düz
        # karşılaştırma o günü dışarıda bırakırdı.
        kosullar.append("b.tarih <= ?")
        params.append(bit_tarih + " 23:59:59")

    if kosullar:
        sql += " WHERE " + " AND ".join(kosullar)

    sql += " GROUP BY b.id ORDER BY b.id DESC LIMIT ?"
    params.append(limit)
    return _rows(sql, tuple(params))


def get_product_belgeler(product_id, limit=50):
    """Bir ürünün geçtiği belgeler — ürün detay sayfası için."""
    return _rows("""
        SELECT b.id, b.belge_no, b.tip, b.tarih, b.durum,
               s.miktar, u.ad_soyad
        FROM belge_satirlari s
        JOIN belgeler b ON b.id = s.belge_id
        JOIN users u ON u.id = b.user_id
        WHERE s.product_id = ?
        ORDER BY b.id DESC LIMIT ?
    """, (product_id, limit))


# --- Müşteriler ------------------------------------------------------------

MUSTERI_TURLERI = {
    "bayi": "Bayi",
    "oem": "OEM Müşterisi",
    "ihracat": "İhracat",
    "perakende": "Perakende",
}


def list_musteriler(sadece_aktif=True):
    sql = "SELECT * FROM musteriler"
    if sadece_aktif:
        sql += " WHERE aktif = 1"
    sql += " ORDER BY unvan"
    return _rows(sql)


def get_musteri(musteri_id):
    return _one("SELECT * FROM musteriler WHERE id = ?", (musteri_id,))


def musteri_satis_ozeti():
    """Müşteri bazında satış toplamları — raporlama için."""
    return _rows("""
        SELECT m.id, m.unvan, m.tur, m.sehir,
               COUNT(DISTINCT b.id) AS fatura_sayisi,
               COALESCE(SUM(s.miktar), 0) AS toplam_adet,
               COALESCE(SUM(s.miktar * s.birim_fiyat), 0) AS toplam_tutar,
               MAX(b.tarih) AS son_satis
        FROM musteriler m
        LEFT JOIN belgeler b ON b.musteri_id = m.id
                            AND b.tip = 'satis' AND b.durum = 'onayli'
        LEFT JOIN belge_satirlari s ON s.belge_id = b.id
        GROUP BY m.id
        ORDER BY toplam_tutar DESC
    """)


def belge_iptal(belge_id, user_id, sebep=""):
    """Belgeyi iptal eder ve ters hareketleri oluşturur.

    NEDEN SİLMİYORUZ?
    Kayıt silmek geçmişi yok eder. Denetimde "bu fatura neden yok?"
    sorusunun cevabı olmalı. Ayrıca silinen bir belgenin numarası
    boşta kalır — SF-2026-0005 yokken 0004 ve 0006 varsa, aradaki
    numaranın ne olduğunu kimse açıklayamaz.

    Bunun yerine belge "iptal" durumuna alınıyor, listede İPTAL
    damgasıyla duruyor, ve etkisini geri almak için TERS HAREKETLER
    yazılıyor. Orijinal hareketler de yerinde kalıyor.

    Yani stok hareketleri tablosunda hem "+40" hem "-40" görünür.
    Net etki sıfır, ama ikisinin de kaydı var. Muhasebedeki
    "ters kayıt" mantığının aynısı.

    TUZAK: Bir üretim fişini iptal etmek stoğu azaltır. Ama o mal
    aradan geçen sürede satılmış olabilir — o zaman iptal stoğu
    eksiye düşürür. Bunu engellemek gerekiyor.
    """
    belge = get_belge(belge_id)
    if not belge:
        raise ValueError("Belge bulunamadı.")
    if belge["durum"] == "iptal":
        raise ValueError("Bu belge zaten iptal edilmiş.")

    satirlar = get_belge_satirlari(belge_id)
    if not satirlar:
        raise ValueError("Belgede kalem yok.")

    # Orijinal hareketin yönünü öğren; tersini yazacağız.
    ilk_hareket = _one(
        "SELECT type FROM stock_movements WHERE belge_id = ? LIMIT 1", (belge_id,))
    if not ilk_hareket:
        raise ValueError("Belgeye bağlı hareket bulunamadı.")

    orijinal_yon = ilk_hareket["type"]
    ters_yon = "OUT" if orijinal_yon == "IN" else "IN"

    # --- Kontrol: ters hareket stoğu eksiye düşürür mü? ---
    # Yazmaya başlamadan önce TÜM satırları kontrol ediyoruz. Yarısını
    # yazıp ortada kalmak, hiç yazmamaktan kötüdür.
    if ters_yon == "OUT":
        for satir in satirlar:
            mevcut = get_product_stock(satir["product_id"])
            if satir["miktar"] > mevcut:
                raise ValueError(
                    f"'{satir['part_code']}' iptal edilemiyor: fişteki "
                    f"{satir['miktar']} adedin {satir['miktar'] - mevcut} adedi "
                    f"zaten çıkış yapmış (mevcut stok {mevcut}). "
                    f"Önce ilgili çıkış belgelerini iptal et."
                )

    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    not_metni = f"İPTAL: {belge['belge_no']}"
    if sebep:
        not_metni += f" — {sebep.strip()[:80]}"

    conn = get_connection()
    try:
        for satir in satirlar:
            conn.execute(
                "INSERT INTO stock_movements "
                "(product_id, type, quantity, created_at, note, belge_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (satir["product_id"], ters_yon, satir["miktar"], zaman,
                 not_metni, belge_id)
            )

        # Belgeyi iptal olarak işaretle ve sebebi açıklamaya ekle
        yeni_aciklama = (belge["aciklama"] or "")
        if sebep:
            yeni_aciklama = (yeni_aciklama + " | " if yeni_aciklama else "")
            yeni_aciklama += f"İptal sebebi: {sebep.strip()[:120]}"

        conn.execute(
            "UPDATE belgeler SET durum = 'iptal', aciklama = ? WHERE id = ?",
            (yeni_aciklama or None, belge_id))

        conn.commit()
        return belge["belge_no"], len(satirlar)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def belge_iptal_edilebilir_mi(belge_id):
    """İptal önizlemesi: edilebilir mi, edilemezse neden?

    Onay ekranında kullanıcıya önceden söylemek için. "Evet, iptal et"
    dedikten sonra hata almak, baştan uyarılmaktan kötü bir deneyim.

    Döner: (edilebilir_mi, mesaj)
    """
    belge = get_belge(belge_id)
    if not belge:
        return False, "Belge bulunamadı."
    if belge["durum"] == "iptal":
        return False, "Bu belge zaten iptal edilmiş."

    ilk = _one("SELECT type FROM stock_movements WHERE belge_id = ? LIMIT 1",
               (belge_id,))
    if not ilk:
        return False, "Belgeye bağlı hareket yok."

    if ilk["type"] == "IN":
        for satir in get_belge_satirlari(belge_id):
            mevcut = get_product_stock(satir["product_id"])
            if satir["miktar"] > mevcut:
                return False, (
                    f"'{satir['part_code']}' için stok yetersiz "
                    f"(fişte {satir['miktar']}, depoda {mevcut}). "
                    f"Bu mal kısmen çıkış yapmış."
                )
    return True, ""


def get_user(user_id):
    """Tek kullanıcı (parola özeti hariç)."""
    return _one("SELECT id, kullanici_adi, ad_soyad, rol, aktif, olusturma "
                "FROM users WHERE id = ?", (user_id,))


def update_user(user_id, ad_soyad, rol):
    """Kullanıcının adını ve rolünü günceller. Kullanıcı adı değişmez —
    o kimlik gibidir; değişirse geçmiş belgelerdeki 'kim yaptı' izi
    okunması zorlaşır."""
    from auth import ROLLER

    ad_soyad = (ad_soyad or "").strip()
    if not ad_soyad:
        raise ValueError("Ad soyad zorunlu.")
    if rol not in ROLLER:
        raise ValueError("Geçersiz rol.")
    if not get_user(user_id):
        raise ValueError("Kullanıcı bulunamadı.")

    conn = get_connection()
    try:
        conn.execute("UPDATE users SET ad_soyad = ?, rol = ? WHERE id = ?",
                     (ad_soyad, rol, user_id))
        conn.commit()
    finally:
        conn.close()


def user_belge_sayisi(user_id):
    """Kullanıcının oluşturduğu belge sayısı — pasife almadan önce
    ne kadar iz bıraktığını göstermek için."""
    row = _one("SELECT COUNT(*) AS n FROM belgeler WHERE user_id = ?", (user_id,))
    return row["n"] if row else 0


# --- Stok seyri ------------------------------------------------------------

def stok_seyri(gun_sayisi=180):
    """Günlük giriş/çıkış toplamları ve kümülatif stok.

    Grafiği çizmek için gereken veri. Hesabı SQL'e yaptırıyoruz:
    binlerce hareketi Python'a çekip orada toplamak yerine, veritabanı
    zaten bu iş için var.

    date(created_at) ile saat kısmı atılıyor, aynı günün hareketleri
    tek satırda toplanıyor.
    """
    gunler = _rows("""
        SELECT date(created_at) AS gun,
               SUM(CASE WHEN type = 'IN'  THEN quantity ELSE 0 END) AS giris,
               SUM(CASE WHEN type = 'OUT' THEN quantity ELSE 0 END) AS cikis
        FROM stock_movements
        GROUP BY date(created_at)
        ORDER BY gun
    """)

    # Kümülatif stok: her günün sonunda depoda ne kadar var.
    # Günlük giriş-çıkış farkını üst üste ekleyerek buluyoruz.
    toplam = 0
    for g in gunler:
        toplam += g["giris"] - g["cikis"]
        g["stok"] = toplam

    return gunler[-gun_sayisi:] if gun_sayisi else gunler


def aylik_ozet():
    """Ay bazında giriş/çıkış — tablo için."""
    return _rows("""
        SELECT strftime('%Y-%m', created_at) AS ay,
               SUM(CASE WHEN type = 'IN'  THEN quantity ELSE 0 END) AS giris,
               SUM(CASE WHEN type = 'OUT' THEN quantity ELSE 0 END) AS cikis,
               COUNT(*) AS hareket_sayisi
        FROM stock_movements
        GROUP BY ay ORDER BY ay DESC
    """)


# --- İade faturası yardımcıları --------------------------------------------

def iade_edilebilir_satirlar(satis_belge_id):
    """Bir satış faturasının satırları + her biri için kalan iade hakkı.

    Ekranda kullanıcıya "bu üründen en fazla şu kadar iade edebilirsin"
    diyebilmek için. Kural kontrolü belge_olustur içinde zaten var; bu
    fonksiyon sadece kullanıcıyı önden bilgilendiriyor — formu doldurup
    kaydettikten sonra hata almak, baştan uyarılmaktan kötü bir deneyim.
    """
    return _rows("""
        SELECT s.product_id, p.part_code, p.name, s.birim_fiyat,
               s.miktar AS satilan,
               COALESCE((
                   SELECT SUM(si.miktar)
                   FROM belge_satirlari si
                   JOIN belgeler bi ON bi.id = si.belge_id
                   WHERE bi.tip = 'iade' AND bi.durum = 'onayli'
                     AND bi.kaynak_belge_id = s.belge_id
                     AND si.product_id = s.product_id
               ), 0) AS iade_edilen
        FROM belge_satirlari s
        JOIN products p ON p.id = s.product_id
        WHERE s.belge_id = ?
        ORDER BY s.id
    """, (satis_belge_id,))


def iade_edilebilir_faturalar(limit=200):
    """İade kesilebilecek satış faturaları.

    İptal edilmiş faturalar listeye girmiyor — iptal edilmiş bir
    satıştan iade alınamaz, o satış zaten hiç olmamış sayılıyor.
    """
    return _rows("""
        SELECT b.id, b.belge_no, b.tarih, m.unvan AS musteri_unvan,
               b.musteri_id,
               COUNT(s.id) AS satir_sayisi,
               COALESCE(SUM(s.miktar * s.birim_fiyat), 0) AS toplam_tutar
        FROM belgeler b
        JOIN musteriler m ON m.id = b.musteri_id
        LEFT JOIN belge_satirlari s ON s.belge_id = b.id
        WHERE b.tip = 'satis' AND b.durum = 'onayli'
        GROUP BY b.id
        ORDER BY b.id DESC LIMIT ?
    """, (limit,))


def get_belge_iadeleri(satis_belge_id):
    """Bir satış faturasına kesilmiş iade faturaları — belge detayında
    'bu faturadan şunlar iade edildi' diye göstermek için."""
    return _rows("""
        SELECT b.id, b.belge_no, b.tarih, b.durum,
               COALESCE(SUM(s.miktar), 0) AS toplam_miktar,
               COALESCE(SUM(s.miktar * s.birim_fiyat), 0) AS toplam_tutar
        FROM belgeler b
        LEFT JOIN belge_satirlari s ON s.belge_id = b.id
        WHERE b.tip = 'iade' AND b.kaynak_belge_id = ?
        GROUP BY b.id ORDER BY b.id
    """, (satis_belge_id,))
