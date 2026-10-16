"""
testler.py — Uygulamanın kritik davranışlarını otomatik doğrular.

    python testler.py

NEDEN VAR
    Her yeni özellik eklediğimizde elle test yazıp attık. Bu dosya
    onların kalıcı hali. Kodu değiştirdiğinde bir şeyi bozup bozmadığını
    tıklayarak değil, tek komutla anlarsın.

    Gerçek projelerde bu testler her değişiklikte otomatik çalışır ve
    geçmeyen değişiklik birleştirilemez. Yazılım geliştirmenin en temel
    alışkanlığı budur.

NASIL ÇALIŞIR
    Kendi geçici veritabanını kurar, testleri çalıştırır, siler.
    Senin stok.db dosyana DOKUNMAZ.

    unittest Python'un içinde gelir; ayrı kurulum gerekmez.
"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

# --- Testler geçici bir veritabanında çalışsın ---
# db modülü içe aktarılmadan ÖNCE yolu değiştiriyoruz, çünkü DB_PATH
# modül yüklenirken bir kez hesaplanıyor.
_gecici = Path(tempfile.mkdtemp()) / "test.db"

import db
db.DB_PATH = _gecici

import queries as q
import auth
import exports
import seed


def veritabanini_kur():
    """Her test için sıfırdan küçük bir veri seti."""
    db.init_db()

    conn = db.get_connection()
    try:
        # Sıra önemli: önce çocuk tablolar, sonra ana tablolar.
        # oturumlar da users'a bağlı — listede olmadığında DELETE FROM
        # users foreign key hatası veriyor, arkasından bağlantı açık
        # kaldığı için "database is locked" geliyordu.
        for tablo in ("stock_movements", "belge_satirlari", "belgeler",
                      "oturumlar", "products", "musteriler", "users"):
            conn.execute(f"DELETE FROM {tablo}")
        conn.commit()
    finally:
        # finally şart: hata çıksa bile bağlantı kapanmalı, yoksa
        # kilit kalır ve sonraki testlerin hepsi patlar.
        conn.close()

    q.add_user("depo", "Test Depocu", "depo", "depo12345")
    q.add_user("satis", "Test Satisci", "satis", "satis12345")
    q.add_user("admin", "Test Yonetici", "yonetici", "admin12345")

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO musteriler (unvan, tur, sehir) VALUES (?, 'bayi', 'Konya')",
            ("Test Bayi Ltd.",))
        conn.executemany("""
            INSERT INTO products
              (part_code, oem_code, name, category, vehicle_make, vehicle_model,
               unit_price, min_stock, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            ("TS-001", "OEM1", "Test Rotil", "Rotil",
             "Ford", "Focus", 100.0, 20, "Depo-A-Raf-01"),
            ("TS-002", "OEM2", "Test Salincak", "Rotilli Salıncak",
             "Ford", "Transit", 250.0, 10, "Depo-A-Raf-02"),
            ("TS-003", "OEM3", "Test Z-Rot", "Z-Rot",
             "Renault", "Clio", 80.0, 50, "Depo-B-Raf-01"),
        ])
        conn.commit()
    finally:
        conn.close()


def kimlikler():
    kullanicilar = {u["kullanici_adi"]: u["id"] for u in q.list_users()}
    urunler = {p["part_code"]: p["id"] for p in q.get_products()}
    musteri = q.list_musteriler()[0]["id"]
    return kullanicilar, urunler, musteri


class TemelTest(unittest.TestCase):
    """Her testten önce veritabanını sıfırlayan ortak sınıf."""

    def setUp(self):
        veritabanini_kur()
        self.kullanici, self.urun, self.musteri = kimlikler()


# ===========================================================================

class ParolaTestleri(TemelTest):

    def test_dogru_parola_gecer(self):
        u = q.get_user_by_username("depo")
        self.assertTrue(auth.parola_dogrula("depo12345", u["parola_ozeti"]))

    def test_yanlis_parola_gecmez(self):
        u = q.get_user_by_username("depo")
        self.assertFalse(auth.parola_dogrula("yanlis", u["parola_ozeti"]))

    def test_parola_duz_metin_saklanmaz(self):
        u = q.get_user_by_username("depo")
        self.assertNotIn("depo12345", u["parola_ozeti"])

    def test_ayni_parola_farkli_ozet(self):
        """Tuz sayesinde aynı parola her seferinde farklı özet üretmeli."""
        self.assertNotEqual(auth.parola_ozeti("ayni"), auth.parola_ozeti("ayni"))

    def test_kapali_hesap_bulunamaz(self):
        q.set_user_active(self.kullanici["depo"], False)
        self.assertIsNone(q.get_user_by_username("depo"))


class YetkiTestleri(TemelTest):

    def test_depo_uretim_kesebilir_satis_kesemez(self):
        self.assertTrue(auth.yetkili_mi("depo", "/uretim-fisi"))
        self.assertFalse(auth.yetkili_mi("depo", "/satis-faturasi"))

    def test_satis_fatura_kesebilir_uretim_kesemez(self):
        self.assertTrue(auth.yetkili_mi("satis", "/satis-faturasi"))
        self.assertFalse(auth.yetkili_mi("satis", "/uretim-fisi"))

    def test_yonetici_her_yere_girer(self):
        for yol in auth.YETKILER:
            self.assertTrue(auth.yetkili_mi("yonetici", yol), yol)

    def test_depo_fiyat_goremez(self):
        self.assertFalse(auth.fiyat_gorebilir("depo"))
        self.assertTrue(auth.fiyat_gorebilir("satis"))

    def test_giris_yapmamis_hicbir_yere_giremez(self):
        self.assertFalse(auth.yetkili_mi(None, "/hareket"))


class BelgeTestleri(TemelTest):

    def test_uretim_fisi_stogu_artirir(self):
        pid = self.urun["TS-001"]
        once = q.get_product_stock(pid)
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 40}])
        self.assertEqual(q.get_product_stock(pid), once + 40)

    def test_satis_stogu_azaltir(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}])
        q.belge_olustur("satis", self.kullanici["satis"],
                        [{"product_id": pid, "miktar": 30}],
                        musteri_id=self.musteri)
        self.assertEqual(q.get_product_stock(pid), 70)

    def test_yetersiz_stok_reddedilir(self):
        pid = self.urun["TS-001"]
        with self.assertRaises(ValueError):
            q.belge_olustur("satis", self.kullanici["satis"],
                            [{"product_id": pid, "miktar": 5}],
                            musteri_id=self.musteri)

    def test_satis_musterisiz_kesilemez(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 50}])
        with self.assertRaises(ValueError):
            q.belge_olustur("satis", self.kullanici["satis"],
                            [{"product_id": pid, "miktar": 5}])

    def test_ayni_urun_iki_satirda_olamaz(self):
        pid = self.urun["TS-001"]
        with self.assertRaises(ValueError):
            q.belge_olustur("uretim", self.kullanici["depo"], [
                {"product_id": pid, "miktar": 10},
                {"product_id": pid, "miktar": 5},
            ])

    def test_bos_belge_olusturulamaz(self):
        with self.assertRaises(ValueError):
            q.belge_olustur("uretim", self.kullanici["depo"], [])

    def test_negatif_miktar_reddedilir(self):
        with self.assertRaises(ValueError):
            q.belge_olustur("uretim", self.kullanici["depo"],
                            [{"product_id": self.urun["TS-001"], "miktar": -5}])

    def test_hicbir_hareket_belgesiz_olmaz(self):
        """Sistemin temel kuralı: her hareket bir belgeye bağlı."""
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": self.urun["TS-001"], "miktar": 10}])
        q.duzeltme_fisi(self.kullanici["depo"], self.urun["TS-002"],
                        "sayim_fazlasi", 5)
        conn = db.get_connection()
        sayi = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE belge_id IS NULL"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(sayi, 0)

    def test_belge_numaralari_sirali(self):
        pid = self.urun["TS-001"]
        _, no1 = q.belge_olustur("uretim", self.kullanici["depo"],
                                 [{"product_id": pid, "miktar": 10}])
        _, no2 = q.belge_olustur("uretim", self.kullanici["depo"],
                                 [{"product_id": pid, "miktar": 10}])
        self.assertTrue(no1.endswith("0001"), no1)
        self.assertTrue(no2.endswith("0002"), no2)

    def test_belge_fiyati_dondurulur(self):
        """Ürün fiyatı sonradan değişse bile belgedeki tutar değişmemeli."""
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 50}])
        bid, _ = q.belge_olustur("satis", self.kullanici["satis"],
                                 [{"product_id": pid, "miktar": 10}],
                                 musteri_id=self.musteri)
        eski_tutar = q.get_belge_satirlari(bid)[0]["tutar"]

        q.update_product(pid, {"part_code": "TS-001", "name": "Test Rotil",
                               "category": "Rotil", "unit_price": "9999",
                               "min_stock": "20"})
        self.assertEqual(q.get_belge_satirlari(bid)[0]["tutar"], eski_tutar)


class DuzeltmeTestleri(TemelTest):

    def test_sebep_listeden_secilmeli(self):
        with self.assertRaises(ValueError):
            q.duzeltme_fisi(self.kullanici["depo"], self.urun["TS-001"],
                            "bayi_satisi", 5)

    def test_sebep_yonu_belirler(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}])
        q.duzeltme_fisi(self.kullanici["depo"], pid, "sayim_fazlasi", 10)
        self.assertEqual(q.get_product_stock(pid), 110)
        q.duzeltme_fisi(self.kullanici["depo"], pid, "fire", 20)
        self.assertEqual(q.get_product_stock(pid), 90)


class IptalTestleri(TemelTest):

    def test_iptal_stogu_geri_alir(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}])
        once = q.get_product_stock(pid)
        bid, _ = q.belge_olustur("satis", self.kullanici["satis"],
                                 [{"product_id": pid, "miktar": 30}],
                                 musteri_id=self.musteri)
        self.assertEqual(q.get_product_stock(pid), once - 30)
        q.belge_iptal(bid, self.kullanici["admin"], "test")
        self.assertEqual(q.get_product_stock(pid), once)

    def test_iptal_belgeyi_silmez(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}])
        bid, no = q.belge_olustur("satis", self.kullanici["satis"],
                                  [{"product_id": pid, "miktar": 10}],
                                  musteri_id=self.musteri)
        q.belge_iptal(bid, self.kullanici["admin"], "test")
        belge = q.get_belge(bid)
        self.assertIsNotNone(belge)
        self.assertEqual(belge["durum"], "iptal")
        self.assertEqual(belge["belge_no"], no)

    def test_iki_kez_iptal_edilemez(self):
        pid = self.urun["TS-001"]
        bid, _ = q.belge_olustur("uretim", self.kullanici["depo"],
                                 [{"product_id": pid, "miktar": 40}])
        q.belge_iptal(bid, self.kullanici["admin"], "test")
        with self.assertRaises(ValueError):
            q.belge_iptal(bid, self.kullanici["admin"], "tekrar")

    def test_satilmis_uretim_fisi_iptal_edilemez(self):
        """Üretilen mal satılmışsa üretim fişi iptal edilemez —
        yoksa stok eksiye düşerdi."""
        pid = self.urun["TS-001"]
        bid, _ = q.belge_olustur("uretim", self.kullanici["depo"],
                                 [{"product_id": pid, "miktar": 50}])
        q.belge_olustur("satis", self.kullanici["satis"],
                        [{"product_id": pid, "miktar": 45}],
                        musteri_id=self.musteri)
        edilebilir, _ = q.belge_iptal_edilebilir_mi(bid)
        self.assertFalse(edilebilir)
        with self.assertRaises(ValueError):
            q.belge_iptal(bid, self.kullanici["admin"], "test")


class EszamanlilikTestleri(TemelTest):
    """Bu sınıf projedeki en önemli hatayı bekliyor.

    Düzeltmeden önce üç eşzamanlı satış stoğu 152'den -121'e düşürüyordu.
    """

    def test_es_zamanli_satis_stogu_negatife_dusurmez(self):
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}])

        # Doğrulama ile yazma arasındaki pencereyi genişlet — gerçek bir
        # HTTP isteğinde ağ gecikmesi zaten bu kadar sürer.
        gercek = q.get_product

        def yavas(urun_id):
            u = gercek(urun_id)
            time.sleep(0.05)
            return u

        q.get_product = yavas
        try:
            sonuclar = []
            bariyer = threading.Barrier(3)

            def sat():
                bariyer.wait()
                try:
                    q.belge_olustur("satis", self.kullanici["satis"],
                                    [{"product_id": pid, "miktar": 60}],
                                    musteri_id=self.musteri)
                    sonuclar.append("ok")
                except ValueError:
                    sonuclar.append("red")
                except Exception as e:
                    sonuclar.append(f"cokme:{type(e).__name__}")

            islemler = [threading.Thread(target=sat) for _ in range(3)]
            for i in islemler:
                i.start()
            for i in islemler:
                i.join()
        finally:
            q.get_product = gercek

        self.assertGreaterEqual(q.get_product_stock(pid), 0,
                                "Stok negatife düştü — yarış durumu var")
        self.assertEqual(sonuclar.count("ok"), 1,
                         f"Sadece biri geçmeliydi: {sonuclar}")
        self.assertEqual([s for s in sonuclar if s.startswith("cokme")], [],
                         f"Beklenmedik çökme: {sonuclar}")

    def test_es_zamanli_belge_numarasi_catismaz(self):
        pid = self.urun["TS-001"]
        hatalar = []
        bariyer = threading.Barrier(4)

        def fis_kes():
            bariyer.wait()
            try:
                q.belge_olustur("uretim", self.kullanici["depo"],
                                [{"product_id": pid, "miktar": 5}])
            except Exception as e:
                hatalar.append(f"{type(e).__name__}: {e}")

        islemler = [threading.Thread(target=fis_kes) for _ in range(4)]
        for i in islemler:
            i.start()
        for i in islemler:
            i.join()

        self.assertEqual(hatalar, [], "Belge numarası çakıştı")
        self.assertEqual(len(q.list_belgeler(tip="uretim")), 4)


class HesaplamaTestleri(TemelTest):

    def test_kritik_stok_dogru_secilir(self):
        q.belge_olustur("uretim", self.kullanici["depo"], [
            {"product_id": self.urun["TS-001"], "miktar": 5},    # min 20 -> kritik
            {"product_id": self.urun["TS-002"], "miktar": 100},  # min 10 -> yeterli
        ])
        kodlar = [r["part_code"] for r in q.get_critical_products()]
        self.assertIn("TS-001", kodlar)
        self.assertNotIn("TS-002", kodlar)

    def test_hicbir_hareketi_olmayan_urun_kaybolmaz(self):
        """LEFT JOIN kontrolü: stoğu olmayan ürün de listede kalmalı."""
        kodlar = [r["part_code"] for r in q.get_products()]
        self.assertIn("TS-003", kodlar)
        self.assertEqual(q.get_product_stock(self.urun["TS-003"]), 0)

    def test_depo_gruplamasi(self):
        depolar = {d["depo"]: d["cesit"] for d in q.get_warehouse_summary()}
        self.assertEqual(depolar.get("A"), 2)
        self.assertEqual(depolar.get("B"), 1)

    def test_arama_uc_kolonda_calisir(self):
        self.assertEqual(len(q.get_products(search="TS-001")), 1)
        self.assertEqual(len(q.get_products(search="Salincak")), 1)
        self.assertEqual(len(q.get_products(search="OEM3")), 1)


class UrunTestleri(TemelTest):

    def test_ayni_parca_kodu_iki_kez_eklenemez(self):
        with self.assertRaises(ValueError):
            q.add_product({"part_code": "TS-001", "name": "Kopya",
                           "category": "Rotil"})

    def test_zorunlu_alanlar(self):
        with self.assertRaises(ValueError):
            q.add_product({"part_code": "", "name": "X", "category": "Y"})
        with self.assertRaises(ValueError):
            q.add_product({"part_code": "X1", "name": "", "category": "Y"})

    def test_turkce_fiyat_bicimi(self):
        pid = q.add_product({"part_code": "TS-900", "name": "Fiyat testi",
                             "category": "Rotil", "unit_price": "1.250,50",
                             "min_stock": "10"})
        self.assertEqual(q.get_product(pid)["unit_price"], 1250.50)

    def test_negatif_fiyat_reddedilir(self):
        with self.assertRaises(ValueError):
            q.add_product({"part_code": "TS-901", "name": "X",
                           "category": "Y", "unit_price": "-5"})

    def test_kullanilmamis_urun_silinebilir(self):
        pid = q.add_product({"part_code": "TS-999", "name": "Yanlislikla eklendi",
                             "category": "Rotil", "min_stock": "10"})
        q.delete_product(pid)
        self.assertIsNone(q.get_product(pid))

    def test_belgede_gecen_urun_silinemez(self):
        """Ürün bir faturada geçiyorsa silinemez — yoksa fatura
        satır kaybeder ve geçmiş bozulur."""
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 10}])
        with self.assertRaises(ValueError) as h:
            q.delete_product(pid)
        self.assertIn("belgede geçiyor", str(h.exception))
        self.assertIsNotNone(q.get_product(pid))


class DisaAktarimTestleri(TemelTest):

    BASLIKLAR = [("part_code", "Parça Kodu"), ("name", "Ürün Adı"),
                 ("stock", "Stok"), ("unit_price", "Birim Fiyat")]

    def test_csv_turkce_karakter(self):
        veri = exports.to_csv(q.get_products(), self.BASLIKLAR)
        self.assertTrue(veri.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM yok")
        self.assertIn("Salincak", veri.decode("utf-8-sig"))

    def test_xlsx_gecerli_zip(self):
        import zipfile
        import io
        veri = exports.to_xlsx([("Ürünler", q.get_products(), self.BASLIKLAR)])
        with zipfile.ZipFile(io.BytesIO(veri)) as z:
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
            self.assertIn("[Content_Types].xml", z.namelist())


class GirisSiniriTestleri(TemelTest):

    def setUp(self):
        super().setUp()
        auth.denemeleri_sifirla("depo")

    def tearDown(self):
        auth.denemeleri_sifirla("depo")

    def test_sinira_kadar_kilitlenmez(self):
        for _ in range(auth.DENEME_SINIRI - 1):
            auth.basarisiz_deneme("depo")
        self.assertEqual(auth.kilitli_mi("depo"), 0)

    def test_sinir_asilinca_kilitlenir(self):
        for _ in range(auth.DENEME_SINIRI):
            auth.basarisiz_deneme("depo")
        self.assertGreater(auth.kilitli_mi("depo"), 0)

    def test_basarili_giris_sayaci_sifirlar(self):
        for _ in range(auth.DENEME_SINIRI - 1):
            auth.basarisiz_deneme("depo")
        auth.denemeleri_sifirla("depo")
        for _ in range(auth.DENEME_SINIRI - 1):
            auth.basarisiz_deneme("depo")
        self.assertEqual(auth.kilitli_mi("depo"), 0)

    def test_bir_hesabin_kilidi_digerini_etkilemez(self):
        for _ in range(auth.DENEME_SINIRI):
            auth.basarisiz_deneme("depo")
        self.assertEqual(auth.kilitli_mi("satis"), 0)


class OturumTestleri(TemelTest):

    def test_oturum_acilir_ve_bulunur(self):
        u = q.get_user_by_username("depo")
        jeton = auth.oturum_ac(u)
        oturum = auth.oturum_getir(jeton)
        self.assertIsNotNone(oturum)
        self.assertEqual(oturum["kullanici_adi"], "depo")
        self.assertEqual(oturum["rol"], "depo")

    def test_oturum_veritabaninda_saklanir(self):
        """Bellekte olsaydı sunucu yeniden başlayınca kaybolurdu."""
        u = q.get_user_by_username("depo")
        auth.oturum_ac(u)
        conn = db.get_connection()
        sayi = conn.execute("SELECT COUNT(*) FROM oturumlar").fetchone()[0]
        conn.close()
        self.assertEqual(sayi, 1)

    def test_cikis_oturumu_siler(self):
        u = q.get_user_by_username("depo")
        jeton = auth.oturum_ac(u)
        auth.oturum_kapat(jeton)
        self.assertIsNone(auth.oturum_getir(jeton))

    def test_gecersiz_jeton_kabul_edilmez(self):
        self.assertIsNone(auth.oturum_getir("uydurma-jeton"))
        self.assertIsNone(auth.oturum_getir(""))
        self.assertIsNone(auth.oturum_getir(None))

    def test_hesap_kapatilinca_oturum_duser(self):
        """Yönetici birini pasife alınca o kişi çalışmaya devam edememeli."""
        u = q.get_user_by_username("depo")
        jeton = auth.oturum_ac(u)
        q.set_user_active(u["id"], False)
        self.assertIsNone(auth.oturum_getir(jeton))

    def test_jetonlar_benzersiz(self):
        u = q.get_user_by_username("depo")
        jetonlar = {auth.oturum_ac(u) for _ in range(20)}
        self.assertEqual(len(jetonlar), 20)


class CsrfTestleri(TemelTest):

    def test_dogru_jeton_gecerli(self):
        jeton = auth.csrf_jetonu("oturum-abc")
        self.assertTrue(auth.csrf_dogrula("oturum-abc", jeton))

    def test_yanlis_jeton_gecersiz(self):
        self.assertFalse(auth.csrf_dogrula("oturum-abc", "sahte"))

    def test_baska_oturumun_jetonu_gecersiz(self):
        jeton = auth.csrf_jetonu("oturum-abc")
        self.assertFalse(auth.csrf_dogrula("oturum-xyz", jeton))

    def test_bos_deger_gecersiz(self):
        jeton = auth.csrf_jetonu("oturum-abc")
        self.assertFalse(auth.csrf_dogrula("", jeton))
        self.assertFalse(auth.csrf_dogrula("oturum-abc", ""))


class BelgeAramaTestleri(TemelTest):

    def setUp(self):
        super().setUp()
        pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": pid, "miktar": 100}],
                        aciklama="Gece vardiyasi", tarih="2026-03-15 10:00:00")
        q.belge_olustur("satis", self.kullanici["satis"],
                        [{"product_id": pid, "miktar": 10}],
                        musteri_id=self.musteri, aciklama="Sevkiyat",
                        tarih="2026-06-20 14:00:00")

    def test_belge_numarasiyla_arama(self):
        self.assertEqual(len(q.list_belgeler(arama="SF-")), 1)
        self.assertEqual(len(q.list_belgeler(arama="ÜF-")), 1)

    def test_aciklamayla_arama(self):
        self.assertEqual(len(q.list_belgeler(arama="vardiyasi")), 1)

    def test_musteri_unvaniyla_arama(self):
        sonuc = q.list_belgeler(arama="Test Bayi")
        self.assertEqual(len(sonuc), 1)
        self.assertEqual(sonuc[0]["tip"], "satis")

    def test_tarih_araligi(self):
        self.assertEqual(len(q.list_belgeler(bas_tarih="2026-06-01")), 1)
        self.assertEqual(len(q.list_belgeler(bit_tarih="2026-04-01")), 1)
        self.assertEqual(len(q.list_belgeler(bas_tarih="2026-01-01",
                                             bit_tarih="2026-12-31")), 2)

    def test_bitis_tarihi_gunun_tamamini_kapsar(self):
        """20.06 seçen kullanıcı o günün belgelerini de görmeli."""
        sonuc = q.list_belgeler(bas_tarih="2026-06-20", bit_tarih="2026-06-20")
        self.assertEqual(len(sonuc), 1)

    def test_filtreler_birlikte_calisir(self):
        self.assertEqual(len(q.list_belgeler(tip="satis", arama="Sevkiyat")), 1)
        self.assertEqual(len(q.list_belgeler(tip="uretim", arama="Sevkiyat")), 0)

    def test_durum_filtresi(self):
        bid = q.list_belgeler(tip="satis")[0]["id"]
        q.belge_iptal(bid, self.kullanici["admin"], "test")
        self.assertEqual(len(q.list_belgeler(durum="iptal")), 1)
        self.assertEqual(len(q.list_belgeler(durum="onayli")), 1)

    def test_sonucsuz_arama_bos_doner(self):
        self.assertEqual(q.list_belgeler(arama="boyle-bir-sey-yok"), [])


class CokSatirliBelgeTestleri(TemelTest):

    def test_sekizden_fazla_kalem(self):
        """Form 8 satırla açılıyor ama daha fazlası kaydedilebilmeli."""
        conn = db.get_connection()
        conn.executemany(
            "INSERT INTO products (part_code, name, category, unit_price, min_stock) "
            "VALUES (?, ?, 'Rotil', 100.0, 10)",
            [(f"TS-1{i:02d}", f"Ek urun {i}") for i in range(12)])
        conn.commit()
        conn.close()

        idler = [p["id"] for p in q.get_products() if p["part_code"].startswith("TS-1")]
        self.assertEqual(len(idler), 12)

        bid, _ = q.belge_olustur(
            "uretim", self.kullanici["depo"],
            [{"product_id": i, "miktar": 5} for i in idler])
        self.assertEqual(len(q.get_belge_satirlari(bid)), 12)
        for i in idler:
            self.assertEqual(q.get_product_stock(i), 5)


class IadeTestleri(TemelTest):
    """İade faturası — satıştan geri dönen mal."""

    def setUp(self):
        super().setUp()
        self.pid = self.urun["TS-001"]
        q.belge_olustur("uretim", self.kullanici["depo"],
                        [{"product_id": self.pid, "miktar": 100}])
        self.satis_id, _ = q.belge_olustur(
            "satis", self.kullanici["satis"],
            [{"product_id": self.pid, "miktar": 40}],
            musteri_id=self.musteri)

    def _iade(self, miktar, **kwargs):
        varsayilan = {"musteri_id": self.musteri,
                      "kaynak_belge_id": self.satis_id}
        varsayilan.update(kwargs)
        return q.belge_olustur(
            "iade", self.kullanici["satis"],
            [{"product_id": self.pid, "miktar": miktar}], **varsayilan)

    def test_iade_stogu_artirir(self):
        once = q.get_product_stock(self.pid)
        self._iade(10)
        self.assertEqual(q.get_product_stock(self.pid), once + 10)

    def test_iade_kaynak_faturaya_baglanir(self):
        bid, _ = self._iade(5)
        belge = q.get_belge(bid)
        self.assertEqual(belge["kaynak_belge_id"], self.satis_id)
        self.assertEqual(belge["tip"], "iade")

    def test_kaynaksiz_iade_kesilemez(self):
        with self.assertRaises(ValueError):
            self._iade(5, kaynak_belge_id=None)

    def test_musterisiz_iade_kesilemez(self):
        with self.assertRaises(ValueError):
            self._iade(5, musteri_id=None)

    def test_satilandan_fazla_iade_edilemez(self):
        """40 adet satıldıysa 41 adet iade edilemez."""
        with self.assertRaises(ValueError) as h:
            self._iade(41)
        self.assertIn("iade edilebilir miktar aşıldı", str(h.exception))

    def test_onceki_iadeler_hesaba_katilir(self):
        """40 satıldı, 30 iade edildi -> kalan hak 10."""
        self._iade(30)
        self._iade(10)   # tam sınırda, geçmeli
        with self.assertRaises(ValueError):
            self._iade(1)   # hak bitti

    def test_faturada_olmayan_urun_iade_edilemez(self):
        baska = self.urun["TS-002"]
        with self.assertRaises(ValueError) as h:
            q.belge_olustur("iade", self.kullanici["satis"],
                            [{"product_id": baska, "miktar": 1}],
                            musteri_id=self.musteri,
                            kaynak_belge_id=self.satis_id)
        self.assertIn("satılmamış", str(h.exception))

    def test_iptal_faturadan_iade_alinamaz(self):
        q.belge_iptal(self.satis_id, self.kullanici["admin"], "test")
        with self.assertRaises(ValueError) as h:
            self._iade(5)
        self.assertIn("iptal", str(h.exception).lower())

    def test_kaynak_uretim_fisi_olamaz(self):
        uf_id, _ = q.belge_olustur("uretim", self.kullanici["depo"],
                                   [{"product_id": self.pid, "miktar": 10}])
        with self.assertRaises(ValueError):
            self._iade(5, kaynak_belge_id=uf_id)

    def test_kalan_hak_hesabi(self):
        kalemler = q.iade_edilebilir_satirlar(self.satis_id)
        self.assertEqual(kalemler[0]["satilan"], 40)
        self.assertEqual(kalemler[0]["iade_edilen"], 0)
        self._iade(15)
        kalemler = q.iade_edilebilir_satirlar(self.satis_id)
        self.assertEqual(kalemler[0]["iade_edilen"], 15)

    def test_iade_de_iptal_edilebilir(self):
        """İade yanlış kesilirse iptal edilebilmeli; stok geri düşmeli."""
        once = q.get_product_stock(self.pid)
        bid, _ = self._iade(10)
        self.assertEqual(q.get_product_stock(self.pid), once + 10)
        q.belge_iptal(bid, self.kullanici["admin"], "hatalı iade")
        self.assertEqual(q.get_product_stock(self.pid), once)

    def test_iptal_iade_kalan_hakki_geri_verir(self):
        """İptal edilmiş iade, kalan hak hesabına dahil edilmemeli."""
        bid, _ = self._iade(40)          # hakkın tamamı
        q.belge_iptal(bid, self.kullanici["admin"], "hatalı")
        # iptal edildiğine göre yeniden 40 iade edilebilmeli
        self._iade(40)

    def test_duzeltme_sebeplerinde_iade_yok(self):
        """İadenin kendi belgesi olduğu için düzeltme listesinden çıktı."""
        kodlar = [k for k, _, _ in q.DUZELTME_SEBEPLERI]
        self.assertNotIn("iade_kabul", kodlar)


class YedeklemeTestleri(TemelTest):

    def test_yedek_alinir_ve_okunabilir(self):
        import yedekle
        klasor = _gecici.parent / "yedek_testi"
        hedef = yedekle.yedek_al(klasor)
        self.assertTrue(hedef.exists())

        # Yedek gerçekten okunabilir bir veritabanı mı?
        conn = sqlite3.connect(hedef)
        sayi = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        conn.close()
        self.assertEqual(sayi, 3)

    def test_ayni_saniyede_yedekler_birbirini_ezmez(self):
        """Bu hata gerçekten yaşandı: geri yükleme sırasında emniyet
        yedeği, geri yüklenecek dosyanın üzerine yazdı."""
        import yedekle
        klasor = _gecici.parent / "yedek_testi2"
        adlar = {yedekle.yedek_al(klasor).name for _ in range(5)}
        self.assertEqual(len(adlar), 5)

    def test_eski_yedekler_temizlenir(self):
        import yedekle
        klasor = _gecici.parent / "yedek_testi3"
        for _ in range(8):
            yedekle.yedek_al(klasor)
        yedekle.eskileri_temizle(klasor, saklanacak=3)
        self.assertEqual(len(list(klasor.glob("stok_*.db"))), 3)


def temizle():
    shutil.rmtree(_gecici.parent, ignore_errors=True)


if __name__ == "__main__":
    print(f"Geçici veritabanı: {_gecici}\n")
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        temizle()
        print("\nGeçici veritabanı silindi. stok.db'ye dokunulmadı.")
