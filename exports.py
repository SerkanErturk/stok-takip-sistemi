"""
exports.py — CSV ve Excel çıktısı, sıfır dış kütüphane.

CSV kolay: Python'un csv modülü zaten var.

XLSX kısmı ilk bakışta sihir gibi görünür ama değil. Bir .xlsx dosyası
aslında ZIP'lenmiş bir klasördür. Uzantısını .zip yapıp açarsan içinde
şu XML dosyalarını görürsün:

    [Content_Types].xml          hangi dosya ne tipte
    _rels/.rels                  "ana dosya nerede"
    xl/workbook.xml              sayfa listesi
    xl/worksheets/sheet1.xml     hücrelerin kendisi
    xl/_rels/workbook.xml.rels   sayfa dosyalarının adresleri

Biz de bu XML'leri metin olarak üretip zipfile ile paketliyoruz.
openpyxl'in yaptığı işin en temel hali bu.
"""

import csv
import io
import zipfile
from xml.sax.saxutils import escape

# XML'de yasak olan kontrol karakterleri. Veriden birinin içine kaçarsa
# Excel dosyayı "bozuk" diye açmayı reddeder, o yüzden temizliyoruz.
ILLEGAL = {chr(c) for c in range(32) if c not in (9, 10, 13)}


def _clean(text):
    return "".join(ch for ch in str(text) if ch not in ILLEGAL)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def to_csv(rows, headers) -> bytes:
    """Sözlük listesini CSV'ye çevirir.

    rows:    [{"part_code": "TR-1", ...}, ...]
    headers: [("part_code", "Parça Kodu"), ...]  -> (anahtar, başlık)
    """
    buffer = io.StringIO()

    # delimiter=";" -> Türkçe Excel'de ayraç noktalı virgüldür. Virgül
    # kullanırsan Excel her satırı tek hücreye tıkıştırır.
    writer = csv.writer(buffer, delimiter=";")

    writer.writerow([label for _, label in headers])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in headers])

    # utf-8-sig: dosyanın başına görünmez bir "bu UTF-8" işareti koyar.
    # Bu işaret olmadan Excel Türkçe harfleri bozuk gösterir (Ã¼, Ä±...).
    # En sık karşılaşılan Türkçe CSV sorunu budur.
    return buffer.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def _column_letter(index):
    """1 -> A, 2 -> B, ... 27 -> AA"""
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _sheet_xml(rows, headers):
    """Tek bir sayfanın hücrelerini XML'e çevirir."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>',
    ]

    def cell(col, row_no, value):
        ref = f"{_column_letter(col)}{row_no}"
        # Sayılar ve metinler Excel'de FARKLI yazılır:
        #   sayı  -> <c r="A1"><v>42</v></c>
        #   metin -> <c r="A1" t="inlineStr"><is><t>merhaba</t></is></c>
        # Sayıyı metin olarak yazarsan Excel'de toplama yapamazsın.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        # escape(): & < > karakterlerini XML'in anlayacağı hale getirir.
        # Ürün adında "Ø 22 & M14" gibi bir şey varsa dosyayı bozmasın.
        return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f'{escape(_clean(value))}</t></is></c>')

    # 1. satır: başlıklar
    header_cells = "".join(
        cell(i, 1, label) for i, (_, label) in enumerate(headers, start=1)
    )
    parts.append(f'<row r="1">{header_cells}</row>')

    # 2. satırdan itibaren veri
    for row_no, row in enumerate(rows, start=2):
        cells = "".join(
            cell(i, row_no, row.get(key, ""))
            for i, (key, _) in enumerate(headers, start=1)
        )
        parts.append(f'<row r="{row_no}">{cells}</row>')

    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def to_xlsx(sheets) -> bytes:
    """Birden fazla sayfayı tek Excel dosyasına paketler.

    sheets: [("Sayfa Adı", rows, headers), ...]
    """
    NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
    NS_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    NS_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
    NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    # Excel sayfa adı kuralları: en fazla 31 karakter, şu işaretler yasak.
    def safe_name(name):
        for bad in "[]:*?/\\":
            name = name.replace(bad, "-")
        return name[:31]

    names = [safe_name(name) for name, _, _ in sheets]

    # 1) Hangi dosya ne tipte
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.'
        f'spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{NS_TYPES}">'
        '<Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'{overrides}</Types>'
    )

    # 2) "Ana dosya xl/workbook.xml"
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG}">'
        f'<Relationship Id="rId1" Type="{NS_DOC}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )

    # 3) Sayfa listesi
    sheet_tags = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(names, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_DOC}">'
        f'<sheets>{sheet_tags}</sheets></workbook>'
    )

    # 4) Sayfa dosyalarının adresleri
    rel_tags = "".join(
        f'<Relationship Id="rId{i}" Type="{NS_DOC}/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG}">{rel_tags}</Relationships>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for i, (_, rows, headers) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml",
                             _sheet_xml(rows, headers))

    return buffer.getvalue()
