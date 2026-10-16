"""
server.py — Uygulamayı çalıştıran dosya.

    python server.py

Streamlit yerine Python'un kendi http.server modülünü kullanıyor.
Hiçbir dış kütüphane gerekmiyor, yani pip'e hiç ihtiyaç yok.

--- Bir web sunucusu aslında ne yapar? ---
Tarayıcı bir metin gönderir:      GET /urunler?search=rotil
Sunucu buna karşılık HTML döner:  <html>...</html>
Hepsi bu. Django, Flask, Streamlit — hepsi bu döngünün etrafına
kolaylık katmanları ekler. Burada o döngüyü doğrudan görüyorsun.

BaseHTTPRequestHandler sınıfından miras alıp do_GET ve do_POST
metodlarını yazıyoruz. Sunucu bir istek aldığında bu metodları
kendisi çağırıyor.
"""

import html
import http.cookies
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import exports
import queries as q
import yedekle

# --- İstek bağlamı ---
# Her isteği ayrı bir thread karşılıyor (ThreadingHTTPServer). O isteğin
# kullanıcısını her fonksiyona parametre olarak taşımak yerine thread'e
# özel bir alanda tutuyoruz. threading.local() her thread için ayrı bir
# saklama alanı verir, yani iki kullanıcının isteği birbirine karışmaz.
#
# Flask'ın current_user'ı, Django'nun request.user'ı da aynı fikrin
# daha gelişmiş halidir.
_istek = threading.local()


def aktif_kullanici():
    """O anki isteği yapan kullanıcı. Giriş yapılmamışsa None."""
    return getattr(_istek, "kullanici", None)


def rol():
    k = aktif_kullanici()
    return k["rol"] if k else None


def csrf_alani():
    """Formlara eklenecek gizli CSRF alanı.

    Her POST formunda bulunmak zorunda. Unutulan bir form 403 alır —
    bu kasıtlı: sessizce korumasız kalmaktansa gürültülü şekilde
    kırılması iyidir, çünkü test ederken hemen fark edilir.
    """
    jeton = getattr(_istek, "csrf", "")
    return f'<input type="hidden" name="_csrf" value="{jeton}">' if jeton else ""

PORT = 8000

# Excel/CSV çıktılarında hangi kolonlar, hangi başlıkla yer alacak.
EXPORT_HEADERS = [
    ("part_code", "Parça Kodu"),
    ("oem_code", "OEM Kodu"),
    ("name", "Ürün Adı"),
    ("category", "Kategori"),
    ("vehicle_make", "Marka"),
    ("vehicle_model", "Model"),
    ("location", "Lokasyon"),
    ("unit_price", "Birim Fiyat"),
    ("stock", "Stok"),
    ("min_stock", "Kritik Seviye"),
]


# ===========================================================================
# Biçimlendirme yardımcıları
# ===========================================================================

def tl(value):
    """15430425.17 -> '15.430.425,17 ₺'"""
    return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") + " ₺"


def sayi(value):
    """19674 -> '19.674'"""
    return f"{int(value):,}".replace(",", ".")


def esc(value):
    """Kullanıcıdan gelen metni HTML'e basmadan önce zararsızlaştırır.

    Biri arama kutusuna <script>alert(1)</script> yazarsa ve biz bunu
    olduğu gibi sayfaya basarsak, o kod tarayıcıda ÇALIŞIR. Buna XSS denir.
    html.escape() < > & " karakterlerini görüntülenebilir hale çevirir,
    böylece metin metin olarak kalır, koda dönüşmez.
    """
    return html.escape(str(value if value is not None else ""))


def durum(row):
    """Ürünün stok durumu: (etiket, css sınıfı)"""
    stock, minimum = row["stock"], row["min_stock"]
    if stock <= 0:
        return "Tükendi", "s-out"
    if stock <= minimum:
        return "Kritik", "s-crit"
    if stock <= minimum * 1.5:
        return "Sınırda", "s-warn"
    return "Yeterli", "s-ok"


def doluluk_cubugu(row):
    """Stok doluluk çubuğu.

    Çubuğun ortasındaki çentik kritik seviyeyi gösteriyor. Yani dolgu
    çentiğin solunda kaldıysa o ürün üretim planına alınmalı. Ölçek: kritik
    seviyenin 2 katı = tam dolu.
    """
    minimum = max(row["min_stock"], 1)
    oran = min(row["stock"] / (minimum * 2), 1.0) * 100
    _, sinif = durum(row)
    return (f'<div class="bar"><i class="{sinif}" style="width:{oran:.0f}%"></i>'
            f'<u></u></div>')


# ===========================================================================
# HTML iskeleti
# ===========================================================================

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  /* Teknorot kurumsal renkleri: lacivert + yeşil.
     Lacivert taşıyıcı renk (üst şerit, düğmeler), yeşil vurgu rengi
     (marka işareti, aktif sekme, olumlu durumlar).

     Kırmızı burada MARKA rengi değil, yalnızca uyarı rengi: kritik
     stok, hata mesajı, silme işlemi. Uyarı rengini marka renginden
     ayırmak gerekir — ikisi aynı olursa "dikkat et" sinyali
     arayüzün genel görüntüsü içinde kaybolur. */
  --ink:#141a20; --rule:#dde3e9; --muted:#66717e;
  --navy:#0e2c4b;          /* kurumsal lacivert */
  --navy-light:#1c4571;    /* açık lacivert: hover */
  --slate:#0e2c4b;         /* üst şerit ve düğmeler laciverte bağlı */
  --accent:#4f9d2e;        /* kurumsal yeşil */
  --accent-dark:#3d7a23;
  --accent-soft:#eef7e9;
  --uyari:#c8202f;         /* yalnızca kritik/hata */
  --uyari-soft:#fdeaec;
  --warn:#c77700; --ok:#4f9d2e; --bg:#f5f7f9;
  --mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
}
body{font:15px/1.5 system-ui,"Segoe UI",sans-serif;color:var(--ink);
     background:var(--bg)}
header{background:var(--navy);color:#fff;padding:0 24px;
       border-bottom:3px solid var(--accent)}
header .wrap{max-width:1240px;margin:0 auto;display:flex;align-items:center;
             gap:32px;height:56px}
header b{font-size:15px;letter-spacing:.08em;text-transform:uppercase}
header b s{color:var(--accent);text-decoration:none}
nav{display:flex;gap:4px;margin-left:auto}
nav a{color:#b9c8d8;text-decoration:none;padding:8px 14px;border-radius:4px;
      font-size:14px}
nav a:hover{background:var(--navy-light);color:#fff}
nav a.on{background:#fff;color:var(--ink);font-weight:600;
         box-shadow:inset 0 -3px 0 var(--accent)}
main{max-width:1240px;margin:0 auto;padding:28px 24px 64px}
h1{font-size:22px;font-weight:650;margin-bottom:4px}
.sub{color:var(--muted);font-size:14px;margin-bottom:22px}
h2{font-size:15px;font-weight:650;margin:28px 0 10px;text-transform:uppercase;
   letter-spacing:.06em;color:var(--muted)}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
       gap:12px}
.card{background:#fff;border:1px solid var(--rule);border-radius:6px;
      padding:16px 18px}
.card span{display:block;color:var(--muted);font-size:12px;
           text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.card strong{font:600 24px/1.1 var(--mono)}
.card.alert{border-color:var(--uyari)}
.card.alert strong{color:var(--uyari)}

table{width:100%;border-collapse:collapse;background:#fff;
      border:1px solid var(--rule);border-radius:6px;overflow:hidden}
th{background:#eef1f4;text-align:left;font-size:12px;text-transform:uppercase;
   letter-spacing:.05em;color:#4a545e;padding:9px 12px;
   border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid #eef1f4;font-size:14px}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbfc}
.code{font:13px var(--mono);color:var(--navy-light);white-space:nowrap}
.num{font:13px var(--mono);text-align:right;white-space:nowrap}

.bar{position:relative;width:96px;height:8px;background:#e8ebee;
     border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:2px}
.bar u{position:absolute;left:50%;top:0;width:1px;height:100%;
       background:#8d97a1}
.s-out{background:#8d1520}.s-crit{background:var(--uyari)}
.s-warn{background:var(--warn)}.s-ok{background:var(--ok)}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;
      font-weight:600}
.pill.s-out{background:var(--uyari-soft);color:#8d1520}
.pill.s-crit{background:var(--uyari-soft);color:var(--uyari)}
.pill.s-warn{background:#fdf0dc;color:#8a5300}
.pill.s-ok{background:var(--accent-soft);color:var(--accent-dark)}

form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
input,select,textarea{font:14px system-ui,sans-serif;padding:8px 10px;
       border:1px solid #ccd2d8;border-radius:4px;background:#fff;color:var(--ink)}
input:focus,select:focus{outline:2px solid var(--slate);outline-offset:-1px}
button{font:600 14px system-ui,sans-serif;padding:8px 18px;border:0;
       border-radius:4px;background:var(--slate);color:#fff;cursor:pointer}
button:hover{background:var(--navy-light)}
button.birincil{background:var(--accent)}
button.birincil:hover{background:var(--accent-dark)}
tr[data-yol]{cursor:pointer}
td a.code{text-decoration:none;border-bottom:1px solid transparent}
td a.code:hover{border-bottom-color:var(--accent)}
a.btn{display:inline-block;padding:8px 16px;border:1px solid #ccd2d8;
      border-radius:4px;background:#fff;color:var(--ink);text-decoration:none;
      font-size:14px;font-weight:500}
a.btn:hover{border-color:var(--slate)}

.panel{background:#fff;border:1px solid var(--rule);border-radius:6px;
       padding:20px;max-width:460px}
.panel label{display:block;font-size:13px;font-weight:600;margin:14px 0 5px}
.panel label:first-child{margin-top:0}
.form2{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}
.form2 label{margin-top:14px}
@media(max-width:700px){.form2{grid-template-columns:1fr}}
.panel input,.panel select{width:100%}
.radio{display:flex;gap:8px}
.radio label{flex:1;margin:0;padding:9px;border:1px solid #ccd2d8;
             border-radius:4px;text-align:center;cursor:pointer;font-weight:500}
.radio input{display:none}
.radio input:checked+span{font-weight:700}
.radio label:has(input:checked){border-color:var(--slate);background:#eef1f4}

.msg{padding:11px 14px;border-radius:4px;margin-bottom:16px;font-size:14px}
.msg.ok{background:#e6f2eb;color:#1d5c39;border:1px solid #b8dcc7}
.msg.err{background:var(--uyari-soft);color:#8d1520;
         border:1px solid #f2c2c7}
.two{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
.empty{padding:36px;text-align:center;color:var(--muted);background:#fff;
       border:1px solid var(--rule);border-radius:6px}
@media(max-width:860px){.two{grid-template-columns:1fr}}
.who{display:flex;align-items:center;gap:10px;margin-left:20px;
     padding-left:20px;border-left:1px solid #2a4d75;font-size:13px}
.who span{color:#fff;font-weight:600}
.who em{color:#93a8bd;font-style:normal;font-size:12px}
.who a{color:#b9c8d8;text-decoration:none;padding:5px 10px;border-radius:4px;
       border:1px solid #2a4d75}
.who a:hover{background:var(--navy-light);color:#fff}

body.giris-sayfa{display:flex;align-items:center;justify-content:center;
                 min-height:100vh;padding:20px}
.giris-kart{background:#fff;border:1px solid var(--rule);border-radius:8px;
            padding:32px;width:100%;max-width:380px}
.giris-kart .marka{display:block;font-size:15px;letter-spacing:.08em;
                   text-transform:uppercase;font-weight:700}
.giris-kart .marka s{color:var(--accent);text-decoration:none}
.giris-kart .marka{color:var(--navy)}
.giris-kart label{display:block;font-size:13px;font-weight:600;margin:14px 0 5px}
.giris-kart input{width:100%}
.giris-kart .demo{margin-top:24px;padding-top:18px;border-top:1px solid var(--rule)}
.giris-kart .demo b{font-size:12px;text-transform:uppercase;letter-spacing:.06em;
                    color:var(--muted)}
.giris-kart .demo table{border:0;background:none;margin-top:8px;width:100%}
.giris-kart .demo td{border:0;padding:3px 0;font-size:13px;color:var(--muted)}
.giris-kart .demo td:first-child{font-family:var(--mono);color:var(--ink)}
.giris-kart .demo td:nth-child(2){font-family:var(--mono)}

@media print{
  header,nav,form,.noprint{display:none!important}
  body{background:#fff}main{padding:0}
  table{border:1px solid #999}th{background:#eee}
}
"""

# Tüm menü girdileri. Hangisinin görüneceğine rol karar veriyor:
# auth.yetkili_mi() zaten "bu rol bu adrese girebilir mi" biliyor,
# menüyü de aynı bilgiden üretiyoruz. Böylece yetki kuralı tek yerde
# tanımlı kalıyor — menüde görünüp tıklandığında reddedilen bağlantı
# olmuyor.
MENU = [("/", "Panel"), ("/urunler", "Ürünler"), ("/belgeler", "Belgeler"),
        ("/musteriler", "Müşteriler"), ("/hareket", "Stok Düzeltme"),
        ("/depo", "Depo"), ("/seyir", "Stok Seyri"), ("/rapor", "Raporlar"),
        ("/kullanicilar", "Kullanıcılar")]


def sayfa(baslik, aktif, govde):
    """Ortak HTML iskeleti. Her sayfa bunun içine yerleşiyor."""
    kullanici = aktif_kullanici()
    kul_rol = kullanici["rol"] if kullanici else None

    nav = "".join(
        f'<a href="{yol}" class="{"on" if yol == aktif else ""}">{ad}</a>'
        for yol, ad in MENU
        if auth.yetkili_mi(kul_rol, yol)
    )

    if kullanici:
        kimlik = f"""<div class="who">
            <span>{esc(kullanici['ad_soyad'])}</span>
            <em>{esc(auth.ROLLER.get(kullanici['rol'], kullanici['rol']))}</em>
            <a href="/cikis">Çıkış</a></div>"""
    else:
        kimlik = ""

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(baslik)} · Teknorot Stok</title><style>{CSS}</style></head>
<body><header><div class="wrap"><b>TEKNOROT<s>/</s>STOK</b><nav>{nav}</nav>
{kimlik}</div>
</header><main>{govde}</main>
<script>
/* Tablo satırının tamamını tıklanabilir yapar.
   HTML'de <tr> etiketini link yapmanın saf bir yolu yok — <a> sadece
   hücre içeriğini sarabilir. O yüzden satıra data-yol koyup burada
   dinliyoruz.

   Parça kodundaki <a> bağlantısı yerinde duruyor: JavaScript kapalı
   olsa bile koda tıklayarak detaya gidilebiliyor. Buna "aşamalı
   iyileştirme" (progressive enhancement) deniyor — temel işlev
   JS olmadan çalışır, JS varsa kullanım kolaylaşır. */
document.querySelectorAll("tr[data-yol]").forEach(function (satir) {{
  satir.addEventListener("click", function (olay) {{
    // Hücrenin içindeki bir bağlantıya veya form öğesine tıklandıysa
    // karışma, onun kendi işini yapmasına izin ver.
    if (olay.target.closest("a, button, input, select, label")) return;
    // Kullanıcı metin seçiyorsa sayfa değiştirmeyelim.
    if (window.getSelection().toString()) return;
    location.href = satir.dataset.yol;
  }});
}});

/* --- Belge formlarında satır ekleme ---
   Formlar 8 satırla açılıyor; daha fazla kalem gerekirse kullanıcı
   "+ Satır ekle" ile yenisini alıyor.

   Yeni satırı sıfırdan kurmuyoruz: son satırı KOPYALIYORUZ
   (cloneNode(true) = içindekilerle birlikte kopyala). Böylece 180
   ürünlük <select> listesini JavaScript'te yeniden üretmemiz
   gerekmiyor — zaten sayfada var.

   JavaScript kapalıysa form yine 8 satırla çalışır; sadece bu
   düğme görünmez. Aşamalı iyileştirme.  */
(function () {{
  var dugme = document.getElementById("satir-ekle");
  if (!dugme) return;

  var govde = document.getElementById("kalemler");
  dugme.hidden = false;

  dugme.addEventListener("click", function () {{
    var satirlar = govde.querySelectorAll("tr");
    var yeni = satirlar[satirlar.length - 1].cloneNode(true);

    // Kopyalanan satırın alanlarını temizle
    yeni.querySelectorAll("input").forEach(function (alan) {{ alan.value = ""; }});
    yeni.querySelectorAll("select").forEach(function (alan) {{ alan.selectedIndex = 0; }});

    // Sıra numarasını güncelle
    var numara = yeni.querySelector("td");
    if (numara) numara.textContent = satirlar.length + 1;

    govde.appendChild(yeni);
    var ilk = yeni.querySelector("select");
    if (ilk) ilk.focus();
  }});
}})();
</script>
</body></html>"""


def sayfa_giris(hata="", kullanici_adi="", devam=""):
    """Giriş ekranı. Menüsüz, ortalanmış tek kart."""
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Giriş · Teknorot Stok</title><style>{CSS}</style></head>
<body class="giris-sayfa">
<div class="giris-kart">
  <b class="marka">TEKNOROT<s>/</s>STOK</b>
  <p class="sub" style="margin:6px 0 20px">Stok Takip Sistemi</p>
  {f'<div class="msg err">{esc(hata)}</div>' if hata else ''}
  <form method="post" action="/giris">
    <input type="hidden" name="devam" value="{esc(devam)}">
    <label>Kullanıcı Adı
      <input name="kullanici_adi" value="{esc(kullanici_adi)}"
             autofocus required autocomplete="username"></label>
    <label>Parola
      <input name="parola" type="password" required
             autocomplete="current-password"></label>
    <button style="width:100%;margin-top:18px">Giriş yap</button>
  </form>
  <div class="demo">
    <b>Demo hesaplar</b>
    <table>
      <tr><td>depo</td><td>depo123</td><td>Depo Görevlisi</td></tr>
      <tr><td>satis</td><td>satis123</td><td>Satış Sorumlusu</td></tr>
      <tr><td>admin</td><td>admin123</td><td>Yönetici</td></tr>
    </table>
  </div>
</div></body></html>"""


def sayfa_yetkisiz(yol):
    """403 — giriş yapmış ama bu sayfaya yetkisi olmayan kullanıcı."""
    kullanici = aktif_kullanici()
    return sayfa("Yetkisiz", "/", f"""
      <h1>Bu sayfaya erişim yetkiniz yok</h1>
      <p class="sub">{esc(yol)}</p>
      <div class="panel">
        <p style="font-size:14px;margin-bottom:14px">
          <b>{esc(kullanici['ad_soyad'])}</b> hesabı
          <b>{esc(auth.ROLLER.get(kullanici['rol'], ''))}</b> rolünde.
          Bu bölüm başka bir role tanımlı.</p>
        <a class="btn" href="/">Panele dön</a>
      </div>""")


def sayfa_kullanicilar(params):
    """Kullanıcı listesi (yalnızca yönetici).

    İki ayrı durum kolonu var ve karıştırılmaması önemli:
      Hesap  -> hesap açık mı? (veritabanındaki aktif kolonu, kalıcı)
      Oturum -> şu an giriş yapmış mı? (bellekteki oturum, geçici)
    """
    kullanicilar = q.list_users()
    oturumda = auth.oturumdaki_idler()
    benim_id = (aktif_kullanici() or {}).get("id")

    # Mevcut yedeklerin özeti. yedekle.py'nin klasör yolunu kullanıyoruz,
    # yolu burada tekrar yazmıyoruz — tek kaynak orası.
    yedek_klasor = yedekle.VARSAYILAN_KLASOR
    yedekler = (sorted(yedek_klasor.glob("stok_*.db"), reverse=True)
                if yedek_klasor.exists() else [])

    if yedekler:
        son = yedekler[0]
        son_tarih = datetime.fromtimestamp(son.stat().st_mtime)
        yedek_ozet = (f"{len(yedekler)} yedek · en son "
                      f"{son_tarih:%d.%m.%Y %H:%M}")
    else:
        yedek_ozet = "Henüz yedek alınmamış"

    satirlar = ""
    for k in kullanicilar:
        hesap = ("<span class='pill s-ok'>Açık</span>" if k["aktif"]
                 else "<span class='pill s-crit'>Kapalı</span>")
        if k["id"] in oturumda:
            oturum = ("<span class='pill s-ok'>Oturumda"
                      + (" (sen)" if k["id"] == benim_id else "") + "</span>")
        else:
            oturum = "<span style='color:var(--muted)'>—</span>"

        satirlar += (
            f"<tr data-yol='/kullanici-duzenle?id={k['id']}'>"
            f"<td><a class='code' href='/kullanici-duzenle?id={k['id']}'>"
            f"{esc(k['kullanici_adi'])}</a></td>"
            f"<td>{esc(k['ad_soyad'])}</td>"
            f"<td>{esc(auth.ROLLER.get(k['rol'], k['rol']))}</td>"
            f"<td>{hesap}</td><td>{oturum}</td>"
            f"<td class='num' style='text-align:left'>{esc(k['olusturma'])}</td></tr>"
        )

    return sayfa("Kullanıcılar", "/kullanicilar", f"""
      <h1>Kullanıcılar</h1>
      <p class="sub">{len(kullanicilar)} kayıtlı kullanıcı ·
         {auth.oturum_sayisi()} açık oturum</p>
      {mesajlar(params)}
      <div class="filters" style="margin-bottom:18px;align-items:center">
        <a class="btn" href="/kullanici-ekle">+ Yeni kullanıcı</a>
        <form method="post" action="/yedek-al"
              style="margin:0;display:inline-block;vertical-align:middle">
          {csrf_alani()}
          <button class="btn" style="cursor:pointer">Yedek al</button>
        </form>
        <span class="sub" style="font-size:13px;margin:0;vertical-align:middle">
          {esc(yedek_ozet)}</span>
      </div>
      <table>
        <tr><th>Kullanıcı Adı</th><th>Ad Soyad</th><th>Rol</th>
            <th>Hesap</th><th>Oturum</th><th>Oluşturma</th></tr>
        {satirlar}
      </table>
      <div class="two" style="margin-top:20px">
        <p class="sub" style="font-size:13px">
          <b>Hesap</b> kalıcı bir ayardır: kapalı hesap giriş yapamaz.
          <b>Oturum</b> geçicidir; kullanıcı çıkış yaptığında veya sunucu
          yeniden başladığında düşer. Oturumlar bellekte tutulduğu için
          sunucu her yeniden başlatmada herkes çıkış yapmış olur.</p>
        <p class="sub" style="font-size:13px">
          Parolalar veritabanında tek yönlü özet olarak saklanıyor
          (PBKDF2-SHA256, 200.000 tur, kullanıcıya özel tuz). Düz metin
          parola hiçbir yerde tutulmuyor, bu listede de yok.</p>
      </div>""")


# ===========================================================================
# Sayfalar
# ===========================================================================

def sayfa_panel():
    # Depo görevlisi maliyet bilgisi görmez — auth.fiyat_gorebilir karar veriyor.
    fiyat_var = auth.fiyat_gorebilir(rol())
    s = q.get_dashboard_stats()
    kritik = q.get_critical_products()

    kartlar = f"""
    <div class="cards">
      <div class="card"><span>Ürün Çeşidi</span><strong>{s['urun_sayisi']}</strong></div>
      <div class="card"><span>Toplam Adet</span><strong>{sayi(s['toplam_adet'])}</strong></div>
      {f"<div class='card'><span>Stok Değeri</span><strong>{tl(s['stok_degeri'])}</strong></div>" if fiyat_var else ""}
      <div class="card {'alert' if s['kritik_sayisi'] else ''}">
        <span>Kritik Ürün</span><strong>{s['kritik_sayisi']}</strong></div>
    </div>"""

    # Kategori tablosu
    satirlar = "".join(
        f"<tr data-yol='/urunler?kategori={urllib.parse.quote(g['kategori'])}'>"
        f"<td><a class='code' style='color:var(--ink)' "
        f"href='/urunler?kategori={urllib.parse.quote(g['kategori'])}'>"
        f"{esc(g['kategori'])}</a></td>"
        f"<td class='num'>{g['cesit']}</td>"
        f"<td class='num'>{sayi(g['adet'])}</td>"
        + (f"<td class='num'>{tl(g['deger'])}</td>" if fiyat_var else "")
        + "</tr>"
        for g in q.get_category_summary()
    )
    kategori = f"""<h2>Kategori dağılımı</h2><table>
      <tr><th>Kategori</th><th class="num">Çeşit</th><th class="num">Adet</th>
          {'<th class="num">Değer</th>' if fiyat_var else ''}</tr>{satirlar}</table>"""

    # Kritik ürünler
    if kritik:
        kr = "".join(
            f"<tr data-yol='/urun?id={r['id']}'>"
            f"<td><a class='code' href='/urun?id={r['id']}'>"
            f"{esc(r['part_code'])}</a></td>"
            f"<td>{esc(r['name'])}</td><td>{esc(r['location'])}</td>"
            f"<td class='num'>{r['stock']}</td>"
            f"<td class='num'>{r['min_stock']}</td>"
            f"<td>{doluluk_cubugu(r)}</td></tr>"
            for r in kritik[:10]
        )
        acil = f"""<h2>Acil müdahale gerekenler</h2><table>
          <tr><th>Parça Kodu</th><th>Ürün</th><th>Lokasyon</th>
              <th class="num">Stok</th><th class="num">Kritik</th>
              <th>Doluluk</th></tr>{kr}</table>
          <p class="sub" style="margin-top:10px">
            Çubuktaki çentik kritik seviyeyi gösterir.</p>"""
    else:
        acil = '<h2>Acil müdahale gerekenler</h2><div class="empty">Kritik seviyenin altında ürün yok.</div>'

    # Hızlı işlemler: kullanıcı hangi belgeleri kesebiliyorsa onlar.
    # Belge oluşturma ekranları menüde ayrı sekme değil (menü zaten uzun),
    # o yüzden giriş yapınca ilk görülen yerde duruyorlar. Bir işlevin
    # var olması yetmez — bulunabilir de olması gerekir.
    islemler = []
    if auth.yetkili_mi(rol(), "/satis-faturasi"):
        islemler.append(('/satis-faturasi', 'Satış faturası kes', True))
    if auth.yetkili_mi(rol(), "/iade-faturasi"):
        islemler.append(('/iade-faturasi', 'İade faturası kes', False))
    if auth.yetkili_mi(rol(), "/uretim-fisi"):
        islemler.append(('/uretim-fisi', 'Üretim fişi kes', not islemler))
    if auth.yetkili_mi(rol(), "/hareket"):
        islemler.append(('/hareket', 'Stok düzeltme', False))
    islemler.append(('/belgeler', 'Belgeleri gör', False))

    hizli = ""
    if islemler:
        dugmeler = ""
        for adres, etiket, birincil in islemler:
            stil = ('style="background:var(--accent);color:#fff;'
                    'border-color:var(--accent)"' if birincil else '')
            dugmeler += f'<a class="btn" href="{adres}" {stil}>{etiket}</a>'
        hizli = f'''<h2>Hızlı işlem</h2>
          <div class="filters" style="margin-bottom:26px">{dugmeler}</div>'''

    return sayfa("Panel", "/", f"""
      <h1>Genel Durum</h1>
      <p class="sub">Depodaki tüm kalemlerin özeti</p>
      {kartlar}
      {hizli}
      {kategori}{acil}""")


def sayfa_urunler(params):
    fiyat_var = auth.fiyat_gorebilir(rol())
    search = params.get("search", [""])[0]
    kategori = params.get("kategori", [""])[0]
    marka = params.get("marka", [""])[0]

    urunler = q.get_products(search=search, category=kategori, vehicle_make=marka)
    kategoriler, markalar = q.get_filter_options()

    def secenekler(liste, secili, bos_etiket):
        opts = f'<option value="">{bos_etiket}</option>'
        for x in liste:
            sec = " selected" if x == secili else ""
            opts += f'<option value="{esc(x)}"{sec}>{esc(x)}</option>'
        return opts

    filtre = f"""<form class="filters" method="get">
      <input name="search" value="{esc(search)}" placeholder="Parça kodu, ürün adı veya OEM" style="min-width:280px">
      <select name="kategori">{secenekler(kategoriler, kategori, "Tüm kategoriler")}</select>
      <select name="marka">{secenekler(markalar, marka, "Tüm markalar")}</select>
      <button>Ara</button>
      <a class="btn" href="/urunler">Temizle</a>
      {'<a class="btn" href="/urun-ekle" style="margin-left:auto">+ Yeni ürün</a>' if auth.yetkili_mi(rol(), "/urun-ekle") else ''}
    </form>"""

    if not urunler:
        tablo = '<div class="empty">Aramanla eşleşen ürün yok. Filtreleri gevşetmeyi dene.</div>'
    else:
        satirlar = ""
        for r in urunler:
            etiket, sinif = durum(r)
            satirlar += (
                f"<tr data-yol='/urun?id={r['id']}'>"
                f"<td><a class='code' href='/urun?id={r['id']}'>"
                f"{esc(r['part_code'])}</a></td>"
                f"<td class='code' style='color:var(--muted)'>{esc(r['oem_code'])}</td>"
                f"<td>{esc(r['name'])}</td>"
                f"<td>{esc(r['category'])}</td>"
                f"<td>{esc(r['location'])}</td>"
                + (f"<td class='num'>{tl(r['unit_price'])}</td>" if fiyat_var else "")
                + f"<td class='num'>{r['stock']}</td>"
                f"<td>{doluluk_cubugu(r)}</td>"
                f"<td><span class='pill {sinif}'>{etiket}</span></td></tr>"
            )
        tablo = f"""<table>
          <tr><th>Parça Kodu</th><th>OEM</th><th>Ürün Adı</th><th>Kategori</th>
              <th>Lokasyon</th>
              {'<th class="num">Birim Fiyat</th>' if fiyat_var else ''}
              <th class="num">Stok</th><th>Doluluk</th><th>Durum</th></tr>
          {satirlar}</table>"""

    return sayfa("Ürünler", "/urunler", f"""
      <h1>Ürün Kataloğu</h1>
      <p class="sub">{len(urunler)} ürün listeleniyor</p>
      {mesajlar(params)}
      {filtre}{tablo}""")


def mesajlar(params):
    """Adresteki ?ok= veya ?hata= parametresini yeşil/kırmızı kutuya çevirir."""
    if "ok" in params:
        return f'<div class="msg ok">{esc(params["ok"][0])}</div>'
    if "hata" in params:
        return f'<div class="msg err">{esc(params["hata"][0])}</div>'
    return ""


def urun_formu(degerler=None, hata="", duzenle_id=None):
    """Ürün ekleme ve düzenleme formunu üretir.

    İki ekran için tek fonksiyon: duzenle_id verilmişse "düzenle",
    verilmemişse "yeni ürün" modunda çalışıyor. Alanlar aynı olduğu
    için formu iki kez yazmanın anlamı yok.

    degerler: formda görünecek değerler. Hata durumunda kullanıcının
    yazdıkları kaybolmasın diye geri besliyoruz — bir formu doldurup
    tek hata yüzünden her şeyi baştan yazmak zorunda kalmak, en can
    sıkıcı arayüz hatalarından biridir.
    """
    d = degerler or {}
    duzenleme = duzenle_id is not None

    # Mevcut kategori ve markalar öneri listesine konuyor.
    # datalist: kullanıcı listeden seçebilir AMA yeni bir şey de yazabilir.
    # select olsaydı sadece var olanlardan seçebilirdi.
    kategoriler, markalar = q.get_filter_options()
    kat_opt = "".join(f'<option value="{esc(k)}">' for k in kategoriler)
    marka_opt = "".join(f'<option value="{esc(m)}">' for m in markalar)

    def alan(ad, etiket, tip="text", liste=None, ipucu="", zorunlu=False):
        deger = d.get(ad)
        deger = "" if deger is None else str(deger)
        return f"""<label>{etiket}{' *' if zorunlu else ''}
          <input name="{ad}" type="{tip}" value="{esc(deger)}"
                 {f'list="{liste}"' if liste else ''}
                 {'required' if zorunlu else ''}
                 placeholder="{esc(ipucu)}"></label>"""

    aksiyon = "/urun-duzenle" if duzenleme else "/urun-ekle"
    gizli = f'<input type="hidden" name="id" value="{duzenle_id}">' if duzenleme else ""

    return f"""
      {f'<div class="msg err">{esc(hata)}</div>' if hata else ''}
      <form method="post" action="{aksiyon}" class="panel" style="max-width:720px">{csrf_alani()}
        {gizli}
        <div class="form2">
          {alan("part_code", "Parça Kodu", ipucu="FD-1054", zorunlu=True)}
          {alan("oem_code", "OEM Kodu", ipucu="1859739")}
        </div>
        {alan("name", "Ürün Adı", ipucu="Rotilli Salıncak Ön Sol - Ford Transit", zorunlu=True)}
        <div class="form2">
          {alan("category", "Ürün Grubu", liste="kategoriler", zorunlu=True)}
          {alan("vehicle_make", "Marka", liste="markalar")}
        </div>
        <div class="form2">
          {alan("vehicle_model", "Model", ipucu="Transit")}
          {alan("location", "Depo Lokasyonu", ipucu="Depo-A-Raf-03")}
        </div>
        <div class="form2">
          {alan("unit_price", "Birim Fiyat (TL)", ipucu="1250,00")}
          {alan("min_stock", "Kritik Seviye", tip="number", ipucu="30")}
        </div>
        <datalist id="kategoriler">{kat_opt}</datalist>
        <datalist id="markalar">{marka_opt}</datalist>
        <div style="margin-top:20px;display:flex;gap:10px">
          <button>{'Değişiklikleri kaydet' if duzenleme else 'Ürünü ekle'}</button>
          <a class="btn" href="{f'/urun?id={duzenle_id}' if duzenleme else '/urunler'}">Vazgeç</a>
        </div>
        <p class="sub" style="margin:14px 0 0;font-size:13px">
          * işaretli alanlar zorunlu. Yeni ürün eklendiğinde stoğu 0 olur;
          stok girmek için Stok Hareketi sayfasını kullan.</p>
      </form>"""


def sayfa_urun_ekle(degerler=None, hata=""):
    return sayfa("Yeni Ürün", "/urunler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/urunler" style="color:var(--muted)">&larr; Ürün kataloğu</a></p>
      <h1>Yeni Ürün</h1>
      <p class="sub">Katalogda olmayan bir parçayı elle ekle</p>
      {urun_formu(degerler, hata)}""")


def sayfa_urun_duzenle(params, degerler=None, hata=""):
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Ürün", "/urunler", '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Geçersiz ürün numarası.</div>')

    urun = q.get_product(int(ham_id))
    if not urun:
        return sayfa("Ürün", "/urunler", '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir ürün yok.</div>')

    # Hata sonrası kullanıcının yazdıkları varsa onları göster,
    # yoksa veritabanındaki mevcut değerleri.
    return sayfa(f"Düzenle · {urun['part_code']}", "/urunler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/urun?id={urun['id']}" style="color:var(--muted)">
          &larr; {esc(urun['part_code'])}</a></p>
      <h1>Ürünü Düzenle</h1>
      <p class="sub">Stok adedi buradan değiştirilemez — hareket kaydıyla değişir</p>
      {urun_formu(degerler or urun, hata, duzenle_id=urun['id'])}""")


def sayfa_urun_sil(params):
    """Silme onay ekranı.

    Silme geri alınamaz bir işlem, o yüzden tek tıkla yapılmıyor.
    Kullanıcıya ne kaybedeceğini rakamla gösterip onay istiyoruz.
    Web'de yıkıcı işlemler asla GET ile yapılmaz — tarayıcı veya bir
    tarama botu adresi kendiliğinden açarsa ürün silinmiş olurdu.
    Bu yüzden onay ekranı GET, silme işlemi POST.
    """
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Ürün", "/urunler", '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Geçersiz ürün numarası.</div>')

    urun = q.get_product(int(ham_id))
    if not urun:
        return sayfa("Ürün", "/urunler", '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir ürün yok.</div>')

    ozet = q.get_product_totals(urun["id"])

    return sayfa("Ürünü Sil", "/urunler", f"""
      <h1>Ürünü sil?</h1>
      <p class="sub">Bu işlem geri alınamaz</p>
      <div class="panel">
        <p style="font-size:15px;margin-bottom:16px">
          <span class="code">{esc(urun['part_code'])}</span><br>
          {esc(urun['name'])}</p>
        <div class="msg err" style="margin-bottom:18px">
          Ürünle birlikte <b>{ozet['hareket_sayisi']} stok hareketi</b> de
          silinecek. Mevcut {sayi(urun['stock'])} adetlik stok kaydı kaybolur.
        </div>
        <form method="post" action="/urun-sil">{csrf_alani()}
          <input type="hidden" name="id" value="{urun['id']}">
          <div style="display:flex;gap:10px">
            <button style="background:var(--uyari)">Evet, sil</button>
            <a class="btn" href="/urun?id={urun['id']}">Vazgeç</a>
          </div>
        </form>
      </div>""")



# ===========================================================================
# BELGELER
# ===========================================================================

BELGE_ROZET = {
    "uretim": ("s-ok", "Üretim Fişi"),
    "satis": ("s-warn", "Satış Faturası"),
    "iade": ("s-ok", "İade Faturası"),
    "sayim": ("s-warn", "Düzeltme Fişi"),
    "acilis": ("s-ok", "Açılış Fişi"),
}


def belge_rozeti(tip, durum="onayli"):
    if durum == "iptal":
        return "<span class='pill s-crit'>İPTAL</span>"
    sinif, ad = BELGE_ROZET.get(tip, ("s-ok", tip))
    return f"<span class='pill {sinif}'>{esc(ad)}</span>"


def belge_tablosu(belgeler, fiyat_var=True):
    if not belgeler:
        return '<div class="empty">Henüz belge yok.</div>'

    satirlar = ""
    for b in belgeler:
        iptal = b["durum"] == "iptal"
        satirlar += (
            f"<tr data-yol='/belge?id={b['id']}'"
            f"{' style=\'opacity:.55\'' if iptal else ''}>"
            f"<td><a class='code' href='/belge?id={b['id']}'>"
            f"{esc(b['belge_no'])}</a></td>"
            f"<td>{belge_rozeti(b['tip'], b['durum'])}</td>"
            f"<td class='num' style='text-align:left'>{esc(b['tarih'][:16])}</td>"
            f"<td>{esc(b['ad_soyad'])}</td>"
            f"<td style='color:var(--muted)'>{esc(b['musteri_unvan'] or '—')}</td>"
            f"<td class='num'>{b['satir_sayisi']}</td>"
            f"<td class='num'>{sayi(b['toplam_miktar'])}</td>"
            + (f"<td class='num'>{tl(b['toplam_tutar'])}</td>" if fiyat_var else "")
            + f"<td style='color:var(--muted)'>{esc(b['aciklama'])}</td></tr>"
        )

    return f"""<table>
      <tr><th>Belge No</th><th>Tip</th><th>Tarih</th><th>Oluşturan</th>
          <th>Müşteri</th><th class="num">Kalem</th><th class="num">Adet</th>
          {'<th class="num">Tutar</th>' if fiyat_var else ''}
          <th>Açıklama</th></tr>{satirlar}</table>"""


def sayfa_belgeler(params):
    """Belge listesi. Tip düğmeleri + arama ve tarih aralığı."""
    fiyat_var = auth.fiyat_gorebilir(rol())
    tip = params.get("tip", [""])[0]
    tip = tip if tip in q.BELGE_TIPLERI else None

    arama = params.get("arama", [""])[0]
    bas = params.get("bas", [""])[0]
    bit = params.get("bit", [""])[0]
    durum = params.get("durum", [""])[0]

    belgeler = q.list_belgeler(tip=tip, arama=arama, bas_tarih=bas,
                               bit_tarih=bit, durum=durum, limit=300)

    # Tip düğmelerine tıklandığında arama ve tarih kaybolmasın:
    # mevcut filtreleri adrese ekliyoruz. Aksi halde kullanıcı tarih
    # aralığı seçip tipi değiştirince tarihi baştan giriyor.
    def adres_kur(**degisen):
        mevcut = {"tip": tip or "", "arama": arama, "bas": bas,
                  "bit": bit, "durum": durum}
        mevcut.update(degisen)
        parcalar = urllib.parse.urlencode({k: v for k, v in mevcut.items() if v})
        return "/belgeler" + (f"?{parcalar}" if parcalar else "")

    dugmeler = ""
    for kod, etiket in [("", "Tümü"), ("uretim", "Üretim Fişleri"),
                        ("satis", "Satış Faturaları"), ("iade", "İadeler"),
                        ("sayim", "Düzeltmeler"), ("acilis", "Açılış")]:
        secili = "background:var(--slate);color:#fff;border-color:var(--slate)"
        dugmeler += (f'<a class="btn" href="{adres_kur(tip=kod)}" '
                     f'style="{secili if kod == (tip or "") else ""}">{etiket}</a>')

    # Arama formu. GET kullanıyoruz, POST değil: sonuç sayfası adres
    # çubuğunda görünür, yer imine eklenebilir, paylaşılabilir.
    # Arama sonucu bir "sayfa"dır, bir "işlem" değil — GET'in işi budur.
    filtre_formu = f"""<form class="filters" method="get" style="margin-bottom:14px">
      {f'<input type="hidden" name="tip" value="{esc(tip)}">' if tip else ''}
      <input name="arama" value="{esc(arama)}" style="min-width:260px"
             placeholder="Belge no, açıklama veya müşteri">
      <input type="date" name="bas" value="{esc(bas)}" title="Başlangıç tarihi">
      <input type="date" name="bit" value="{esc(bit)}" title="Bitiş tarihi">
      <select name="durum">
        <option value="">Tüm durumlar</option>
        <option value="onayli"{' selected' if durum == 'onayli' else ''}>Onaylı</option>
        <option value="iptal"{' selected' if durum == 'iptal' else ''}>İptal</option>
      </select>
      <button>Ara</button>
      {f'<a class="btn" href="{adres_kur(arama="", bas="", bit="", durum="")}">Temizle</a>' if (arama or bas or bit or durum) else ''}
    </form>"""

    # Yeni belge düğmeleri. Vurgulu duruyorlar çünkü bu sayfanın asıl
    # işlevi belge OLUŞTURMAK; listelemek ikincil.
    yeni = ""
    vurgu = ('style="background:var(--accent);color:#fff;'
             'border-color:var(--accent);margin-left:auto"')
    if auth.yetkili_mi(rol(), "/satis-faturasi"):
        yeni += f'<a class="btn" href="/satis-faturasi" {vurgu}>+ Satış faturası</a>'
        vurgu = 'style="background:var(--accent);color:#fff;border-color:var(--accent)"'
    if auth.yetkili_mi(rol(), "/uretim-fisi"):
        if not yeni:
            vurgu = ('style="background:var(--accent);color:#fff;'
                     'border-color:var(--accent);margin-left:auto"')
        yeni += f'<a class="btn" href="/uretim-fisi" {vurgu}>+ Üretim fişi</a>'
    if auth.yetkili_mi(rol(), "/iade-faturasi"):
        bosluk = "" if yeni else 'style="margin-left:auto"'
        yeni += f'<a class="btn" href="/iade-faturasi" {bosluk}>+ İade</a>' 

    if arama or bas or bit or durum:
        ozet = f"{len(belgeler)} belge bulundu"
        if arama:
            ozet += f" · \"{esc(arama)}\""
        if bas or bit:
            ozet += f" · {esc(bas or 'başlangıç')} — {esc(bit or 'bugün')}"
    else:
        ozet = f"Her stok hareketi bir belgeye dayanır — {len(belgeler)} kayıt"

    return sayfa("Belgeler", "/belgeler", f"""
      <h1>Belgeler</h1>
      <p class="sub">{ozet}</p>
      {mesajlar(params)}
      <div class="filters" style="margin-bottom:10px">{dugmeler}{yeni}</div>
      {filtre_formu}
      {belge_tablosu(belgeler, fiyat_var)}""")


def sayfa_belge_detay(params):
    """Tek belgenin dökümü."""
    fiyat_var = auth.fiyat_gorebilir(rol())
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Belge", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Geçersiz belge numarası.</div>')

    belge = q.get_belge(int(ham_id))
    if not belge:
        return sayfa("Belge", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir belge yok.</div>')

    satirlar_veri = q.get_belge_satirlari(belge["id"])
    toplam_adet = sum(x["miktar"] for x in satirlar_veri)
    toplam_tutar = sum(x["tutar"] for x in satirlar_veri)

    # Satış faturasıysa müşteri bilgisi göster
    musteri_kutusu = ""
    if belge["musteri_id"] and fiyat_var:
        musteri_kutusu = f"""
        <div class="panel" style="max-width:420px;margin:18px 0">
          <div style="font-size:12px;text-transform:uppercase;
               letter-spacing:.06em;color:var(--muted);margin-bottom:8px">
            Müşteri</div>
          <div style="font-weight:600;font-size:15px">
            {esc(belge['musteri_unvan'])}</div>
          <div style="color:var(--muted);font-size:13px;margin-top:4px">
            {esc(q.MUSTERI_TURLERI.get(belge['musteri_tur'], ''))}
            {'· ' + esc(belge['musteri_sehir']) if belge['musteri_sehir'] else ''}
            {'<br>VKN: ' + esc(belge['musteri_vergi_no']) if belge['musteri_vergi_no'] else ''}
          </div>
        </div>"""

    # Düzeltme fişiyse sebebi göster — belgenin varlık sebebi bu
    sebep_kutusu = ""
    if belge["sebep"]:
        sebep_adi = q.DUZELTME_SEBEP_HARITA.get(belge["sebep"], (belge["sebep"], ""))[0]
        sebep_kutusu = (f'<p class="sub">Düzeltme sebebi: '
                        f'<b>{esc(sebep_adi)}</b></p>')

    # İade faturasıysa hangi satıştan döndüğü, satış faturasıysa
    # kendisine kesilmiş iadeler. Bağlantı iki yönlü olsun ki her
    # iki belgeden de diğerine gidilebilsin.
    if belge["tip"] == "iade" and belge["kaynak_belge_id"]:
        sebep_kutusu += (
            f'<p class="sub">Kaynak satış faturası: '
            f'<a class="code" href="/belge?id={belge["kaynak_belge_id"]}">'
            f'{esc(belge["kaynak_belge_no"])}</a></p>')

    elif belge["tip"] == "satis":
        iadeler = q.get_belge_iadeleri(belge["id"])
        if iadeler:
            baglar = " · ".join(
                f'<a class="code" href="/belge?id={i["id"]}">'
                f'{esc(i["belge_no"])}</a>'
                f'{" (iptal)" if i["durum"] == "iptal" else ""}'
                f' — {sayi(i["toplam_miktar"])} adet'
                for i in iadeler
            )
            sebep_kutusu += f'<p class="sub">Bu faturaya kesilen iadeler: {baglar}</p>' 

    satirlar = "".join(
        f"<tr data-yol='/urun?id={x['product_id']}'>"
        f"<td><a class='code' href='/urun?id={x['product_id']}'>"
        f"{esc(x['part_code'])}</a></td>"
        f"<td>{esc(x['name'])}</td>"
        f"<td>{esc(x['location'])}</td>"
        f"<td class='num'>{sayi(x['miktar'])}</td>"
        + (f"<td class='num'>{tl(x['birim_fiyat'])}</td>"
           f"<td class='num'>{tl(x['tutar'])}</td>" if fiyat_var else "")
        + "</tr>"
        for x in satirlar_veri
    )

    if belge["tip"] == "satis" and fiyat_var:
        # Fatura toplamı: ara toplam + KDV = genel toplam
        kdv = toplam_tutar * KDV_ORANI
        toplam_satiri = (
            f"<tr style='background:#f2f4f7'>"
            f"<td colspan='3'>Ara Toplam</td><td class='num'>{sayi(toplam_adet)}</td>"
            f"<td></td><td class='num'>{tl(toplam_tutar)}</td></tr>"
            f"<tr style='background:#f2f4f7'>"
            f"<td colspan='5'>KDV (%{int(KDV_ORANI * 100)})</td>"
            f"<td class='num'>{tl(kdv)}</td></tr>"
            f"<tr style='font-weight:700;background:#e9edf2'>"
            f"<td colspan='5'>GENEL TOPLAM</td>"
            f"<td class='num'>{tl(toplam_tutar + kdv)}</td></tr>"
        )
    else:
        toplam_satiri = (
            f"<tr style='font-weight:700;background:#eef1f4'>"
            f"<td colspan='3'>TOPLAM</td>"
            f"<td class='num'>{sayi(toplam_adet)}</td>"
            + (f"<td></td><td class='num'>{tl(toplam_tutar)}</td>" if fiyat_var else "")
            + "</tr>"
        )

    # İşlem düğmeleri
    dugmeler = f'<a class="btn" href="/fatura?id={belge["id"]}">Yazdır / PDF</a>'
    if belge["durum"] == "onayli" and auth.yetkili_mi(rol(), "/belge-iptal"):
        dugmeler += (f'<a class="btn" href="/belge-iptal?id={belge["id"]}" '
                     f'style="color:var(--uyari)">Belgeyi iptal et</a>')

    return sayfa(belge["belge_no"], "/belgeler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/belgeler" style="color:var(--muted)">&larr; Belgeler</a></p>
      {mesajlar(params)}
      <h1><span class="code" style="font-size:22px">{esc(belge['belge_no'])}</span></h1>
      <p class="sub">{belge_rozeti(belge['tip'], belge['durum'])}
         &nbsp;{esc(belge['tarih'][:16])} · {esc(belge['ad_soyad'])}</p>
      {f'<p class="sub">{esc(belge["aciklama"])}</p>' if belge['aciklama'] else ''}
      {musteri_kutusu}{sebep_kutusu}
      <div class="cards" style="margin:18px 0">
        <div class="card"><span>Kalem</span><strong>{len(satirlar_veri)}</strong></div>
        <div class="card"><span>Toplam Adet</span><strong>{sayi(toplam_adet)}</strong></div>
        {f"<div class='card'><span>Toplam Tutar</span><strong>{tl(toplam_tutar)}</strong></div>" if fiyat_var else ""}
      </div>
      <div class="filters" style="margin:18px 0">{dugmeler}</div>
      <h2>Kalemler</h2>
      <table>
        <tr><th>Parça Kodu</th><th>Ürün Adı</th><th>Lokasyon</th>
            <th class="num">Miktar</th>
            {'<th class="num">Birim Fiyat</th><th class="num">Tutar</th>' if fiyat_var else ''}
        </tr>
        {satirlar}{toplam_satiri}
      </table>""")


SATIR_SAYISI = 8   # formda kaç boş satır gösterilecek


def sayfa_uretim_fisi(degerler=None, hata="", on_secim=""):
    """Üretim fişi oluşturma ekranı — çok satırlı form.

    Şimdiye kadarki formlar tek kayıt içindi. Burada bir belgede
    birden fazla ürün var, yani forma AYNI ADI TAŞIYAN birden çok
    alan koyuyoruz:

        <select name="urun">  ... 8 tane
        <input  name="miktar"> ... 8 tane

    parse_qs bunları liste olarak topluyor:
        {"urun": ["5", "12", ""], "miktar": ["10", "4", ""]}

    Sonra ikisini zip() ile eşleştirip satırları kuruyoruz. Boş
    bırakılanlar atlanıyor — kullanıcı 8 satırın hepsini doldurmak
    zorunda değil.
    """
    d = degerler or {}
    urunler = q.get_product_options()

    # Kullanıcı JavaScript ile satır eklediyse ve form hata verdiyse,
    # eklediği satırlar kaybolmasın: gelen veri kadar satır basıyoruz.
    satir_adedi = max(SATIR_SAYISI, len(d.get("urun") or []))

    def satir(i):
        secili = (d.get("urun") or [])[i] if i < len(d.get("urun") or []) else ""
        # Ürün detayından "Üretim fişine ekle" ile gelinmişse ilk satır
        # o ürünle dolu gelsin — kullanıcı 180 ürün arasında aramasın.
        if not secili and i == 0 and on_secim:
            secili = on_secim
        miktar = (d.get("miktar") or [])[i] if i < len(d.get("miktar") or []) else ""
        secenekler = '<option value="">— seç —</option>' + "".join(
            f'<option value="{pid}"{" selected" if str(pid) == secili else ""}>'
            f'{esc(etiket)}</option>'
            for pid, etiket in urunler
        )
        return f"""<tr>
          <td class="num" style="color:var(--muted)">{i + 1}</td>
          <td><select name="urun">{secenekler}</select></td>
          <td><input name="miktar" type="number" min="1" step="1"
                     value="{esc(miktar)}" placeholder="0"
                     style="width:110px;text-align:right"></td>
        </tr>"""

    return sayfa("Üretim Fişi", "/belgeler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/belgeler" style="color:var(--muted)">&larr; Belgeler</a></p>
      <h1>Yeni Üretim Fişi</h1>
      <p class="sub">Üretimden depoya giren parçalar. Belge numarası
         kaydedildiğinde otomatik verilir.</p>
      {f'<div class="msg err">{esc(hata)}</div>' if hata else ''}
      <form method="post" action="/uretim-fisi">{csrf_alani()}
        <div class="panel" style="max-width:none;margin-bottom:18px">
          <label>Açıklama
            <input name="aciklama" maxlength="150"
                   value="{esc(d.get('aciklama', [''])[0] if isinstance(d.get('aciklama'), list) else d.get('aciklama', ''))}"
                   placeholder="Örn: 3 numaralı hat, gece vardiyası"></label>
        </div>
        <h2>Kalemler</h2>
        <table>
          <thead>
            <tr><th style="width:40px">#</th><th>Ürün</th>
                <th style="width:140px">Miktar</th></tr>
          </thead>
          <tbody id="kalemler">
            {"".join(satir(i) for i in range(satir_adedi))}
          </tbody>
        </table>
        <div style="margin-top:12px">
          <button type="button" id="satir-ekle" class="btn" hidden
                  style="cursor:pointer">+ Satır ekle</button>
        </div>
        <div style="margin-top:20px;display:flex;gap:10px">
          <button class="birincil">Fişi kaydet</button>
          <a class="btn" href="/belgeler">Vazgeç</a>
        </div>
        <p class="sub" style="margin-top:14px;font-size:13px">
          Boş satırlar yok sayılır. Kaydedilen fiş stok girişi olarak
          işlenir ve geri alınamaz — hatalı fiş için iptal kaydı gerekir.</p>
      </form>""")



KDV_ORANI = 0.20   # yüzde 20


def sayfa_satis_faturasi(degerler=None, hata="", on_secim=""):
    """Satış faturası kesme ekranı.

    Üretim fişinden farkı: müşteri seçimi ve fiyat/tutar var.
    Aynı çok satırlı form kalıbı, ama bu belge parayla ilgili olduğu
    için birim fiyat da satır bazında girilebiliyor — kampanya veya
    özel anlaşma fiyatı olabilir. Boş bırakılırsa ürünün liste fiyatı
    kullanılıyor.
    """
    d = degerler or {}
    urunler = q.get_product_options()
    musteriler = q.list_musteriler()

    # Hata sonrası kullanıcının eklediği satırlar kaybolmasın
    satir_adedi = max(SATIR_SAYISI, len(d.get("urun") or []))

    secili_musteri = (d.get("musteri") or [""])[0] if isinstance(d.get("musteri"), list) else d.get("musteri", "")
    musteri_secenek = '<option value="">— müşteri seç —</option>' + "".join(
        f'<option value="{m["id"]}"{" selected" if str(m["id"]) == str(secili_musteri) else ""}>'
        f'{esc(m["unvan"])} · {esc(q.MUSTERI_TURLERI.get(m["tur"], m["tur"]))}'
        f'{" · " + esc(m["sehir"]) if m["sehir"] else ""}</option>'
        for m in musteriler
    )

    def satir(i):
        def deger(ad):
            liste = d.get(ad) or []
            return liste[i] if i < len(liste) else ""

        secili = deger("urun")
        if not secili and i == 0 and on_secim:
            secili = on_secim
        secenekler = '<option value="">— seç —</option>' + "".join(
            f'<option value="{pid}"{" selected" if str(pid) == secili else ""}>'
            f'{esc(etiket)}</option>'
            for pid, etiket in urunler
        )
        return f"""<tr>
          <td class="num" style="color:var(--muted)">{i + 1}</td>
          <td><select name="urun">{secenekler}</select></td>
          <td><input name="miktar" type="number" min="1" step="1"
                     value="{esc(deger('miktar'))}" placeholder="0"
                     style="width:100px;text-align:right"></td>
          <td><input name="fiyat" type="text" value="{esc(deger('fiyat'))}"
                     placeholder="liste fiyatı"
                     style="width:130px;text-align:right"></td>
        </tr>"""

    aciklama = d.get("aciklama", [""])
    aciklama = aciklama[0] if isinstance(aciklama, list) else aciklama

    return sayfa("Satış Faturası", "/belgeler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/belgeler" style="color:var(--muted)">&larr; Belgeler</a></p>
      <h1>Yeni Satış Faturası</h1>
      <p class="sub">Depodan çıkan ve müşteriye sevk edilen parçalar.
         Stokta olmayan miktar faturalanamaz.</p>
      {f'<div class="msg err">{esc(hata)}</div>' if hata else ''}
      <form method="post" action="/satis-faturasi">{csrf_alani()}
        <div class="panel" style="max-width:none;margin-bottom:18px">
          <label>Müşteri *
            <select name="musteri" required>{musteri_secenek}</select></label>
          <label>Açıklama
            <input name="aciklama" maxlength="150" value="{esc(aciklama)}"
                   placeholder="Örn: Sipariş no 2026-441, kısmi sevkiyat"></label>
        </div>
        <h2>Kalemler</h2>
        <table>
          <thead>
            <tr><th style="width:40px">#</th><th>Ürün</th>
                <th style="width:120px">Miktar</th>
                <th style="width:150px">Birim Fiyat</th></tr>
          </thead>
          <tbody id="kalemler">
            {"".join(satir(i) for i in range(satir_adedi))}
          </tbody>
        </table>
        <div style="margin-top:12px">
          <button type="button" id="satir-ekle" class="btn" hidden
                  style="cursor:pointer">+ Satır ekle</button>
        </div>
        <div style="margin-top:20px;display:flex;gap:10px">
          <button class="birincil">Faturayı kes</button>
          <a class="btn" href="/belgeler">Vazgeç</a>
        </div>
        <p class="sub" style="margin-top:14px;font-size:13px">
          Birim fiyat boş bırakılırsa ürünün liste fiyatı kullanılır.
          KDV (%{int(KDV_ORANI * 100)}) fatura dökümünde hesaplanır.</p>
      </form>""")


def sayfa_iade_faturasi(params, degerler=None, hata=""):
    """İade faturası ekranı. İki adımlı:

        /iade-faturasi                -> hangi satış faturasından iade?
        /iade-faturasi?satis=12       -> o faturanın kalemleri, iade miktarları

    Neden önce fatura seçtiriyoruz? Çünkü iade havada duran bir kayıt
    olamaz — hangi satıştan döndüğü belli olmalı. Ürün listesinden
    serbestçe seçtirseydik, satılmamış bir ürün de iade edilebilirdi.

    Ayrıca faturayı seçince ekran kullanıcıya "bu üründen en fazla şu
    kadar iade edebilirsin" diyebiliyor. Kural zaten belge_olustur
    içinde var ama formu doldurup kaydettikten sonra hata almak,
    baştan uyarılmaktan kötü bir deneyim.
    """
    d = degerler or {}
    ham_satis = params.get("satis", [""])[0]
    if isinstance(d.get("satis"), list):
        ham_satis = d["satis"][0] or ham_satis

    # --- 1. adım: fatura seçimi ---
    if not ham_satis.isdigit():
        faturalar = q.iade_edilebilir_faturalar()
        if not faturalar:
            govde = ('<div class="empty">İade kesilebilecek satış faturası '
                     'yok.</div>')
        else:
            satirlar = "".join(
                f"<tr data-yol='/iade-faturasi?satis={b['id']}'>"
                f"<td><a class='code' href='/iade-faturasi?satis={b['id']}'>"
                f"{esc(b['belge_no'])}</a></td>"
                f"<td class='num' style='text-align:left'>{esc(b['tarih'][:16])}</td>"
                f"<td>{esc(b['musteri_unvan'])}</td>"
                f"<td class='num'>{b['satir_sayisi']}</td>"
                f"<td class='num'>{tl(b['toplam_tutar'])}</td></tr>"
                for b in faturalar
            )
            govde = f"""<table>
              <tr><th>Fatura No</th><th>Tarih</th><th>Müşteri</th>
                  <th class="num">Kalem</th><th class="num">Tutar</th></tr>
              {satirlar}</table>"""

        return sayfa("İade Faturası", "/belgeler", f"""
          <p class="sub" style="margin-bottom:6px">
            <a href="/belgeler" style="color:var(--muted)">&larr; Belgeler</a></p>
          <h1>İade Faturası</h1>
          <p class="sub">Önce iadenin hangi satıştan döndüğünü seç.
             İptal edilmiş faturalar listede yok — iptal edilmiş bir
             satıştan iade alınamaz.</p>
          {govde}""")

    # --- 2. adım: kalemler ---
    satis = q.get_belge(int(ham_satis))
    if not satis or satis["tip"] != "satis":
        return sayfa("İade Faturası", "/belgeler",
                     "<h1>Fatura bulunamadı</h1>"
                     '<div class="empty">Geçerli bir satış faturası seçilmedi.</div>')

    kalemler = q.iade_edilebilir_satirlar(satis["id"])

    def satir(i, k):
        kalan = k["satilan"] - k["iade_edilen"]
        girilen = ""
        liste = d.get("miktar") or []
        if i < len(liste):
            girilen = liste[i]

        if kalan <= 0:
            miktar_hucre = ("<span style='color:var(--muted)'>tamamı iade "
                            "edilmiş</span>"
                            f"<input type='hidden' name='urun' value=''>"
                            f"<input type='hidden' name='miktar' value=''>")
        else:
            miktar_hucre = (
                f"<input type='hidden' name='urun' value='{k['product_id']}'>"
                f"<input name='miktar' type='number' min='0' max='{kalan}' "
                f"value='{esc(girilen)}' placeholder='0' "
                f"style='width:100px;text-align:right'>")

        return (f"<tr><td class='code'>{esc(k['part_code'])}</td>"
                f"<td>{esc(k['name'])}</td>"
                f"<td class='num'>{k['satilan']}</td>"
                f"<td class='num'>{k['iade_edilen'] or '—'}</td>"
                f"<td class='num'><b>{kalan}</b></td>"
                f"<td class='num'>{tl(k['birim_fiyat'])}</td>"
                f"<td>{miktar_hucre}</td></tr>")

    aciklama = d.get("aciklama", [""])
    aciklama = aciklama[0] if isinstance(aciklama, list) else aciklama

    return sayfa("İade Faturası", "/belgeler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/iade-faturasi" style="color:var(--muted)">&larr; Fatura seçimi</a></p>
      <h1>İade Faturası</h1>
      <p class="sub">
        <span class="code">{esc(satis['belge_no'])}</span> ·
        {esc(satis['musteri_unvan'])} · {esc(satis['tarih'][:16])}</p>
      {f'<div class="msg err">{esc(hata)}</div>' if hata else ''}
      <div class="msg" style="background:#eef4fa;border:1px solid #cfe0f0;
           color:var(--navy);margin-bottom:18px">
        İade edilen mal <b>doğrudan stoğa girer.</b> Depo malı teslim alıp
        kontrol eder; hasarlı çıkarsa Stok Düzeltme ekranından
        <b>Fire / hurda ayrımı</b> fişi keser.
      </div>
      <form method="post" action="/iade-faturasi">
        {csrf_alani()}
        <input type="hidden" name="satis" value="{satis['id']}">
        <div class="panel" style="max-width:none;margin-bottom:18px">
          <label>İade Sebebi / Açıklama
            <input name="aciklama" maxlength="150" value="{esc(aciklama)}"
                   placeholder="Örn: Hatalı sevkiyat, müşteri talebi"></label>
        </div>
        <h2>Kalemler</h2>
        <table>
          <tr><th>Parça Kodu</th><th>Ürün Adı</th>
              <th class="num">Satılan</th><th class="num">İade Edilen</th>
              <th class="num">Kalan Hak</th><th class="num">Birim Fiyat</th>
              <th style="width:130px">İade Miktarı</th></tr>
          {"".join(satir(i, k) for i, k in enumerate(kalemler))}
        </table>
        <div style="margin-top:20px;display:flex;gap:10px">
          <button class="birincil">İade faturasını kes</button>
          <a class="btn" href="/iade-faturasi">Vazgeç</a>
        </div>
        <p class="sub" style="margin-top:14px;font-size:13px">
          Boş bırakılan veya 0 girilen kalemler iade edilmez.
          Fiyat, orijinal faturadaki fiyattır — sonradan değişmiş olsa bile
          iade o fiyattan yapılır.</p>
      </form>""")


def sayfa_musteriler():
    """Müşteri listesi ve satış özeti."""
    ozet = q.musteri_satis_ozeti()

    satirlar = "".join(
        f"<tr>"
        f"<td>{esc(m['unvan'])}</td>"
        f"<td><span class='pill s-ok'>{esc(q.MUSTERI_TURLERI.get(m['tur'], m['tur']))}</span></td>"
        f"<td>{esc(m['sehir'] or '-')}</td>"
        f"<td class='num'>{m['fatura_sayisi']}</td>"
        f"<td class='num'>{sayi(m['toplam_adet'])}</td>"
        f"<td class='num'>{tl(m['toplam_tutar'])}</td>"
        f"<td class='num' style='text-align:left;color:var(--muted)'>"
        f"{esc(m['son_satis'][:10] if m['son_satis'] else '—')}</td></tr>"
        for m in ozet
    )

    toplam = sum(m["toplam_tutar"] for m in ozet)
    return sayfa("Müşteriler", "/musteriler", f"""
      <h1>Müşteriler</h1>
      <p class="sub">{len(ozet)} müşteri · toplam satış {tl(toplam)}</p>
      <table>
        <tr><th>Ünvan</th><th>Tür</th><th>Şehir</th>
            <th class="num">Fatura</th><th class="num">Adet</th>
            <th class="num">Tutar</th><th>Son Satış</th></tr>
        {satirlar}
      </table>
      <p class="sub" style="margin-top:14px;font-size:13px">
        Müşteri bilgileri temsilîdir; gerçek firma verisi kullanılmamıştır.</p>""")



def sayfa_belge_iptal(params):
    """İptal onay ekranı.

    Silme onayında olduğu gibi: yıkıcı işlem tek tıkla yapılmaz.
    Farkı, burada önce iptalin MÜMKÜN olup olmadığını kontrol edip
    kullanıcıya söylüyoruz — "Evet, iptal et" dedikten sonra hata
    almak, baştan uyarılmaktan kötü bir deneyim.
    """
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Belge", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Geçersiz belge numarası.</div>')

    belge = q.get_belge(int(ham_id))
    if not belge:
        return sayfa("Belge", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir belge yok.</div>')

    satirlar = q.get_belge_satirlari(belge["id"])
    toplam_adet = sum(x["miktar"] for x in satirlar)
    edilebilir, engel = q.belge_iptal_edilebilir_mi(belge["id"])

    ilk = q.get_movements(limit=1, product_id=None)
    yon_metni = ("Bu belge stoğa giriş yapmıştı; iptal edildiğinde "
                 "aynı miktar stoktan düşülecek."
                 if belge["tip"] in ("uretim", "acilis")
                 else "Bu belge stoktan çıkış yapmıştı; iptal edildiğinde "
                      "aynı miktar stoğa geri eklenecek.")

    if not edilebilir:
        govde = f"""
          <div class="msg err">{esc(engel)}</div>
          <div class="panel">
            <p style="font-size:14px;margin-bottom:14px">
              Bu belge şu an iptal edilemiyor.</p>
            <a class="btn" href="/belge?id={belge['id']}">Belgeye dön</a>
          </div>"""
    else:
        govde = f"""
          <div class="panel">
            <p style="font-size:15px;margin-bottom:14px">
              <span class="code">{esc(belge['belge_no'])}</span> ·
              {len(satirlar)} kalem · {sayi(toplam_adet)} adet</p>
            <div class="msg err" style="margin-bottom:18px">
              <b>Bu işlem geri alınamaz.</b><br>
              {esc(yon_metni)} Belge silinmez; listede
              <b>İPTAL</b> damgasıyla kalır ve ters hareketler kaydedilir.
            </div>
            <form method="post" action="/belge-iptal">{csrf_alani()}
              <input type="hidden" name="id" value="{belge['id']}">
              <label style="display:block;font-size:13px;font-weight:600;
                     margin-bottom:5px">İptal sebebi *</label>
              <input name="sebep" required maxlength="120" style="width:100%"
                     placeholder="Örn: Hatalı miktar girildi, yeniden kesilecek">
              <div style="margin-top:18px;display:flex;gap:10px">
                <button style="background:var(--uyari)">Evet, iptal et</button>
                <a class="btn" href="/belge?id={belge['id']}">Vazgeç</a>
              </div>
            </form>
          </div>"""

    return sayfa("Belge İptal", "/belgeler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/belge?id={belge['id']}" style="color:var(--muted)">
          &larr; {esc(belge['belge_no'])}</a></p>
      <h1>Belgeyi iptal et?</h1>
      <p class="sub">{belge_rozeti(belge['tip'])} · {esc(belge['tarih'][:16])}</p>
      {govde}""")


def sayfa_fatura_yazdir(params):
    """Yazdırılabilir fatura dökümü.

    Menüsüz, tam sayfa. CSS'teki @media print kuralları ekrandaki
    fazlalıkları gizliyor; tarayıcının "PDF olarak kaydet" seçeneğiyle
    PDF üretiliyor. Ayrı bir PDF kütüphanesine gerek yok.
    """
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Fatura", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Geçersiz belge numarası.</div>')

    belge = q.get_belge(int(ham_id))
    if not belge:
        return sayfa("Fatura", "/belgeler", '<h1>Belge bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir belge yok.</div>')

    satirlar = q.get_belge_satirlari(belge["id"])
    ara_toplam = sum(x["tutar"] for x in satirlar)
    kdv = ara_toplam * KDV_ORANI
    genel = ara_toplam + kdv

    kalemler = "".join(
        f"<tr><td class='num' style='text-align:left'>{i}</td>"
        f"<td class='code'>{esc(x['part_code'])}</td>"
        f"<td>{esc(x['name'])}</td>"
        f"<td class='num'>{sayi(x['miktar'])}</td>"
        f"<td class='num'>{tl(x['birim_fiyat'])}</td>"
        f"<td class='num'>{tl(x['tutar'])}</td></tr>"
        for i, x in enumerate(satirlar, start=1)
    )

    if belge["musteri_id"]:
        musteri_bilgi = f"""
          <div style="font-weight:600">{esc(belge['musteri_unvan'])}</div>
          <div>{esc(q.MUSTERI_TURLERI.get(belge['musteri_tur'], ''))}
               {'· ' + esc(belge['musteri_sehir']) if belge['musteri_sehir'] else ''}</div>
          {f"<div>VKN: {esc(belge['musteri_vergi_no'])}</div>" if belge['musteri_vergi_no'] else ''}"""
    else:
        musteri_bilgi = "<div style='color:var(--muted)'>—</div>"

    iptal_damgasi = ""
    if belge["durum"] == "iptal":
        iptal_damgasi = """
          <div style="position:absolute;top:150px;left:50%;
               transform:translateX(-50%) rotate(-18deg);
               font-size:76px;font-weight:800;color:rgba(200,32,47,.16);
               letter-spacing:.12em;pointer-events:none">İPTAL</div>"""

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(belge['belge_no'])}</title><style>{CSS}
body{{background:#e9ecef;padding:24px}}
.kagit{{position:relative;max-width:800px;margin:0 auto;background:#fff;
       padding:44px;border:1px solid var(--rule);border-radius:4px}}
.fatura-ust{{display:flex;justify-content:space-between;
            border-bottom:3px solid var(--navy);padding-bottom:18px}}
.fatura-ust b{{font-size:17px;letter-spacing:.08em;text-transform:uppercase;
              color:var(--navy)}}
.fatura-ust b s{{color:var(--accent);text-decoration:none}}
.taraf{{display:grid;grid-template-columns:1fr 1fr;gap:30px;margin:26px 0}}
.taraf h3{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
          color:var(--muted);margin-bottom:7px;font-weight:600}}
.taraf div{{font-size:14px;line-height:1.7}}
.toplamlar{{margin-top:20px;margin-left:auto;width:320px}}
.toplamlar tr td{{padding:7px 12px;font-size:14px}}
.toplamlar tr:last-child td{{font-weight:700;font-size:16px;
                            border-top:2px solid var(--navy)}}
.dipnot{{margin-top:36px;padding-top:16px;border-top:1px solid var(--rule);
        font-size:12px;color:var(--muted);line-height:1.7}}
@media print{{
  body{{background:#fff;padding:0}}
  .kagit{{border:0;border-radius:0;padding:0;max-width:none}}
  .noprint{{display:none!important}}
}}
</style></head><body>
<div class="noprint" style="max-width:800px;margin:0 auto 16px;display:flex;gap:10px">
  <a class="btn" href="/belge?id={belge['id']}">&larr; Belgeye dön</a>
  <button onclick="window.print()" class="birincil">Yazdır / PDF kaydet</button>
</div>
<div class="kagit">
  {iptal_damgasi}
  <div class="fatura-ust">
    <div>
      <b>TEKNOROT<s>/</s>STOK</b>
      <div style="font-size:13px;color:var(--muted);margin-top:5px">
        Otomotiv Yedek Parça Sanayi</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
           color:var(--muted)">{esc(q.belge_tipi_adi(belge['tip']))}</div>
      <div style="font:700 20px var(--mono);color:var(--navy);margin-top:3px">
        {esc(belge['belge_no'])}</div>
      <div style="font-size:13px;color:var(--muted);margin-top:3px">
        {esc(belge['tarih'][:16])}</div>
    </div>
  </div>

  <div class="taraf">
    <div><h3>Satıcı</h3>
      <div><b>Teknorot Otomotiv Sanayi</b><br>
        Düzce Organize Sanayi Bölgesi<br>Düzce / Türkiye</div></div>
    <div><h3>Alıcı</h3><div>{musteri_bilgi}</div></div>
  </div>

  <table>
    <tr><th style="width:36px">#</th><th>Parça Kodu</th><th>Ürün Adı</th>
        <th class="num">Miktar</th><th class="num">Birim Fiyat</th>
        <th class="num">Tutar</th></tr>
    {kalemler}
  </table>

  <table class="toplamlar">
    <tr><td>Ara Toplam</td><td class="num">{tl(ara_toplam)}</td></tr>
    <tr><td>KDV (%{int(KDV_ORANI * 100)})</td><td class="num">{tl(kdv)}</td></tr>
    <tr><td>Genel Toplam</td><td class="num">{tl(genel)}</td></tr>
  </table>

  <div class="dipnot">
    {f"<b>Açıklama:</b> {esc(belge['aciklama'])}<br>" if belge['aciklama'] else ''}
    Düzenleyen: {esc(belge['ad_soyad'])}<br>
    Bu belge Teknorot Stok Takip Sistemi tarafından oluşturulmuştur.
    Örnek veri içerir, mali değeri yoktur.
  </div>
</div></body></html>"""


def stok_grafigi(veri, genislik=980, yukseklik=260):
    """Stok seyri grafiğini SVG olarak çizer. Kütüphane yok.

    Grafik kütüphanesi kuramadığımız için SVG'yi elle üretiyoruz.
    Aslında bir çizgi grafiği matematiksel olarak basit: veri
    değerlerini piksel koordinatlarına ÖLÇEKLEMEK.

        x = sol_kenar + (indeks / son_indeks) * çizim_genişliği
        y = alt_kenar - (deger / en_buyuk) * çizim_yüksekliği

    y ekseninde çıkarma var çünkü SVG'de y aşağı doğru artar — 0 en
    üsttedir. Grafikte ise büyük değer yukarıda olmalı.
    """
    if len(veri) < 2:
        return '<div class="empty">Grafik için yeterli veri yok.</div>'

    sol, sag, ust, alt = 64, 16, 16, 34
    cizim_g = genislik - sol - sag
    cizim_y = yukseklik - ust - alt

    stoklar = [g["stok"] for g in veri]
    en_buyuk, en_kucuk = max(stoklar), min(stoklar)

    # Ekseni biraz genişletiyoruz ki çizgi kenarlara yapışmasın
    tavan = en_buyuk * 1.06
    taban = max(0, en_kucuk - (en_buyuk - en_kucuk) * 0.15)
    aralik = tavan - taban or 1

    def x(i):
        return sol + (i / (len(veri) - 1)) * cizim_g

    def y(deger):
        return ust + cizim_y - ((deger - taban) / aralik) * cizim_y

    noktalar = " ".join(f"{x(i):.1f},{y(g['stok']):.1f}"
                        for i, g in enumerate(veri))

    cizgi = (f'<polyline points="{noktalar}" fill="none" stroke="#0e2c4b" '
             f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')

    # Çizginin altını dolduran alan: aynı noktalar + alt köşelere kapanış
    alan = (f'<polygon points="{sol},{ust + cizim_y} {noktalar} '
            f'{sol + cizim_g},{ust + cizim_y}" fill="#0e2c4b" opacity="0.07"/>')

    izgara = ""
    for i in range(5):
        deger = taban + (aralik * i / 4)
        yy = y(deger)
        izgara += (f'<line x1="{sol}" y1="{yy:.1f}" x2="{genislik - sag}" '
                   f'y2="{yy:.1f}" stroke="#dde3e9" stroke-width="1"/>'
                   f'<text x="{sol - 8}" y="{yy + 4:.1f}" text-anchor="end" '
                   f'font-size="11" fill="#66717e">{sayi(round(deger))}</text>')

    etiketler = ""
    for i in (0, len(veri) // 2, len(veri) - 1):
        hiza = "start" if i == 0 else ("end" if i == len(veri) - 1 else "middle")
        etiketler += (f'<text x="{x(i):.1f}" y="{yukseklik - 12}" '
                      f'text-anchor="{hiza}" font-size="11" fill="#66717e">'
                      f'{esc(veri[i]["gun"])}</text>')

    isaret = (f'<circle cx="{x(len(veri) - 1):.1f}" cy="{y(veri[-1]["stok"]):.1f}" '
              f'r="4" fill="#4f9d2e"/>')

    return ('<div style="background:#fff;border:1px solid var(--rule);'
            'border-radius:6px;padding:14px;overflow-x:auto">'
            f'<svg viewBox="0 0 {genislik} {yukseklik}" width="100%" '
            f'height="{yukseklik}" role="img" aria-label="Stok seyri grafiği">'
            f'{izgara}{alan}{cizgi}{isaret}{etiketler}</svg></div>')


def sayfa_stok_seyri():
    """Stok seyri: grafik + aylık özet."""
    veri = q.stok_seyri()
    aylar = q.aylik_ozet()

    if veri:
        ilk, son = veri[0]["stok"], veri[-1]["stok"]
        degisim = son - ilk
        toplam_giris = sum(g["giris"] for g in veri)
        toplam_cikis = sum(g["cikis"] for g in veri)
    else:
        ilk = son = degisim = toplam_giris = toplam_cikis = 0

    renk = "var(--ok)" if degisim >= 0 else "var(--uyari)"
    isaret = "+" if degisim >= 0 else ""

    kartlar = (
        '<div class="cards">'
        f'<div class="card"><span>Güncel Stok</span><strong>{sayi(son)}</strong></div>'
        f'<div class="card"><span>Dönem Başı</span><strong>{sayi(ilk)}</strong></div>'
        f'<div class="card"><span>Değişim</span>'
        f'<strong style="color:{renk}">{isaret}{sayi(degisim)}</strong></div>'
        f'<div class="card"><span>Giriş / Çıkış</span>'
        f'<strong style="font-size:19px">{sayi(toplam_giris)} / '
        f'{sayi(toplam_cikis)}</strong></div></div>'
    )

    ay_satirlari = "".join(
        f"<tr><td class='code'>{esc(a['ay'])}</td>"
        f"<td class='num' style='color:var(--ok)'>+{sayi(a['giris'])}</td>"
        f"<td class='num' style='color:var(--uyari)'>-{sayi(a['cikis'])}</td>"
        f"<td class='num'><b>{'+' if a['giris'] >= a['cikis'] else ''}"
        f"{sayi(a['giris'] - a['cikis'])}</b></td>"
        f"<td class='num'>{a['hareket_sayisi']}</td></tr>"
        for a in aylar
    )

    return sayfa("Stok Seyri", "/seyir",
        "<h1>Stok Seyri</h1>"
        '<p class="sub">Depodaki toplam adedin zaman içindeki değişimi</p>'
        + kartlar
        + "<h2>Günlük seyir</h2>" + stok_grafigi(veri)
        + "<h2>Aylık özet</h2><table>"
        '<tr><th>Ay</th><th class="num">Giriş</th><th class="num">Çıkış</th>'
        '<th class="num">Net</th><th class="num">Hareket</th></tr>'
        + ay_satirlari + "</table>")


def kullanici_formu(degerler=None, hata="", duzenle=None):
    """Kullanıcı ekleme / düzenleme formu — tek fonksiyon, iki mod."""
    d = degerler or {}

    def al(ad):
        v = d.get(ad, "")
        return v[0] if isinstance(v, list) else (v or "")

    duzenleme = duzenle is not None
    secili_rol = al("rol") or (duzenle["rol"] if duzenleme else "")
    rol_secenek = "".join(
        f'<option value="{kod}"{" selected" if kod == secili_rol else ""}>'
        f'{esc(ad)}</option>' for kod, ad in auth.ROLLER.items()
    )
    kullanici_adi = al("kullanici_adi") or (duzenle["kullanici_adi"] if duzenleme else "")
    ad_soyad = al("ad_soyad") or (duzenle["ad_soyad"] if duzenleme else "")

    hata_kutusu = f'<div class="msg err">{esc(hata)}</div>' if hata else ""
    gizli = f'<input type="hidden" name="id" value="{duzenle["id"]}">' if duzenleme else ""
    aksiyon = "/kullanici-duzenle" if duzenleme else "/kullanici-ekle"

    if duzenleme:
        uyari = ('<p class="sub" style="font-size:12px;margin:4px 0 0">'
                 'Kullanıcı adı değiştirilemez — geçmiş belgelerdeki '
                 '&quot;kim yaptı&quot; izi bozulmasın diye.</p>')
        parola_alani = ""
    else:
        uyari = ""
        parola_alani = ('<label>Parola *<input name="parola" type="password" '
                        'required minlength="5" placeholder="en az 5 karakter">'
                        '</label>')

    return (
        hata_kutusu +
        f'<form method="post" action="{aksiyon}" class="panel" style="max-width:520px">{csrf_alani()}'
        + gizli +
        '<label>Kullanıcı Adı *<input name="kullanici_adi" '
        f'value="{esc(kullanici_adi)}" '
        + ("disabled" if duzenleme else "required") +
        ' placeholder="depo2" maxlength="30"></label>' + uyari +
        f'<label>Ad Soyad *<input name="ad_soyad" value="{esc(ad_soyad)}" '
        'required placeholder="Mehmet Yılmaz" maxlength="60"></label>'
        '<label>Rol *<select name="rol" required>'
        f'<option value="">— rol seç —</option>{rol_secenek}</select></label>'
        + parola_alani +
        '<div style="margin-top:20px;display:flex;gap:10px">'
        f'<button class="birincil">{"Kaydet" if duzenleme else "Kullanıcı ekle"}</button>'
        '<a class="btn" href="/kullanicilar">Vazgeç</a></div></form>'
    )


def sayfa_kullanici_ekle(degerler=None, hata=""):
    return sayfa("Yeni Kullanıcı", "/kullanicilar",
        '<p class="sub" style="margin-bottom:6px">'
        '<a href="/kullanicilar" style="color:var(--muted)">&larr; Kullanıcılar</a></p>'
        "<h1>Yeni Kullanıcı</h1>"
        '<p class="sub">Parola veritabanına özetlenerek kaydedilir, '
        'düz metin tutulmaz</p>'
        + kullanici_formu(degerler, hata))


def sayfa_kullanici_duzenle(params, degerler=None, hata=""):
    ham_id = params.get("id", [""])[0]
    if not ham_id.isdigit():
        return sayfa("Kullanıcı", "/kullanicilar", "<h1>Kullanıcı bulunamadı</h1>"
                     '<div class="empty">Geçersiz numara.</div>')

    kullanici = q.get_user(int(ham_id))
    if not kullanici:
        return sayfa("Kullanıcı", "/kullanicilar", "<h1>Kullanıcı bulunamadı</h1>"
                     '<div class="empty">Bu numarada kullanıcı yok.</div>')

    belge_sayisi = q.user_belge_sayisi(kullanici["id"])
    kendisi = kullanici["id"] == (aktif_kullanici() or {}).get("id")
    acik = bool(kullanici["aktif"])

    # Parola sıfırlama ve hesap kapatma AYRI formlar — farklı işlemler
    # aynı formda toplanırsa yanlışlıkla tetiklenme riski artar.
    parola_formu = (
        "<h2>Parola sıfırla</h2>"
        '<form method="post" action="/kullanici-parola" class="panel" '
        'style="max-width:520px">' + csrf_alani() +
        f'<input type="hidden" name="id" value="{kullanici["id"]}">'
        '<label>Yeni Parola<input name="parola" type="password" required '
        'minlength="5" placeholder="en az 5 karakter"></label>'
        '<div style="margin-top:18px"><button>Parolayı değiştir</button></div>'
        '<p class="sub" style="margin-top:12px;font-size:13px">'
        'Eski parola sorulmuyor çünkü bu işlemi yönetici yapıyor. '
        'Kullanıcı kendi parolasını değiştirseydi eski parola sorulurdu.</p>'
        '</form>'
    )

    if kendisi:
        dugme = ('<button disabled style="opacity:.4;cursor:not-allowed">'
                 'Hesabı kapat</button>')
        uyari = ('<div class="msg err" style="margin-bottom:14px">'
                 'Kendi hesabını kapatamazsın.</div>')
    else:
        stil = ' style="background:var(--uyari)"' if acik else ""
        dugme = f'<button{stil}>{"Hesabı kapat" if acik else "Hesabı aç"}</button>'
        uyari = ""

    durum_formu = (
        "<h2>Hesap durumu</h2>"
        '<div class="panel" style="max-width:520px">'
        f'<p style="font-size:14px;margin-bottom:14px">Bu hesap '
        f'<b>{"açık" if acik else "kapalı"}</b>. {belge_sayisi} belge '
        f'oluşturmuş.</p>' + uyari +
        '<form method="post" action="/kullanici-durum">' + csrf_alani() +
        f'<input type="hidden" name="id" value="{kullanici["id"]}">'
        f'<input type="hidden" name="aktif" value="{0 if acik else 1}">'
        + dugme + '</form>'
        '<p class="sub" style="margin-top:12px;font-size:13px">'
        'Kullanıcı silinmez, pasife alınır. Silinseydi oluşturduğu '
        f'{belge_sayisi} belge sahipsiz kalırdı.</p></div>'
    )

    return sayfa(f"Düzenle · {kullanici['kullanici_adi']}", "/kullanicilar",
        '<p class="sub" style="margin-bottom:6px">'
        '<a href="/kullanicilar" style="color:var(--muted)">&larr; Kullanıcılar</a></p>'
        "<h1>Kullanıcıyı Düzenle</h1>"
        f'<p class="sub"><span class="code">{esc(kullanici["kullanici_adi"])}</span></p>'
        + mesajlar(params)
        + kullanici_formu(degerler, hata, duzenle=kullanici)
        + parola_formu + durum_formu)


def sayfa_urun_detay(params):
    """Tek bir ürünün kartı: bilgiler, stok özeti ve tüm hareket geçmişi.

    Adres şöyle: /urun?id=42
    params["id"] bir LİSTE olarak gelir (["42"]), çünkü aynı isim
    adreste birden fazla kez geçebilir. Bu yüzden sondaki [0].
    """
    ham_id = params.get("id", [""])[0]

    # Adres çubuğuna elle "/urun?id=abc" yazan biri olabilir.
    # int() patlamasın diye kontrol ediyoruz.
    if not ham_id.isdigit():
        return sayfa("Ürün", "/urunler",
                     '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Geçersiz ürün numarası.</div>')

    urun = q.get_product(int(ham_id))
    if not urun:
        return sayfa("Ürün", "/urunler",
                     '<h1>Ürün bulunamadı</h1>'
                     '<div class="empty">Bu numarada bir ürün yok.</div>')

    fiyat_var = auth.fiyat_gorebilir(rol())
    etiket, sinif = durum(urun)
    ozet = q.get_product_totals(urun["id"])
    hareketler = q.get_movements(limit=200, product_id=urun["id"])

    # --- Sol sütun: ürün bilgi kartı ---
    alanlar = [
        ("Parça Kodu", f"<span class='code'>{esc(urun['part_code'])}</span>"),
        ("OEM Kodu", f"<span class='code'>{esc(urun['oem_code'] or '-')}</span>"),
        ("Ürün Grubu", esc(urun["category"])),
        ("Marka / Model", esc(f"{urun['vehicle_make'] or '-'} {urun['vehicle_model'] or ''}".strip())),
        ("Depo Lokasyonu", esc(urun["location"] or "-")),
        ("Kritik Seviye", f"{urun['min_stock']} adet"),
    ]
    if fiyat_var:
        alanlar.insert(-1, ("Birim Fiyat", tl(urun["unit_price"])))
    bilgi = "".join(
        f"<tr><th style='width:42%'>{ad}</th><td>{deger}</td></tr>"
        for ad, deger in alanlar
    )

    # --- Sağ sütun: stok özeti ---
    kartlar = f"""
    <div class="cards">
      <div class="card"><span>Mevcut Stok</span><strong>{sayi(urun['stock'])}</strong></div>
      {f"<div class='card'><span>Stok Değeri</span><strong>{tl(urun['stock'] * urun['unit_price'])}</strong></div>" if fiyat_var else ""}
      <div class="card"><span>Toplam Giriş</span><strong>{sayi(ozet['giris'])}</strong></div>
      <div class="card"><span>Toplam Çıkış</span><strong>{sayi(ozet['cikis'])}</strong></div>
    </div>"""

    # --- Hareket geçmişi ---
    if hareketler:
        # Hareketin bağlı olduğu belgeye gidebilmek için belge numarasını
        # da gösteriyoruz — "bu 40 adet nereden geldi" sorusunun cevabı
        # tek tık uzakta olsun.
        belge_haritasi = {b["id"]: b for b in q.get_product_belgeler(urun["id"], 500)}
        satirlar = ""
        for m in hareketler:
            belge = belge_haritasi.get(m.get("belge_id"))
            if belge:
                belge_hucre = (f"<a class='code' href='/belge?id={belge['id']}'>"
                               f"{esc(belge['belge_no'])}</a>")
            else:
                belge_hucre = "<span style='color:var(--muted)'>—</span>"
            satirlar += (
                f"<tr><td class='num' style='text-align:left'>{esc(m['created_at'])}</td>"
                f"<td>{belge_hucre}</td>"
                f"<td><span class='pill {'s-ok' if m['type'] == 'IN' else 's-warn'}'>"
                f"{'Giriş' if m['type'] == 'IN' else 'Çıkış'}</span></td>"
                f"<td class='num'>{m['quantity']}</td>"
                f"<td style='color:var(--muted)'>{esc(m['note'])}</td></tr>"
            )
        gecmis = f"""<table>
          <tr><th>Tarih</th><th>Belge</th><th>Tip</th><th class="num">Adet</th>
              <th>Açıklama</th></tr>{satirlar}</table>"""
    else:
        gecmis = '<div class="empty">Bu ürüne ait hareket kaydı yok.</div>'

    return sayfa(urun["part_code"], "/urunler", f"""
      <p class="sub" style="margin-bottom:6px">
        <a href="/urunler" style="color:var(--muted)">&larr; Ürün kataloğu</a></p>
      {mesajlar(params)}
      <h1><span class="code" style="font-size:22px">{esc(urun['part_code'])}</span>
          &nbsp;{esc(urun['name'])}</h1>
      <p class="sub">
        <span class="pill {sinif}">{etiket}</span>
        &nbsp;{doluluk_cubugu(urun).replace('<div class="bar"', '<div class="bar" style="display:inline-block;vertical-align:middle"')}
      </p>
      {kartlar}
      <div class="two" style="margin-top:24px">
        <div><h2>Ürün bilgileri</h2><table>{bilgi}</table></div>
        <div><h2>İşlem</h2>
          <div class="panel">
            <p style="font-size:14px;color:var(--muted);margin-bottom:14px">
              Bu ürünle belge oluştur. Ürün formda hazır seçili gelir.</p>
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              {f'<a class="btn" href="/satis-faturasi?urun={urun["id"]}" style="background:var(--accent);color:#fff;border-color:var(--accent)">Satış faturası kes</a>' if auth.yetkili_mi(rol(), "/satis-faturasi") else ""}
              {f'<a class="btn" href="/uretim-fisi?urun={urun["id"]}">Üretim fişi kes</a>' if auth.yetkili_mi(rol(), "/uretim-fisi") else ""}
              {f'<a class="btn" href="/hareket?urun={urun["id"]}">Stok düzeltme</a>' if auth.yetkili_mi(rol(), "/hareket") else ""}
              {f'<a class="btn" href="/urun-duzenle?id={urun["id"]}">Düzenle</a>' if auth.yetkili_mi(rol(), "/urun-duzenle") else ""}
              {f'<a class="btn" href="/urun-sil?id={urun["id"]}" style="color:var(--uyari)">Sil</a>' if auth.yetkili_mi(rol(), "/urun-sil") else ""}
            </div>
          </div>
        </div>
      </div>
      <h2>Hareket geçmişi ({ozet['hareket_sayisi']} kayıt)</h2>
      {gecmis}""")


def sayfa_hareket(params):
    mesaj = ""
    if "ok" in params:
        mesaj = f'<div class="msg ok">{esc(params["ok"][0])}</div>'
    elif "hata" in params:
        mesaj = f'<div class="msg err">{esc(params["hata"][0])}</div>'

    # /hareket?urun=42 ile gelinmişse o ürün formda hazır seçili gelsin.
    # Ürün detay sayfasındaki "Hareket ekle" düğmesi böyle çalışıyor.
    secili = params.get("urun", [""])[0]
    secenekler = "".join(
        f'<option value="{pid}"{" selected" if str(pid) == secili else ""}>'
        f'{esc(etiket)}</option>'
        for pid, etiket in q.get_product_options()
    )

    # Sebep listesi. Serbest metin YERİNE bu liste var — kullanıcı
    # "bayi satışı" yazıp satışı düzeltme fişi olarak kaydedemesin diye.
    # Yön de sebebe bağlı: sayım fazlası girişse, fire çıkıştır.
    # Kullanıcının ayrıca giriş/çıkış seçmesine gerek yok, hatta
    # seçtirmek hata kaynağı olurdu ("fire" seçip "giriş" işaretlemek).
    giris_sebepleri = "".join(
        f'<option value="{kod}">{esc(ad)}</option>'
        for kod, ad, yon in q.DUZELTME_SEBEPLERI if yon == "IN"
    )
    cikis_sebepleri = "".join(
        f'<option value="{kod}">{esc(ad)}</option>'
        for kod, ad, yon in q.DUZELTME_SEBEPLERI if yon == "OUT"
    )

    form = f"""<div class="panel">
      <form method="post" action="/hareket">{csrf_alani()}
        <label>Ürün</label>
        <select name="product_id" required>{secenekler}</select>
        <label>Düzeltme Sebebi</label>
        <select name="sebep" required>
          <option value="">— sebep seç —</option>
          <optgroup label="Stok artışı">{giris_sebepleri}</optgroup>
          <optgroup label="Stok azalışı">{cikis_sebepleri}</optgroup>
        </select>
        <label>Adet</label>
        <input type="number" name="adet" min="1" value="1" required>
        <label>Açıklama (isteğe bağlı)</label>
        <input name="not" placeholder="Örn: 12.03 sayımı, A-14 rafı" maxlength="120">
        <div style="margin-top:18px"><button>Düzeltme fişi kes</button></div>
      </form>
    </div>"""

    hareketler = q.get_movements(limit=25)
    satirlar = "".join(
        f"<tr data-yol='/urun?id={m['product_id']}'>"
        f"<td class='num' style='text-align:left'>{esc(m['created_at'])}</td>"
        f"<td><a class='code' href='/urun?id={m['product_id']}'>"
        f"{esc(m['part_code'])}</a></td>"
        f"<td>{esc(m['name'])}</td>"
        f"<td><span class='pill {'s-ok' if m['type'] == 'IN' else 's-warn'}'>"
        f"{'Giriş' if m['type'] == 'IN' else 'Çıkış'}</span></td>"
        f"<td class='num'>{m['quantity']}</td>"
        f"<td style='color:var(--muted)'>{esc(m['note'])}</td></tr>"
        for m in hareketler
    )
    liste = f"""<table>
      <tr><th>Tarih</th><th>Parça Kodu</th><th>Ürün</th><th>Tip</th>
          <th class="num">Adet</th><th>Açıklama</th></tr>{satirlar}</table>"""

    return sayfa("Stok Hareketi", "/hareket", f"""
      <h1>Stok Düzeltme</h1>
      <p class="sub">Yalnızca istisnai düzeltmeler: sayım farkı, fire,
         iade, depo transferi. Her kayıt bir <b>düzeltme fişi</b> (SY)
         olarak belgelenir.</p>
      <div class="msg" style="background:#eef4fa;border:1px solid #cfe0f0;
           color:var(--navy);margin-bottom:18px">
        <b>Rutin işlemler buradan yapılmaz.</b>
        Üretimden gelen parçalar için
        <a href="/uretim-fisi" style="color:var(--navy)">üretim fişi</a>,
        müşteriye sevkiyat için
        <a href="/satis-faturasi" style="color:var(--navy)">satış faturası</a>
        kesilir. Düzeltme fişi denetimde en çok incelenen belgedir;
        gerçek sebebi olmadan kullanılmaz.
      </div>
      {mesaj}{form}
      <h2>Son hareketler</h2>{liste}""")


def urun_tablosu(urunler, bos_mesaj="Ürün yok."):
    """Ürün listesini tabloya çevirir.

    Depo alt sayfalarında aynı tabloyu birkaç yerde basıyoruz — rafın
    içeriği, kritik liste, sınırdaki liste. Aynı HTML'i üç kez yazmak
    yerine tek fonksiyona aldık: kolon eklemek gerektiğinde tek yerde
    değişiyor.
    """
    if not urunler:
        return f'<div class="empty">{esc(bos_mesaj)}</div>'

    satirlar = ""
    for r in urunler:
        etiket, sinif = durum(r)
        satirlar += (
            f"<tr data-yol='/urun?id={r['id']}'>"
            f"<td><a class='code' href='/urun?id={r['id']}'>"
            f"{esc(r['part_code'])}</a></td>"
            f"<td>{esc(r['name'])}</td>"
            f"<td>{esc(r['category'])}</td>"
            f"<td>{esc(r['location'])}</td>"
            f"<td class='num'>{r['stock']}</td>"
            f"<td class='num'>{r['min_stock']}</td>"
            f"<td>{doluluk_cubugu(r)}</td>"
            f"<td><span class='pill {sinif}'>{etiket}</span></td></tr>"
        )

    return f"""<table>
      <tr><th>Parça Kodu</th><th>Ürün Adı</th><th>Kategori</th><th>Lokasyon</th>
          <th class="num">Stok</th><th class="num">Kritik</th>
          <th>Doluluk</th><th>Durum</th></tr>{satirlar}</table>"""


def depo_filtre_dugmeleri(depo, aktif_durum):
    """Depo sayfasındaki süzme düğmeleri.

    Seçili olan düğme vurgulanıyor — kullanıcı hangi listede olduğunu
    düğmeye bakarak da anlıyor, sadece başlığa bakmak zorunda kalmıyor.
    """
    secenekler = [
        ("", "Tüm raflar"),
        ("kritik", "Kritik ürünler"),
        ("sinirda", "Sınırdakiler"),
    ]
    dugmeler = ""
    for kod, etiket in secenekler:
        adres = f"/depo?ad={urllib.parse.quote(depo)}"
        if kod:
            adres += f"&durum={kod}"
        secili = "background:var(--slate);color:#fff;border-color:var(--slate)"
        dugmeler += (f'<a class="btn" href="{adres}" '
                     f'style="{secili if kod == aktif_durum else ""}">{etiket}</a>')
    return f'<div class="filters" style="margin-bottom:18px">{dugmeler}</div>'


def sayfa_depo(params):
    """Depo raporu. Dört görünüm, hepsi aynı fonksiyonda:

        /depo                    -> depoların listesi
        /depo?ad=A               -> A deposundaki raflar
        /depo?ad=A&raf=03        -> o raftaki ürünler
        /depo?ad=A&durum=kritik  -> A deposundaki kritik ürünler

    Ayrı ayrı fonksiyon yazmak yerine parametreye bakıp hangi görünümde
    olduğumuza karar veriyoruz. Adres çubuğuna bakınca nerede olduğun
    belli oluyor — bu, iyi bir web adresinin işareti.
    """
    depo = params.get("ad", [""])[0]
    raf = params.get("raf", [""])[0]
    durum_filtresi = params.get("durum", [""])[0]

    # --- Seviye 3: tek rafın içindeki ürünler ---
    if depo and raf:
        urunler = q.get_products_by_location(depo, raf)

        return sayfa(f"Depo {depo} / Raf {raf}", "/depo", f"""
          <p class="sub" style="margin-bottom:6px">
            <a href="/depo" style="color:var(--muted)">Depolar</a>
            &nbsp;&rsaquo;&nbsp;
            <a href="/depo?ad={esc(depo)}" style="color:var(--muted)">Depo {esc(depo)}</a>
            &nbsp;&rsaquo;&nbsp; Raf {esc(raf)}</p>
          <h1>Depo {esc(depo)} · Raf {esc(raf)}</h1>
          <p class="sub">{len(urunler)} kalem</p>
          {urun_tablosu(urunler, "Bu rafta ürün yok.")}""")

    # --- Duruma göre süzülmüş liste ---
    if depo and durum_filtresi in ("kritik", "sinirda"):
        urunler = q.get_products_by_location(depo, durum=durum_filtresi)

        if durum_filtresi == "kritik":
            baslik = "Kritik ürünler"
            aciklama = ("Stoğu kritik seviyeye inmiş veya altına düşmüş kalemler — "
                        "üretim planına acil alınmalı. En acil olan üstte.")
            bos = "Bu depoda kritik seviyede ürün yok."
        else:
            baslik = "Sınırdaki ürünler"
            aciklama = ("Kritik seviyenin biraz üstünde — henüz acil değil ama "
                        "üretim planına girmesi gereken kalemler.")
            bos = "Bu depoda sınırda ürün yok."

        return sayfa(f"Depo {depo} · {baslik}", "/depo", f"""
          <p class="sub" style="margin-bottom:6px">
            <a href="/depo" style="color:var(--muted)">Depolar</a>
            &nbsp;&rsaquo;&nbsp;
            <a href="/depo?ad={esc(depo)}" style="color:var(--muted)">Depo {esc(depo)}</a>
            &nbsp;&rsaquo;&nbsp; {esc(baslik)}</p>
          <h1>Depo {esc(depo)} · {esc(baslik)}</h1>
          <p class="sub">{len(urunler)} kalem — {aciklama}</p>
          {depo_filtre_dugmeleri(depo, durum_filtresi)}
          {urun_tablosu(urunler, bos)}""")

    # --- Seviye 2: bir depodaki raflar ---
    if depo:
        raflar = q.get_shelf_summary(depo)
        if not raflar:
            govde = '<div class="empty">Bu depoda ürün yok.</div>'
        else:
            satirlar = "".join(
                f"<tr data-yol='/depo?ad={esc(depo)}&amp;raf={esc(g['raf'])}'>"
                f"<td><a class='code' href='/depo?ad={esc(depo)}&raf={esc(g['raf'])}'>"
                f"Raf {esc(g['raf'])}</a></td>"
                f"<td class='num'>{g['cesit']}</td>"
                f"<td class='num'>{sayi(g['adet'])}</td>"
                f"<td class='num'>{tl(g['deger'])}</td>"
                f"<td class='num'>"
                + (f"<span class='pill s-crit'>{g['kritik']}</span>"
                   if g["kritik"] else "-")
                + "</td></tr>"
                for g in raflar
            )
            govde = f"""<table>
              <tr><th>Raf</th><th class="num">Çeşit</th><th class="num">Adet</th>
                  <th class="num">Değer</th><th class="num">Kritik</th></tr>
              {satirlar}</table>"""

        toplam = sum(g["adet"] for g in raflar)
        kritik_toplam = sum(g["kritik"] for g in raflar)
        return sayfa(f"Depo {depo}", "/depo", f"""
          <p class="sub" style="margin-bottom:6px">
            <a href="/depo" style="color:var(--muted)">&larr; Depolar</a></p>
          <h1>Depo {esc(depo)}</h1>
          <p class="sub">{len(raflar)} raf · toplam {sayi(toplam)} adet
            {f"· <b style='color:var(--uyari)'>{kritik_toplam} kritik kalem</b>" if kritik_toplam else ""}</p>
          {depo_filtre_dugmeleri(depo, durum_filtresi)}
          {govde}""")

    # --- Seviye 1: tüm depolar ---
    depolar = q.get_warehouse_summary()

    kartlar = f"""
    <div class="cards">
      <div class="card"><span>Depo Sayısı</span><strong>{len(depolar)}</strong></div>
      <div class="card"><span>Toplam Raf</span>
        <strong>{sum(d['raf_sayisi'] for d in depolar)}</strong></div>
      <div class="card"><span>Toplam Adet</span>
        <strong>{sayi(sum(d['adet'] for d in depolar))}</strong></div>
      <div class="card {'alert' if sum(d['kritik'] for d in depolar) else ''}">
        <span>Kritik Ürün</span>
        <strong>{sum(d['kritik'] for d in depolar)}</strong></div>
    </div>"""

    satirlar = "".join(
        f"<tr data-yol='/depo?ad={esc(g['depo'])}'>"
        f"<td><a class='code' href='/depo?ad={esc(g['depo'])}'>"
        f"Depo {esc(g['depo'])}</a></td>"
        f"<td class='num'>{g['raf_sayisi']}</td>"
        f"<td class='num'>{g['cesit']}</td>"
        f"<td class='num'>{sayi(g['adet'])}</td>"
        f"<td class='num'>{tl(g['deger'])}</td>"
        f"<td class='num'>"
        + (f"<span class='pill s-crit'>{g['kritik']}</span>" if g["kritik"] else "-")
        + "</td></tr>"
        for g in depolar
    )

    return sayfa("Depo Raporu", "/depo", f"""
      <h1>Depo Raporu</h1>
      <p class="sub">Stoğun fiziksel dağılımı — depodan rafa inebilirsin</p>
      {kartlar}
      <h2>Depolar</h2>
      <table>
        <tr><th>Depo</th><th class="num">Raf</th><th class="num">Çeşit</th>
            <th class="num">Adet</th><th class="num">Değer</th>
            <th class="num">Kritik</th></tr>
        {satirlar}
      </table>""")


def sayfa_rapor():
    kritik = q.get_critical_products()
    s = q.get_dashboard_stats()

    if kritik:
        satirlar = "".join(
            f"<tr data-yol='/urun?id={r['id']}'>"
            f"<td><a class='code' href='/urun?id={r['id']}'>"
            f"{esc(r['part_code'])}</a></td>"
            f"<td>{esc(r['name'])}</td><td>{esc(r['category'])}</td>"
            f"<td>{esc(r['location'])}</td>"
            f"<td class='num'>{r['stock']}</td>"
            f"<td class='num'>{r['min_stock']}</td>"
            f"<td class='num'>{r['min_stock'] - r['stock']}</td></tr>"
            for r in kritik
        )
        tablo = f"""<table>
          <tr><th>Parça Kodu</th><th>Ürün Adı</th><th>Kategori</th><th>Lokasyon</th>
              <th class="num">Stok</th><th class="num">Kritik</th>
              <th class="num">Eksik</th></tr>{satirlar}</table>"""
    else:
        tablo = '<div class="empty">Kritik seviyenin altında ürün yok.</div>'

    return sayfa("Raporlar", "/rapor", f"""
      <h1>Kritik Stok Raporu</h1>
      <p class="sub">{len(kritik)} ürün kritik seviyede ·
         toplam envanter değeri {tl(s['stok_degeri'])}</p>
      <div class="filters noprint">
        <a class="btn" href="/indir/kritik.xlsx">Excel indir</a>
        <a class="btn" href="/indir/kritik.csv">CSV indir</a>
        <a class="btn" href="/indir/envanter.xlsx">Tüm envanter (Excel)</a>
        <a class="btn" href="javascript:window.print()">PDF olarak yazdır</a>
      </div>
      {tablo}""")


# ===========================================================================
# Sunucu
# ===========================================================================

class Handler(BaseHTTPRequestHandler):

    OTURUM_CEREZI = "oturum"

    def _jeton(self):
        """İstekteki çerezden oturum jetonunu okur."""
        ham = self.headers.get("Cookie")
        if not ham:
            return None
        kutu = http.cookies.SimpleCookie()
        try:
            kutu.load(ham)
        except http.cookies.CookieError:
            return None
        parca = kutu.get(self.OTURUM_CEREZI)
        return parca.value if parca else None

    def _oturum_kur(self):
        """Bu isteğin kullanıcısını thread bağlamına yerleştirir.

        Her istekte baştan yapılıyor. Çerez geçersizse veya sunucu
        yeniden başladıysa kullanıcı None olur ve _kapi girişe yönlendirir.
        """
        jeton = self._jeton()
        _istek.kullanici = auth.oturum_getir(jeton)
        _istek.csrf = auth.csrf_jetonu(jeton) if _istek.kullanici else ""
        return _istek.kullanici

    def _kapi(self, yol):
        """Giriş ve yetki kontrolü. Geçerse True döner.

        İki ayrı durum var ve ikisi farklı cevap gerektirir:
          - Giriş yapılmamış  -> giriş ekranına yönlendir
          - Giriş var, yetki yok -> 403 sayfası göster

        İkincisini de girişe yönlendirmek yanlış olurdu: kullanıcı
        zaten giriş yapmış, tekrar giriş yapması sorunu çözmez.
        """
        kullanici = aktif_kullanici()

        if not kullanici:
            # Nereye gitmek istediğini saklıyoruz ki girişten sonra
            # oraya devam edebilsin.
            self._yonlendir("/giris?devam=" + urllib.parse.quote(self.path))
            return False

        if not auth.yetkili_mi(kullanici["rol"], yol):
            # 403 Forbidden: "kim olduğunu biliyorum ama buraya giremezsin"
            self._gonder(sayfa_yetkisiz(yol), kod=403)
            return False

        return True

    def _gonder(self, govde, tip="text/html; charset=utf-8", indir=None,
                cerez=None, kod=200):
        """Tarayıcıya cevabı yollar."""
        if isinstance(govde, str):
            govde = govde.encode("utf-8")

        self.send_response(kod)
        self.send_header("Content-Type", tip)
        self.send_header("Content-Length", str(len(govde)))
        if cerez:
            self.send_header("Set-Cookie", cerez)
        if indir:
            # Bu başlık tarayıcıya "bunu gösterme, dosya olarak indir" der.
            self.send_header("Content-Disposition",
                             f'attachment; filename="{indir}"')
        self.end_headers()
        self.wfile.write(govde)

    def _yonlendir(self, konum, cerez=None):
        """303 See Other — 'şu adrese git' demek.

        Form gönderildikten sonra doğrudan HTML dönersek, kullanıcı
        sayfayı yenilediğinde tarayıcı formu TEKRAR gönderir ve aynı
        stok hareketi ikinci kez kaydedilir. Yönlendirme yaparak
        bunu engelliyoruz. Bu kalıbın adı Post/Redirect/Get.
        """
        self.send_response(303)
        self.send_header("Location", konum)
        if cerez:
            self.send_header("Set-Cookie", cerez)
        self.end_headers()

    def do_GET(self):
        # "/urunler?search=rotil" -> yol="/urunler", params={"search":["rotil"]}
        parcalar = urllib.parse.urlparse(self.path)
        yol = parcalar.path
        params = urllib.parse.parse_qs(parcalar.query)

        self._oturum_kur()

        # Giriş ve çıkış yolları kontrolden muaf — aksi halde giriş
        # yapabilmek için giriş yapmış olman gerekirdi.
        if yol == "/giris":
            if aktif_kullanici():
                self._yonlendir("/")
            else:
                self._gonder(sayfa_giris(devam=params.get("devam", [""])[0]))
            return

        if yol == "/cikis":
            auth.oturum_kapat(self._jeton())
            # Max-Age=0 çerezi tarayıcıdan siler.
            self._yonlendir("/giris",
                            cerez=f"{self.OTURUM_CEREZI}=; Max-Age=0; Path=/")
            return

        if not self._kapi(yol):
            return

        if yol == "/":
            self._gonder(sayfa_panel())

        elif yol == "/urunler":
            self._gonder(sayfa_urunler(params))

        elif yol == "/urun":
            self._gonder(sayfa_urun_detay(params))

        elif yol == "/hareket":
            self._gonder(sayfa_hareket(params))

        elif yol == "/belgeler":
            self._gonder(sayfa_belgeler(params))

        elif yol == "/belge":
            self._gonder(sayfa_belge_detay(params))

        elif yol == "/uretim-fisi":
            self._gonder(sayfa_uretim_fisi(
                on_secim=params.get("urun", [""])[0]))

        elif yol == "/belge-iptal":
            self._gonder(sayfa_belge_iptal(params))

        elif yol == "/fatura":
            self._gonder(sayfa_fatura_yazdir(params))

        elif yol == "/satis-faturasi":
            self._gonder(sayfa_satis_faturasi(
                on_secim=params.get("urun", [""])[0]))

        elif yol == "/iade-faturasi":
            self._gonder(sayfa_iade_faturasi(params))

        elif yol == "/musteriler":
            self._gonder(sayfa_musteriler())

        elif yol == "/seyir":
            self._gonder(sayfa_stok_seyri())

        elif yol == "/kullanicilar":
            self._gonder(sayfa_kullanicilar(params))

        elif yol == "/kullanici-ekle":
            self._gonder(sayfa_kullanici_ekle())

        elif yol == "/kullanici-duzenle":
            self._gonder(sayfa_kullanici_duzenle(params))

        elif yol == "/urun-ekle":
            self._gonder(sayfa_urun_ekle())

        elif yol == "/urun-duzenle":
            self._gonder(sayfa_urun_duzenle(params))

        elif yol == "/urun-sil":
            self._gonder(sayfa_urun_sil(params))

        elif yol == "/depo":
            self._gonder(sayfa_depo(params))

        elif yol == "/rapor":
            self._gonder(sayfa_rapor())

        elif yol == "/indir/kritik.xlsx":
            veri = exports.to_xlsx([
                ("Kritik Stok", q.get_critical_products(), EXPORT_HEADERS)])
            self._gonder(veri, "application/vnd.openxmlformats-officedocument."
                               "spreadsheetml.sheet", "kritik_stok.xlsx")

        elif yol == "/indir/kritik.csv":
            veri = exports.to_csv(q.get_critical_products(), EXPORT_HEADERS)
            self._gonder(veri, "text/csv; charset=utf-8", "kritik_stok.csv")

        elif yol == "/indir/envanter.xlsx":
            ozet_basliklar = [("kategori", "Kategori"), ("cesit", "Ürün Çeşidi"),
                              ("adet", "Toplam Adet"), ("deger", "Stok Değeri")]
            veri = exports.to_xlsx([
                ("Tüm Ürünler", q.get_products(), EXPORT_HEADERS),
                ("Kritik Stok", q.get_critical_products(), EXPORT_HEADERS),
                ("Kategori Özeti", q.get_category_summary(), ozet_basliklar),
            ])
            self._gonder(veri, "application/vnd.openxmlformats-officedocument."
                               "spreadsheetml.sheet", "envanter_raporu.xlsx")

        else:
            self.send_error(404, "Sayfa bulunamadi")

    def do_POST(self):
        # POST verisi adreste değil, isteğin gövdesinde gelir.
        # Kaç byte okuyacağımızı Content-Length başlığından öğreniyoruz.
        uzunluk = int(self.headers.get("Content-Length", 0))
        govde = self.rfile.read(uzunluk).decode("utf-8")
        form = urllib.parse.parse_qs(govde)

        # parse_qs değerleri liste verir; tek değer almak için kısa yardımcı
        def al(ad, varsayilan=""):
            return form.get(ad, [varsayilan])[0]

        self._oturum_kur()

        if self.path == "/giris":
            self._post_giris(al)
            return

        if not self._kapi(self.path):
            return

        # CSRF kontrolü. Giriş formu hariç tüm POST'lar için geçerli
        # (giriş sırasında henüz oturum yok, dolayısıyla jeton da yok).
        if not auth.csrf_dogrula(self._jeton(), al("_csrf")):
            self._gonder(sayfa_yetkisiz(self.path + " (CSRF)"), kod=403)
            return

        if self.path == "/hareket":
            self._post_hareket(form, al)
        elif self.path == "/uretim-fisi":
            self._post_uretim_fisi(form, al)
        elif self.path == "/satis-faturasi":
            self._post_satis_faturasi(form, al)
        elif self.path == "/iade-faturasi":
            self._post_iade_faturasi(form, al)
        elif self.path == "/belge-iptal":
            self._post_belge_iptal(al)
        elif self.path == "/kullanici-ekle":
            self._post_kullanici_ekle(al)
        elif self.path == "/kullanici-duzenle":
            self._post_kullanici_duzenle(al)
        elif self.path == "/kullanici-parola":
            self._post_kullanici_parola(al)
        elif self.path == "/kullanici-durum":
            self._post_kullanici_durum(al)
        elif self.path == "/yedek-al":
            self._post_yedek_al()
        elif self.path == "/urun-ekle":
            self._post_urun_ekle(form, al)
        elif self.path == "/urun-duzenle":
            self._post_urun_duzenle(form, al)
        elif self.path == "/urun-sil":
            self._post_urun_sil(al)
        else:
            self.send_error(404)

    # --- POST işleyicileri -------------------------------------------------

    def _post_giris(self, al):
        kullanici_adi = al("kullanici_adi")
        parola = al("parola")
        devam = al("devam") or "/"

        # Deneme sınırı kontrolü, parola kontrolünden ÖNCE.
        # Kilitliyse parolayı hiç hesaplamıyoruz — hem gereksiz iş
        # yapmamış oluyoruz hem de doğru parola bile kilidi açmıyor.
        kalan_kilit = auth.kilitli_mi(kullanici_adi)
        if kalan_kilit:
            dakika = kalan_kilit // 60 + 1
            self._gonder(sayfa_giris(
                f"Çok fazla hatalı deneme. {dakika} dakika sonra tekrar deneyin.",
                kullanici_adi, devam))
            return

        kayit = q.get_user_by_username(kullanici_adi)

        # DİKKAT: "böyle bir kullanıcı yok" ile "parola yanlış" için AYNI
        # mesajı veriyoruz. Ayrı mesaj verirsek saldırgan hangi kullanıcı
        # adlarının var olduğunu tek tek öğrenebilir. Buna kullanıcı
        # numaralandırma (user enumeration) deniyor.
        if not kayit or not auth.parola_dogrula(parola, kayit["parola_ozeti"]):
            kalan = auth.basarisiz_deneme(kullanici_adi)
            mesaj = "Kullanıcı adı veya parola hatalı."
            # Kalan hakkı sadece azaldığında söylüyoruz. Her seferinde
            # söylersek var olmayan bir kullanıcı adına da sayaç
            # gösterip o hesabın varlığını ele vermiş oluruz.
            if 0 < kalan <= 2:
                mesaj += f" {kalan} deneme hakkınız kaldı."
            elif kalan == 0:
                mesaj = ("Çok fazla hatalı deneme. Hesap "
                         f"{auth.KILIT_SURESI // 60} dakika kilitlendi.")
            self._gonder(sayfa_giris(mesaj, kullanici_adi, devam))
            return

        # Başarılı giriş: sayacı temizle
        auth.denemeleri_sifirla(kullanici_adi)

        jeton = auth.oturum_ac(kayit)

        # HttpOnly: JavaScript bu çerezi okuyamaz. Sayfada bir XSS açığı
        #   olsa bile oturum jetonu çalınamaz.
        # SameSite=Lax: başka bir siteden gelen isteklerde çerez
        #   gönderilmez — CSRF'e karşı temel koruma.
        # Secure bayrağı YOK, çünkü HTTP üzerinden çalışıyoruz. Gerçek
        #   kullanımda HTTPS ve Secure şart olurdu.
        cerez = f"{self.OTURUM_CEREZI}={jeton}; HttpOnly; Path=/; SameSite=Lax"

        # Açık yönlendirme (open redirect) engeli: devam parametresi
        # "http://kotusite.com" olabilir. Sadece kendi sitemizdeki
        # yolları kabul ediyoruz.
        if not devam.startswith("/") or devam.startswith("//"):
            devam = "/"

        self._yonlendir(devam, cerez=cerez)

    def _post_hareket(self, form, al):
        try:
            sebep_kodu = al("sebep")
            _, belge_no = q.duzeltme_fisi(
                aktif_kullanici()["id"], int(al("product_id")),
                sebep_kodu, al("adet"), al("not"))
            sebep_adi, yon = q.DUZELTME_SEBEP_HARITA[sebep_kodu]
            mesaj = (f"{sebep_adi}: {al('adet')} adet "
                     f"{'giriş' if yon == 'IN' else 'çıkış'}. "
                     f"Düzeltme fişi: {belge_no}")
            self._yonlendir("/hareket?ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            # duzeltme_fisi kural ihlalinde ValueError fırlatıyor
            # (örn. stoktan fazla çıkış). Yakalamazsak kullanıcı
            # çirkin bir hata sayfası görür.
            self._yonlendir("/hareket?hata=" + urllib.parse.quote(str(hata)))

        except (KeyError, IndexError):
            self._yonlendir("/hareket?hata=" + urllib.parse.quote("Form eksik gönderildi."))

        except Exception as hata:
            # Son emniyet kemeri. Beklenmedik bir hata olursa kullanıcı
            # boş sayfa yerine anlaşılır bir mesaj görsün; hatanın kendisi
            # de terminale yazılsın ki sen görebilesin.
            print("BEKLENMEDIK HATA:", repr(hata))
            self._yonlendir("/hareket?hata=" + urllib.parse.quote(
                "Kayıt sırasında beklenmedik bir hata oluştu."))

    def _post_uretim_fisi(self, form, al):
        """Çok satırlı formu belgeye çevirir.

        form["urun"]   -> ["5", "", "12", ...]   8 satırın hepsi
        form["miktar"] -> ["10", "", "4", ...]

        zip() ikisini eşleştiriyor; ikisi de dolu olan satırlar alınıyor.
        """
        urunler = form.get("urun", [])
        miktarlar = form.get("miktar", [])

        satirlar = []
        for urun_id, miktar in zip(urunler, miktarlar):
            urun_id, miktar = urun_id.strip(), miktar.strip()
            if not urun_id or not miktar:
                continue          # boş satır — yok say
            satirlar.append({"product_id": urun_id, "miktar": miktar})

        if not satirlar:
            self._gonder(sayfa_uretim_fisi(form, "En az bir kalem eklemelisin."))
            return

        try:
            _, belge_no = q.belge_olustur(
                "uretim", aktif_kullanici()["id"], satirlar, al("aciklama"))
            mesaj = f"{belge_no} oluşturuldu, {len(satirlar)} kalem stoğa girdi."
            self._yonlendir("/belgeler?ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            # Hata durumunda yönlendirmiyoruz: kullanıcının doldurduğu
            # 8 satır kaybolmasın diye formu aynı değerlerle geri basıyoruz.
            self._gonder(sayfa_uretim_fisi(form, str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_uretim_fisi(form, "Beklenmedik bir hata oluştu."))

    def _post_satis_faturasi(self, form, al):
        urunler = form.get("urun", [])
        miktarlar = form.get("miktar", [])
        fiyatlar = form.get("fiyat", [])

        satirlar = []
        for i, (urun_id, miktar) in enumerate(zip(urunler, miktarlar)):
            urun_id, miktar = urun_id.strip(), miktar.strip()
            if not urun_id or not miktar:
                continue

            satir = {"product_id": urun_id, "miktar": miktar}

            # Fiyat girilmişse onu kullan, boşsa liste fiyatı geçerli olsun.
            # Türkçe biçimi çeviriyoruz: "1.250,50" -> 1250.50
            ham_fiyat = (fiyatlar[i].strip() if i < len(fiyatlar) else "")
            if ham_fiyat:
                try:
                    satir["birim_fiyat"] = float(
                        ham_fiyat.replace(".", "").replace(",", "."))
                except ValueError:
                    self._gonder(sayfa_satis_faturasi(
                        form, f"{i + 1}. satırdaki birim fiyat sayı olmalı."))
                    return

            satirlar.append(satir)

        if not satirlar:
            self._gonder(sayfa_satis_faturasi(form, "En az bir kalem eklemelisin."))
            return

        musteri = al("musteri").strip()
        if not musteri.isdigit():
            self._gonder(sayfa_satis_faturasi(form, "Müşteri seçilmeli."))
            return

        try:
            _, belge_no = q.belge_olustur(
                "satis", aktif_kullanici()["id"], satirlar,
                aciklama=al("aciklama"), musteri_id=int(musteri))
            mesaj = f"{belge_no} kesildi, {len(satirlar)} kalem sevk edildi."
            self._yonlendir("/belgeler?ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            self._gonder(sayfa_satis_faturasi(form, str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_satis_faturasi(form, "Beklenmedik bir hata oluştu."))

    def _post_iade_faturasi(self, form, al):
        """İade faturasını kaydeder.

        Satış faturasından farkı: fiyat girilmiyor. İade, orijinal
        faturadaki fiyattan yapılıyor — ürünün fiyatı sonradan değişmiş
        olsa bile müşteriye o günkü fiyat üzerinden iade edilmeli.
        """
        ham_satis = al("satis")
        if not ham_satis.isdigit():
            self._yonlendir("/iade-faturasi?hata=" +
                            urllib.parse.quote("Satış faturası seçilmedi."))
            return

        satis = q.get_belge(int(ham_satis))
        if not satis or satis["tip"] != "satis":
            self._yonlendir("/iade-faturasi?hata=" +
                            urllib.parse.quote("Geçersiz satış faturası."))
            return

        urunler = form.get("urun", [])
        miktarlar = form.get("miktar", [])

        satirlar = []
        for urun_id, miktar in zip(urunler, miktarlar):
            urun_id, miktar = urun_id.strip(), miktar.strip()
            # 0 girilen satırlar da atlanıyor: kullanıcı bazen boş
            # bırakmak yerine 0 yazıyor, ikisi de "iade etme" demek.
            if not urun_id or not miktar or miktar == "0":
                continue
            satirlar.append({"product_id": urun_id, "miktar": miktar})

        if not satirlar:
            self._gonder(sayfa_iade_faturasi(
                {"satis": [ham_satis]}, form,
                "En az bir kalem için iade miktarı girmelisin."))
            return

        try:
            _, belge_no = q.belge_olustur(
                "iade", aktif_kullanici()["id"], satirlar,
                aciklama=al("aciklama"),
                musteri_id=satis["musteri_id"],
                kaynak_belge_id=satis["id"])
            mesaj = (f"{belge_no} kesildi, {len(satirlar)} kalem stoğa "
                     f"geri alındı ({satis['belge_no']} faturasından).")
            self._yonlendir("/belgeler?ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            self._gonder(sayfa_iade_faturasi(
                {"satis": [ham_satis]}, form, str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_iade_faturasi(
                {"satis": [ham_satis]}, form, "Beklenmedik bir hata oluştu."))

    def _post_belge_iptal(self, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/belgeler?hata=" + urllib.parse.quote("Geçersiz belge."))
            return

        try:
            belge_no, kalem = q.belge_iptal(
                int(ham_id), aktif_kullanici()["id"], al("sebep"))
            mesaj = (f"{belge_no} iptal edildi. {kalem} kalem için "
                     f"ters hareket kaydedildi.")
            self._yonlendir(f"/belge?id={ham_id}&ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            self._yonlendir(f"/belge?id={ham_id}&hata=" +
                            urllib.parse.quote(str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._yonlendir(f"/belge?id={ham_id}&hata=" + urllib.parse.quote(
                "İptal sırasında beklenmedik bir hata oluştu."))

    def _post_kullanici_ekle(self, al):
        veri = {ad: al(ad) for ad in
                ("kullanici_adi", "ad_soyad", "rol", "parola")}
        try:
            q.add_user(veri["kullanici_adi"], veri["ad_soyad"],
                       veri["rol"], veri["parola"])
            mesaj = f"{veri['kullanici_adi']} kullanıcısı eklendi."
            self._yonlendir("/kullanicilar?ok=" + urllib.parse.quote(mesaj))
        except ValueError as hata:
            self._gonder(sayfa_kullanici_ekle(veri, str(hata)))
        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_kullanici_ekle(veri, "Beklenmedik bir hata oluştu."))

    def _post_kullanici_duzenle(self, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/kullanicilar?hata=" +
                            urllib.parse.quote("Geçersiz kullanıcı."))
            return
        try:
            q.update_user(int(ham_id), al("ad_soyad"), al("rol"))
            self._yonlendir(f"/kullanici-duzenle?id={ham_id}&ok=" +
                            urllib.parse.quote("Bilgiler güncellendi."))
        except ValueError as hata:
            self._gonder(sayfa_kullanici_duzenle(
                {"id": [ham_id]}, {"ad_soyad": al("ad_soyad"), "rol": al("rol")},
                str(hata)))

    def _post_kullanici_parola(self, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/kullanicilar?hata=" +
                            urllib.parse.quote("Geçersiz kullanıcı."))
            return
        try:
            q.set_user_password(int(ham_id), al("parola"))
            self._yonlendir(f"/kullanici-duzenle?id={ham_id}&ok=" +
                            urllib.parse.quote("Parola değiştirildi."))
        except ValueError as hata:
            self._yonlendir(f"/kullanici-duzenle?id={ham_id}&hata=" +
                            urllib.parse.quote(str(hata)))

    def _post_kullanici_durum(self, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/kullanicilar?hata=" +
                            urllib.parse.quote("Geçersiz kullanıcı."))
            return

        # Kendi hesabını kapatma koruması. Arayüzde düğme zaten pasif
        # ama asıl kontrol burada olmalı — biri doğrudan POST atabilir.
        # Tek yönetici kendini kilitlerse sisteme kimse giremez.
        if int(ham_id) == aktif_kullanici()["id"] and al("aktif") == "0":
            self._yonlendir(f"/kullanici-duzenle?id={ham_id}&hata=" +
                            urllib.parse.quote("Kendi hesabını kapatamazsın."))
            return

        try:
            aktif = al("aktif") == "1"
            q.set_user_active(int(ham_id), aktif)
            self._yonlendir(f"/kullanici-duzenle?id={ham_id}&ok=" +
                            urllib.parse.quote(
                                "Hesap açıldı." if aktif else "Hesap kapatıldı."))
        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._yonlendir("/kullanicilar?hata=" +
                            urllib.parse.quote("İşlem başarısız."))

    def _post_yedek_al(self):
        """Veritabanının anlık yedeğini alır.

        Neden POST, GET değil? Çünkü bu bir İŞLEM — diske dosya yazıyor.
        GET ile yapılsaydı o adresi ziyaret eden herhangi bir şey
        (tarayıcı ön yüklemesi, bir bot, yanlışlıkla kaydedilmiş bir
        yer imi) habersizce yedek alırdı.

        Yedekleme işini kendimiz yazmıyoruz — yedekle.py zaten bunu
        yapıyor ve SQLite'ın güvenli yedekleme yöntemini kullanıyor.
        Buradan sadece onu çağırıyoruz. Aynı işi iki yerde yazsaydık
        biri güncellenip diğeri unutulurdu.
        """
        try:
            hedef = yedekle.yedek_al()
            boyut = hedef.stat().st_size / 1024

            # Eski yedekleri de temizle — komut satırından çalıştırıldığında
            # bu zaten yapılıyor, arayüzden de aynı davranış olsun.
            silinen = yedekle.eskileri_temizle()

            mesaj = f"Yedek alındı: {hedef.name} ({boyut:.0f} KB)"
            if silinen:
                mesaj += f" · {len(silinen)} eski yedek temizlendi"
            self._yonlendir("/kullanicilar?ok=" + urllib.parse.quote(mesaj))

        except Exception as hata:
            print("YEDEKLEME HATASI:", repr(hata))
            self._yonlendir("/kullanicilar?hata=" + urllib.parse.quote(
                "Yedek alınamadı. Ayrıntı için sunucu terminaline bak."))

    def _form_verisi(self, al):
        """Formdaki ürün alanlarını sözlüğe toplar."""
        return {alan: al(alan) for alan, _ in q.URUN_ALANLARI}

    def _post_urun_ekle(self, form, al):
        veri = self._form_verisi(al)
        try:
            yeni_id = q.add_product(veri)
            mesaj = f"{veri['part_code']} eklendi. Stok girmek için hareket kaydı oluştur."
            self._yonlendir(f"/urun?id={yeni_id}&ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            # DİKKAT: burada yönlendirme YAPMIYORUZ.
            # Yönlendirirsek kullanıcının doldurduğu form sıfırlanır ve
            # her şeyi baştan yazmak zorunda kalır. Bunun yerine formu
            # aynı değerlerle, hata mesajıyla birlikte yeniden basıyoruz.
            self._gonder(sayfa_urun_ekle(veri, str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_urun_ekle(veri, "Beklenmedik bir hata oluştu."))

    def _post_urun_duzenle(self, form, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/urunler?hata=" + urllib.parse.quote("Geçersiz ürün."))
            return

        veri = self._form_verisi(al)
        try:
            q.update_product(int(ham_id), veri)
            mesaj = f"{veri['part_code']} güncellendi."
            self._yonlendir(f"/urun?id={ham_id}&ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            self._gonder(sayfa_urun_duzenle({"id": [ham_id]}, veri, str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._gonder(sayfa_urun_duzenle({"id": [ham_id]}, veri,
                                            "Beklenmedik bir hata oluştu."))

    def _post_urun_sil(self, al):
        ham_id = al("id")
        if not ham_id.isdigit():
            self._yonlendir("/urunler?hata=" + urllib.parse.quote("Geçersiz ürün."))
            return

        try:
            kod, silinen = q.delete_product(int(ham_id))
            mesaj = f"{kod} silindi ({silinen} hareket kaydıyla birlikte)."
            self._yonlendir("/urunler?ok=" + urllib.parse.quote(mesaj))

        except ValueError as hata:
            self._yonlendir("/urunler?hata=" + urllib.parse.quote(str(hata)))

        except Exception as hata:
            print("BEKLENMEDIK HATA:", repr(hata))
            self._yonlendir("/urunler?hata=" + urllib.parse.quote(
                "Silme sırasında beklenmedik bir hata oluştu."))

    def log_message(self, *args):
        """Terminale her istek için satır basmasın diye boş bıraktık.

        Hata ayıklarken bu metodu silersen tüm istekleri görürsün.
        """
        pass


def main():
    # ThreadingHTTPServer: aynı anda birden fazla isteği karşılar.
    # Düz HTTPServer kullansaydık tarayıcı sayfa + ikon isteğini aynı anda
    # yolladığında biri beklerdi.
    sunucu = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    adres = f"http://127.0.0.1:{PORT}"
    temizlenen = auth.eski_oturumlari_temizle()
    if temizlenen:
        print(f"{temizlenen} suresi dolmus oturum temizlendi.")

    print(f"Teknorot Stok Takip calisiyor:  {adres}")
    print("Durdurmak icin Ctrl+C")

    try:
        webbrowser.open(adres)
    except Exception:
        pass  # Tarayıcı açılamazsa sorun değil, adresi elle yazar.

    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nSunucu durduruldu.")


if __name__ == "__main__":
    main()
