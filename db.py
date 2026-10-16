"""
db.py — Veritabanı bağlantısı ve tablo şeması.

Bu dosyanın tek işi: veritabanına bağlanmak ve tabloları oluşturmak.
Başka hiçbir yerde sqlite3.connect() çağırmıyoruz, hep buradan geçiyoruz.
"""

import sqlite3
from pathlib import Path

# __file__ = bu dosyanın yolu. .parent = içinde bulunduğu klasör.
# Böylece programı hangi klasörden çalıştırırsan çalıştır, veritabanını bulur.
DB_PATH = Path(__file__).parent / "stok.db"


def get_connection():
    """Veritabanı bağlantısı döndürür."""
    conn = sqlite3.connect(DB_PATH)

    # row_factory: sorgu sonuçlarına indeks yerine kolon adıyla erişmemizi sağlar.
    # Bu satır olmadan: row[0]  ->  Bu satırla: row["part_code"]
    conn.row_factory = sqlite3.Row

    # SQLite'ta foreign key kontrolü VARSAYILAN OLARAK KAPALI gelir.
    # Açmazsan, olmayan bir product_id'ye hareket kaydı ekleyebilirsin ve
    # veritabanı sana hiç şikayet etmez. Her bağlantıda açmak gerekiyor.
    conn.execute("PRAGMA foreign_keys = ON")

    # WAL (Write-Ahead Logging) modu.
    #
    # SQLite'ın varsayılan modunda bir yazma işlemi TÜM okuyucuları da
    # bekletir. Tek kullanıcıda fark edilmez; üç kişi aynı anda çalışınca
    # "database is locked" hataları başlar. WAL modunda okuyucular yazma
    # sırasında beklemez, yazıcı da okuyucuları beklemez.
    #
    # Bu ayar veritabanı DOSYASINA yazılır, yani bir kez uygulanır ve
    # kalıcı olur. Yine de her bağlantıda çağırmak zararsız.
    #
    # Not: busy_timeout'u ayrıca ayarlamaya gerek yok — Python'un
    # sqlite3.connect() fonksiyonu varsayılan olarak 5 saniyeye ayarlıyor.
    # (Kontrol etmek için: PRAGMA busy_timeout)
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def init_db():
    """Tabloları oluşturur. Zaten varsa hiçbir şey yapmaz."""
    conn = get_connection()

    # IF NOT EXISTS: tablo varsa hata vermeden geç.
    # Bu sayede init_db()'yi kaç kere çağırırsan çağır sorun olmaz.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT    NOT NULL UNIQUE,
            ad_soyad      TEXT    NOT NULL,
            rol           TEXT    NOT NULL
                          CHECK (rol IN ('depo', 'satis', 'yonetici')),
            parola_ozeti  TEXT    NOT NULL,
            aktif         INTEGER NOT NULL DEFAULT 1,
            olusturma     TEXT    NOT NULL
        );

        -- Parola kolonunun adı bilinçli olarak "parola" değil
        -- "parola_ozeti". Kolonun adı bile içinde ne olduğunu söylüyor:
        -- burada parola yok, parolanın tek yönlü özeti var.
        --
        -- aktif: kullanıcıyı silmek yerine pasife alıyoruz. Sildiğinde
        -- o kullanıcının geçmişte oluşturduğu belgeler sahipsiz kalır.

        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            part_code   TEXT    NOT NULL UNIQUE,
            oem_code    TEXT,
            name        TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            vehicle_make  TEXT,
            vehicle_model TEXT,
            unit_price  REAL    NOT NULL DEFAULT 0,
            min_stock   INTEGER NOT NULL DEFAULT 0,
            location    TEXT
        );

        -- OTURUMLAR
        -- Daha önce bellekte bir sözlükte tutuluyordu; sunucu her
        -- yeniden başladığında herkes çıkış yapmış oluyordu. Üç kişi
        -- çalışırken sen kodu güncellemek için sunucuyu durdurduğunda
        -- ikisi formun ortasında atılıyordu.
        --
        -- Veritabanında tutunca oturumlar sunucu yeniden başlasa da
        -- kalıyor. Yan fayda: yönetici kimin açık oturumu olduğunu
        -- gerçekten görebiliyor, gerekirse sonlandırabiliyor.
        CREATE TABLE IF NOT EXISTS oturumlar (
            jeton       TEXT    PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            olusturma   TEXT    NOT NULL,
            son_erisim  TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_oturum_user ON oturumlar(user_id);

        -- MÜŞTERİLER
        -- Satış faturası kesmek için gerekli. Bayi, OEM müşterisi,
        -- ihracat müşterisi gibi türleri var — fatura üzerinde
        -- görünmese de raporlamada işe yarar.
        CREATE TABLE IF NOT EXISTS musteriler (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            unvan    TEXT    NOT NULL,
            tur      TEXT    NOT NULL DEFAULT 'bayi'
                     CHECK (tur IN ('bayi', 'oem', 'ihracat', 'perakende')),
            sehir    TEXT,
            ulke     TEXT    NOT NULL DEFAULT 'Türkiye',
            vergi_no TEXT,
            telefon  TEXT,
            aktif    INTEGER NOT NULL DEFAULT 1
        );

        -- BELGELER
        -- Gerçek stok sistemlerinde hiçbir hareket belgesiz oluşmaz.
        -- "Bu 40 adet nereden geldi?" sorusunun cevabı bir belge numarasıdır.
        -- Bizde de artık her stok hareketi bir belgeye bağlı.
        CREATE TABLE IF NOT EXISTS belgeler (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            belge_no  TEXT    NOT NULL UNIQUE,
            tip       TEXT    NOT NULL
                      CHECK (tip IN ('uretim', 'satis', 'iade', 'sayim', 'acilis')),
            tarih     TEXT    NOT NULL,
            user_id   INTEGER NOT NULL,
            musteri_id INTEGER,          -- satış faturası ve iade faturasında dolu
            sebep     TEXT,              -- yalnızca düzeltme fişinde dolu
            -- İade faturasının hangi satış faturasına ait olduğu.
            -- Bu alan olmadan "bu iade hangi satıştan" sorusunun cevabı
            -- olmaz; iade tek başına, bağlamsız bir kayıt olarak kalırdı.
            kaynak_belge_id INTEGER,     -- yalnızca iade faturasında dolu
            aciklama  TEXT,
            durum     TEXT    NOT NULL DEFAULT 'onayli'
                      CHECK (durum IN ('onayli', 'iptal')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (musteri_id) REFERENCES musteriler(id),
            FOREIGN KEY (kaynak_belge_id) REFERENCES belgeler(id)
        );

        -- Belgenin kalemleri. Bir üretim fişinde beş farklı ürün olabilir.
        -- birim_fiyat burada TEKRAR saklanıyor — products tablosunda zaten
        -- var olmasına rağmen. Sebebi: fiyat zamanla değişir, ama geçmiş
        -- bir belgenin tutarı değişmemeli. Belge kesildiği andaki fiyatı
        -- dondurup saklıyoruz. Buna "tarihsel veri" (point-in-time) denir
        -- ve faturalarda zorunludur.
        CREATE TABLE IF NOT EXISTS belge_satirlari (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            belge_id    INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            miktar      INTEGER NOT NULL CHECK (miktar > 0),
            birim_fiyat REAL    NOT NULL DEFAULT 0,
            FOREIGN KEY (belge_id) REFERENCES belgeler(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE INDEX IF NOT EXISTS idx_satir_belge
            ON belge_satirlari(belge_id);
        CREATE INDEX IF NOT EXISTS idx_belge_tip
            ON belgeler(tip, tarih);

        CREATE TABLE IF NOT EXISTS stock_movements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL,
            type        TEXT    NOT NULL CHECK (type IN ('IN', 'OUT')),
            quantity    INTEGER NOT NULL CHECK (quantity > 0),
            created_at  TEXT    NOT NULL,
            note        TEXT,
            belge_id    INTEGER,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (belge_id) REFERENCES belgeler(id)
        );

        -- Index: hareketleri sürekli product_id'ye göre filtreleyip
        -- toplayacağız. Index olmadan SQLite her seferinde tüm tabloyu
        -- baştan sona tarar. 500 satırda farkı görmezsin, 500.000'de görürsün.
        CREATE INDEX IF NOT EXISTS idx_movements_product
            ON stock_movements(product_id);

        CREATE INDEX IF NOT EXISTS idx_products_category
            ON products(category);
    """)

    conn.commit()  # Değişiklikleri diske yaz. Bunu unutursan hiçbir şey kaydedilmez.
    conn.close()


# Bu dosya doğrudan çalıştırılırsa (python db.py) tabloları kur.
# Başka bir dosya import ederse bu blok çalışmaz.
if __name__ == "__main__":
    init_db()
    print(f"Veritabanı hazır: {DB_PATH}")
