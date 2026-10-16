"""
auth.py — Parola güvenliği ve oturum yönetimi.

İki iş yapar:
  1. Parolayı güvenli biçimde saklanabilir hale getirir (hash)
  2. Giriş yapan kullanıcı için oturum jetonu üretir ve takip eder

Hiç dış kütüphane kullanmıyor. hashlib, hmac, secrets — üçü de
Python'un içinde geliyor.

--- PAROLA NEDEN DÜZ METİN SAKLANMAZ ---
Veritabanına parolayı olduğu gibi yazarsan, o dosyayı ele geçiren
herkes tüm parolaları okur. Üstelik insanlar aynı parolayı başka
yerlerde de kullandığı için zarar senin uygulamanla sınırlı kalmaz.

Çözüm: parolanın kendisini değil, ondan üretilen tek yönlü bir
"özet" (hash) saklamak. Özetten parolaya geri dönüş yok. Giriş
sırasında girilen parolanın özeti hesaplanır ve kayıtlı özetle
karşılaştırılır.
"""

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta

# Kaç tur döndürüleceği. Sayı büyüdükçe hesap yavaşlar — bu KASITLI.
# Saldırgan milyonlarca parola denemek isterse her deneme ona da
# pahalıya gelir. Bizim için tek girişte fark edilmez bir gecikme.
TUR_SAYISI = 200_000


def parola_ozeti(parola, tuz=None):
    """Parolayı saklanabilir bir metne çevirir.

    Sonuç şuna benzer:
        pbkdf2_sha256$200000$3f9a...$8c1d...
        algoritma      tur     tuz    özet

    Dört parçayı tek metinde tutuyoruz ki doğrulama sırasında hangi
    ayarlarla üretildiğini bilelim. İleride tur sayısını artırırsan
    eski kayıtlar hâlâ doğrulanabilir.
    """
    # TUZ (salt): her kullanıcı için rastgele üretilen ek veri.
    # Aynı parolayı kullanan iki kişinin özeti farklı olsun diye.
    # Tuz olmasa saldırgan hazır özet tablolarıyla eşleştirme yapardı.
    if tuz is None:
        tuz = secrets.token_bytes(16)

    ozet = hashlib.pbkdf2_hmac("sha256", parola.encode("utf-8"), tuz, TUR_SAYISI)
    return f"pbkdf2_sha256${TUR_SAYISI}${tuz.hex()}${ozet.hex()}"


def parola_dogrula(parola, kayitli_ozet):
    """Girilen parola kayıtlı özetle uyuşuyor mu?"""
    try:
        algoritma, tur, tuz_hex, ozet_hex = kayitli_ozet.split("$")
    except (ValueError, AttributeError):
        return False

    if algoritma != "pbkdf2_sha256":
        return False

    yeni = hashlib.pbkdf2_hmac(
        "sha256", parola.encode("utf-8"), bytes.fromhex(tuz_hex), int(tur)
    )

    # compare_digest, düz == karşılaştırmasından farklı olarak her zaman
    # aynı sürede çalışır. Düz karşılaştırma ilk farklı byte'ta durur;
    # saldırgan cevap sürelerini ölçerek özeti byte byte tahmin edebilir.
    # Buna "zamanlama saldırısı" deniyor.
    return hmac.compare_digest(yeni, bytes.fromhex(ozet_hex))


# ---------------------------------------------------------------------------
# Oturumlar
# ---------------------------------------------------------------------------
#
# Oturumları bellekte, basit bir sözlükte tutuyoruz:
#     {jeton: {"id": 3, "kullanici_adi": "depo", "rol": "depo", ...}}
#
# Sonuç: sunucuyu yeniden başlattığında herkes çıkış yapmış olur.
# Gerçek uygulamalarda oturumlar veritabanında veya Redis gibi bir
# yerde tutulur. Öğrenme amaçlı bir projede bellekte tutmak yeterli
# ve mekanizmayı görmeyi kolaylaştırıyor.

# Oturumlar veritabanında tutuluyor. Bellekte tutulduğu ilk sürümde
# sunucu her yeniden başladığında herkes çıkış yapıyordu.
#
# Kaç gün sonra oturum düşsün. Kullanıcı bu süre boyunca hiç işlem
# yapmazsa oturumu geçersiz olur. Depoda gün boyu açık kalan bir
# tarayıcı için 7 gün rahat bir süre.
OTURUM_OMRU_GUN = 7

# CSRF jetonlarını imzalamak için sunucuya özel gizli anahtar.
# Sunucu her başladığında yenileniyor — yani yeniden başlatma sonrası
# açık formlar geçersiz olur, kullanıcı sayfayı yenileyip tekrar
# gönderir. Kalıcı olması gerekseydi bir dosyada saklanırdı.
_CSRF_ANAHTARI = secrets.token_bytes(32)

# Deneme sayaçları hâlâ bellekte (aşağıda) — orada kalması sorun değil,
# sunucu yeniden başlayınca sıfırlanmaları kabul edilebilir.
_kilit = threading.Lock()


def oturum_ac(kullanici):
    """Kullanıcı için yeni bir oturum jetonu üretir ve kaydeder."""
    # token_urlsafe: kriptografik olarak güvenli rastgele metin.
    # random.random() KULLANILMAZ — o tahmin edilebilir, oturum
    # jetonu tahmin edilebilirse başkasının hesabına girilir.
    jeton = secrets.token_urlsafe(32)
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    from db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO oturumlar (jeton, user_id, olusturma, son_erisim) "
            "VALUES (?, ?, ?, ?)",
            (jeton, kullanici["id"], simdi, simdi))
        conn.commit()
    finally:
        conn.close()

    return jeton


def oturum_getir(jeton):
    """Jetona karşılık gelen kullanıcıyı döndürür, yoksa None.

    Her istekte çağrılıyor, yani bu fonksiyon sık çalışıyor.
    son_erisim'i her istekte yazmak gereksiz yazma yükü olurdu;
    sadece 5 dakikadan eskiyse güncelliyoruz.
    """
    if not jeton:
        return None

    from db import get_connection
    conn = get_connection()
    try:
        satir = conn.execute("""
            SELECT o.jeton, o.son_erisim,
                   u.id, u.kullanici_adi, u.ad_soyad, u.rol, u.aktif
            FROM oturumlar o
            JOIN users u ON u.id = o.user_id
            WHERE o.jeton = ?
        """, (jeton,)).fetchone()

        if not satir:
            return None

        # Hesap kapatıldıysa açık oturum da geçersiz olmalı. Aksi halde
        # yönetici birini pasife alır ama o kişi çalışmaya devam eder.
        if not satir["aktif"]:
            conn.execute("DELETE FROM oturumlar WHERE jeton = ?", (jeton,))
            conn.commit()
            return None

        son = datetime.strptime(satir["son_erisim"], "%Y-%m-%d %H:%M:%S")
        simdi = datetime.now()

        if (simdi - son) > timedelta(days=OTURUM_OMRU_GUN):
            conn.execute("DELETE FROM oturumlar WHERE jeton = ?", (jeton,))
            conn.commit()
            return None

        if (simdi - son) > timedelta(minutes=5):
            conn.execute("UPDATE oturumlar SET son_erisim = ? WHERE jeton = ?",
                         (simdi.strftime("%Y-%m-%d %H:%M:%S"), jeton))
            conn.commit()

        return {
            "id": satir["id"],
            "kullanici_adi": satir["kullanici_adi"],
            "ad_soyad": satir["ad_soyad"],
            "rol": satir["rol"],
        }
    finally:
        conn.close()


def oturum_kapat(jeton):
    """Oturumu siler."""
    if not jeton:
        return
    from db import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM oturumlar WHERE jeton = ?", (jeton,))
        conn.commit()
    finally:
        conn.close()


def oturum_sayisi():
    """Açık oturum sayısı — yönetici ekranında göstermek için."""
    from db import get_connection
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM oturumlar").fetchone()[0]
    finally:
        conn.close()


def oturumdaki_idler():
    """Şu an açık oturumu olan kullanıcıların id kümesi.

    Kullanıcının "aktif" olması (hesabı açık) ile "oturumda" olması
    (şu an giriş yapmış) tamamen farklı iki şey.
    """
    from db import get_connection
    conn = get_connection()
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM oturumlar")}
    finally:
        conn.close()


def csrf_jetonu(oturum_jetonu):
    """Oturuma bağlı bir CSRF jetonu üretir.

    --- CSRF NEDİR ---
    Sen bizim sisteme giriş yapmışsın. Sonra başka bir sitede
    gizli bir form var:

        <form action="http://sunucu:8000/urun-sil" method="post">
          <input type="hidden" name="id" value="42">
        </form>
        <script>document.forms[0].submit()</script>

    O siteyi açtığın anda tarayıcın bizim sunucuya POST atar VE
    çerezini de gönderir — çünkü çerezler hedef siteye göre
    gönderilir, isteği kimin başlattığına göre değil. Sunucu
    açısından bu senin isteğin gibi görünür ve ürün silinir.

    Buna Cross-Site Request Forgery deniyor.

    --- ÇÖZÜM ---
    Her forma, o oturuma özel gizli bir değer koyuyoruz. Saldırgan
    bu değeri bilemez, çünkü senin sayfanın içeriğini okuyamaz
    (tarayıcılar buna izin vermez). Değer gelmezse istek reddedilir.

    Jetonu oturum jetonundan TÜRETİYORUZ, ayrıca saklamıyoruz:
    HMAC ile sunucuya özel bir gizli anahtar kullanarak. Böylece
    ekstra tablo gerekmiyor ama saldırgan da üretemiyor.

    Not: SameSite=Lax çerezimiz zaten temel koruma sağlıyor —
    başka siteden gelen POST'larda çerez gönderilmiyor. CSRF jetonu
    ikinci savunma katmanı. Güvenlikte tek katmana güvenilmez.
    """
    if not oturum_jetonu:
        return ""
    return hmac.new(_CSRF_ANAHTARI, oturum_jetonu.encode(),
                    hashlib.sha256).hexdigest()


def csrf_dogrula(oturum_jetonu, gelen):
    """Formdan gelen CSRF jetonu geçerli mi?"""
    if not oturum_jetonu or not gelen:
        return False
    # compare_digest: zamanlama saldırısına karşı sabit süreli karşılaştırma
    return hmac.compare_digest(csrf_jetonu(oturum_jetonu), gelen)


def eski_oturumlari_temizle():
    """Ömrü dolmuş oturumları siler. Sunucu açılışında bir kez çağrılıyor."""
    sinir = (datetime.now() - timedelta(days=OTURUM_OMRU_GUN)
             ).strftime("%Y-%m-%d %H:%M:%S")
    from db import get_connection
    conn = get_connection()
    try:
        n = conn.execute("DELETE FROM oturumlar WHERE son_erisim < ?",
                         (sinir,)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Giriş deneme sınırı
# ---------------------------------------------------------------------------
#
# Sınır olmadan biri parolaları sırayla deneyebilir. Bizim parola
# özetimiz yavaş (200.000 tur, ~120 ms) ve bu zaten büyük bir engel —
# saniyede en fazla 8 deneme yapılabilir. Ama günlerce süren bir
# deneme yine de mümkün.
#
# Sayacı KULLANICI ADINA göre tutuyoruz, IP'ye göre değil:
#   - Aynı ağdaki herkes tek bir IP'den geliyor olabilir (NAT);
#     IP'ye göre sayarsak bir kişinin hatası herkesi kilitler.
#   - Kullanıcı adına göre saymak da bir riski var: biri bilerek
#     yanlış parola girip başkasının hesabını kilitleyebilir. Bu yüzden
#     kilit KALICI değil, birkaç dakikalık.
#
# Bellekte tutuluyor — sunucu yeniden başlayınca sayaçlar sıfırlanır.
# Kabul edilebilir: saldırgan sunucuyu yeniden başlatamaz.

DENEME_SINIRI = 5          # kaç yanlış denemeden sonra kilitlensin
KILIT_SURESI = 300         # saniye (5 dakika)

_denemeler = {}            # {kullanici_adi: [yanlis_sayisi, son_deneme_zamani]}


def kilitli_mi(kullanici_adi):
    """Bu hesap şu an kilitli mi? (kalan_saniye, kilitli_mi)"""
    kullanici_adi = (kullanici_adi or "").strip().lower()
    with _kilit:
        kayit = _denemeler.get(kullanici_adi)
        if not kayit:
            return 0

        sayi, son = kayit
        if sayi < DENEME_SINIRI:
            return 0

        gecen = time.time() - son
        if gecen >= KILIT_SURESI:
            # Süre doldu, sayacı sıfırla
            _denemeler.pop(kullanici_adi, None)
            return 0

        return int(KILIT_SURESI - gecen)


def basarisiz_deneme(kullanici_adi):
    """Yanlış parola denemesini kaydeder, kalan hakkı döndürür."""
    kullanici_adi = (kullanici_adi or "").strip().lower()
    with _kilit:
        sayi, _ = _denemeler.get(kullanici_adi, (0, 0))
        _denemeler[kullanici_adi] = (sayi + 1, time.time())
        return max(0, DENEME_SINIRI - (sayi + 1))


def denemeleri_sifirla(kullanici_adi):
    """Başarılı girişten sonra sayacı temizler."""
    kullanici_adi = (kullanici_adi or "").strip().lower()
    with _kilit:
        _denemeler.pop(kullanici_adi, None)


# ---------------------------------------------------------------------------
# Roller ve yetkiler
# ---------------------------------------------------------------------------

ROLLER = {
    "depo": "Depo Görevlisi",
    "satis": "Satış Sorumlusu",
    "yonetici": "Yönetici",
}

# Hangi adres hangi rollere açık.
# Yönetici her yere girer, o yüzden listelerde ayrıca yazılıyor.
# Burada olmayan bir adres = giriş yapmış herkese açık.
YETKILER = {
    "/hareket": {"depo", "yonetici"},
    "/uretim-fisi": {"depo", "yonetici"},
    "/satis-faturasi": {"satis", "yonetici"},
    # İade, satışın işi: müşteriyle anlaşan, faturayı kesen o.
    # Deponun işi malı teslim alıp kontrol etmek — hasarlıysa
    # "Fire / hurda ayrımı" sebebiyle düzeltme fişi keser.
    "/iade-faturasi": {"satis", "yonetici"},
    "/musteriler": {"satis", "yonetici"},
    # /belgeler ve /belge kısıtlı değil: giriş yapmış herkes belgeleri
    # görebilir. Görmek ile oluşturmak farklı yetkiler — satış sorumlusu
    # üretim fişini görür ama kesemez.
    "/depo": {"depo", "yonetici"},
    "/rapor": {"satis", "yonetici"},
    "/urun-ekle": {"yonetici"},
    "/urun-duzenle": {"yonetici"},
    "/urun-sil": {"yonetici"},
    "/kullanicilar": {"yonetici"},
    # Yedek almak bir yönetim işi. Ayrıca yedekleme sunucuyu kısa süre
    # meşgul ettiği için herkese açık olmamalı — biri arka arkaya
    # tetikleyip diski doldurabilirdi.
    "/yedek-al": {"yonetici"},
    "/kullanici-ekle": {"yonetici"},
    "/kullanici-duzenle": {"yonetici"},
    "/kullanici-parola": {"yonetici"},
    "/kullanici-durum": {"yonetici"},
    # Belge iptali yalnızca yöneticide. Belgeyi kesen kişinin kendi
    # hatasını sessizce silebilmesi, denetim izini işe yaramaz hale
    # getirir — bu yüzden iptal ayrı bir yetkidir.
    "/belge-iptal": {"yonetici"},
    "/indir/kritik.xlsx": {"satis", "yonetici"},
    "/indir/kritik.csv": {"satis", "yonetici"},
    "/indir/envanter.xlsx": {"satis", "yonetici"},
}


def yetkili_mi(rol, yol):
    """Bu rol bu adrese girebilir mi?"""
    izinli = YETKILER.get(yol)
    if izinli is None:
        return True  # kısıtlanmamış sayfa
    return rol in izinli


def fiyat_gorebilir(rol):
    """Depo görevlisi fiyat ve stok değeri görmez.

    Gerçek sistemlerde de böyledir: depocunun işi adet saymak,
    maliyet bilgisi onu ilgilendirmez ve görmemesi tercih edilir.
    """
    return rol in ("satis", "yonetici")
