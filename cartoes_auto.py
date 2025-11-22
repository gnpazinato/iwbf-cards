import streamlit as st
from io import BytesIO
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4


# ============================================================
# TEMPLATE ANALYSIS (Avery or any PDF with rectangles)
# ============================================================

def detectar_slots_template(template_bytes):
    doc = fitz.open(stream=template_bytes, filetype="pdf")
    page = doc[0]
    page_rect = page.rect

    drawings = page.get_drawings()
    rects = []

    for d in drawings:
        r = d.get("rect")
        if r and r.width > 20 and r.height > 20:
            rects.append(r)

    if not rects:
        raise RuntimeError("No card rectangles found in the template PDF.")

    from collections import Counter, defaultdict

    size_counter = Counter((round(r.width, 1), round(r.height, 1)) for r in rects)
    main_size, _ = size_counter.most_common(1)[0]

    card_rects = [r for r in rects
                  if (round(r.width, 1), round(r.height, 1)) == main_size]

    if len(card_rects) < 2:
        raise RuntimeError("Not enough card rectangles in template.")

    rows_dict = defaultdict(list)
    for r in card_rects:
        key_y = round(r.y0, 1)
        rows_dict[key_y].append(r)

    row_keys = sorted(rows_dict.keys())
    rows = []
    for y in row_keys:
        rows.append(sorted(rows_dict[y], key=lambda rc: rc.x0))

    slots_flat = [rc for row in rows for rc in row]
    return page_rect, slots_flat, rows


def rect_to_reportlab_coords(r, page_height):
    x = r.x0
    y = page_height - r.y1
    return x, y, r.width, r.height


# ============================================================
# CONTENT-AWARE TRIM
# ============================================================

def compute_trimmed_clip(page, base_clip, zoom, bg_threshold=250):
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=base_clip, alpha=False)

    w, h, n = pix.width, pix.height, pix.n
    samples = pix.samples

    min_x, min_y = w, h
    max_x, max_y = -1, -1

    for y in range(h):
        row_idx = y * w * n
        for x in range(w):
            idx = row_idx + x * n
            r = samples[idx]
            g = samples[idx + 1] if n > 1 else r
            b = samples[idx + 2] if n > 2 else r

            if r < bg_threshold or g < bg_threshold or b < bg_threshold:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y

    if max_x < 0:
        return base_clip

    inv = 1.0 / zoom
    x0, y0, x1, y1 = base_clip

    return fitz.Rect(
        x0 + min_x * inv,
        y0 + min_y * inv,
        x0 + (max_x + 1) * inv,
        y0 + (max_y + 1) * inv
    )


# ============================================================
# FIT + CENTER
# ============================================================

def fit_and_center(slot_x, slot_y, slot_w, slot_h, pix, margin_factor=0.95):
    img_w, img_h = pix.width, pix.height

    scale = min(slot_w / img_w, slot_h / img_h) * margin_factor

    render_w = img_w * scale
    render_h = img_h * scale

    x = slot_x + (slot_w - render_w) / 2
    y = slot_y + (slot_h - render_h) / 2

    return x, y, render_w, render_h


# ============================================================
# MODE 1 — USING A TEMPLATE (Avery)
# ============================================================

def gerar_pdf_final(template_bytes, card_files):
    page_rect, slots_flat, rows = detectar_slots_template(template_bytes)
    page_width, page_height = page_rect.width, page_rect.height

    card_bytes_list = [f.read() for f in card_files]
    total_cards = len(card_bytes_list)
    card_idx = 0

    output = BytesIO()
    c = canvas.Canvas(output, pagesize=(page_width, page_height))

    zoom = 300 / 72

    while card_idx < total_cards:
        # blank page (no template drawn)

        for row_rects in rows:
            if card_idx >= total_cards:
                break

            if len(row_rects) < 2:
                continue

            left_slot = row_rects[0]
            right_slot = row_rects[1]

            card_pdf = fitz.open(stream=card_bytes_list[card_idx], filetype="pdf")
            page = card_pdf[0]

            card_w, card_h = page.rect.width, page.rect.height
            half = card_w / 2

            clip_L = compute_trimmed_clip(page, fitz.Rect(0, 0, half, card_h), zoom)
            clip_R = compute_trimmed_clip(page, fitz.Rect(half, 0, card_w, card_h), zoom)

            mat = fitz.Matrix(zoom, zoom)
            pix_L = page.get_pixmap(matrix=mat, clip=clip_L)
            pix_R = page.get_pixmap(matrix=mat, clip=clip_R)

            img_L = ImageReader(BytesIO(pix_L.tobytes("png")))
            img_R = ImageReader(BytesIO(pix_R.tobytes("png")))

            xLslot, yLslot, wLslot, hLslot = rect_to_reportlab_coords(left_slot, page_height)
            xRslot, yRslot, wRslot, hRslot = rect_to_reportlab_coords(right_slot, page_height)

            xL, yL, wL, hL = fit_and_center(xLslot, yLslot, wLslot, hLslot, pix_L)
            xR, yR, wR, hR = fit_and_center(xRslot, yRslot, wRslot, hRslot, pix_R)

            c.drawImage(img_L, xL, yL, width=wL, height=hL)
            c.drawImage(img_R, xR, yR, width=wR, height=hR)

            card_idx += 1

        c.showPage()

    c.save()
    return output.getvalue()


# ============================================================
# MODE 2 — A4 AUTOMÁTICO (sem template)
# ============================================================

def gerar_pdf_a4(card_files):
    page_width, page_height = A4
    rows = 5
    cols = 2

    margin_x = 36  # ~1cm
    margin_y = 36

    usable_w = page_width - 2 * margin_x
    usable_h = page_height - 2 * margin_y

    slot_w = usable_w / cols
    slot_h = usable_h / rows

    card_bytes_list = [f.read() for f in card_files]
    total_cards = len(card_bytes_list)
    card_idx = 0

    output = BytesIO()
    c = canvas.Canvas(output, pagesize=A4)

    zoom = 300 / 72

    while card_idx < total_cards:

        for r in range(rows):
            if card_idx >= total_cards:
                break

            y_top = page_height - margin_y - r * slot_h
            y_bottom = y_top - slot_h

            xLslot, yLslot, wLslot, hLslot = (margin_x, y_bottom, slot_w, slot_h)
            xRslot, yRslot, wRslot, hRslot = (margin_x + slot_w, y_bottom, slot_w, slot_h)

            card_pdf = fitz.open(stream=card_bytes_list[card_idx], filetype="pdf")
            page = card_pdf[0]

            cw, ch = page.rect.width, page.rect.height
            half = cw / 2

            clip_L = compute_trimmed_clip(page, fitz.Rect(0, 0, half, ch), zoom)
            clip_R = compute_trimmed_clip(page, fitz.Rect(half, 0, cw, ch), zoom)

            mat = fitz.Matrix(zoom, zoom)
            pix_L = page.get_pixmap(matrix=mat, clip=clip_L)
            pix_R = page.get_pixmap(matrix=mat, clip=clip_R)

            img_L = ImageReader(BytesIO(pix_L.tobytes("png")))
            img_R = ImageReader(BytesIO(pix_R.tobytes("png")))

            xL, yL, wL, hL = fit_and_center(xLslot, yLslot, wLslot, hLslot, pix_L)
            xR, yR, wR, hR = fit_and_center(xRslot, yRslot, wRslot, hRslot, pix_R)

            c.drawImage(img_L, xL, yL, width=wL, height=hL)
            c.drawImage(img_R, xR, yR, width=wR, height=hR)

            card_idx += 1

        c.showPage()

    c.save()
    return output.getvalue()


# ============================================================
# STREAMLIT UI
# ============================================================

st.title("IWBF Player Card Merger")

st.markdown("""
Upload **player card PDFs**.  
Optionally upload a **business card template PDF**.  

- If a business card template is uploaded → cards follow the template positions  
- If no template is uploaded → generates **normal A4 sheets with 5 cards per page (front+back)**  
- It’s strongly recommended that, if you are using business card paper (e.g., Avery Business Cards), you download the PDF template provided by the manufacturer.  
- If you are using Avery business card paper, **[click here to find and download the official template](https://www.avery.com/templates/category/business-cards)**.
""")

template = st.file_uploader("Optional: Upload a business card template PDF (e.g., Avery Template 5371 Business Cards)", type=["pdf"])
cards = st.file_uploader("Upload all the player cards you want to print", type=["pdf"], accept_multiple_files=True)

if st.button("Generate PDF"):
    if not cards:
        st.error("Upload at least one card PDF.")
    else:
        try:
            if template:
                pdf = gerar_pdf_final(template.read(), cards)
            else:
                pdf = gerar_pdf_a4(cards)

            st.success("PDF generated!")
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name="merged_cards_output.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"Error: {e}")
