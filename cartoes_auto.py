import streamlit as st
from io import BytesIO
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


# ---------- Utility functions ----------

def detectar_slots_template(template_bytes):
    """
    Detects card rectangles in the template PDF automatically.
    Returns:
      - page_rect: the full page rectangle
      - slots_flat: list of all card rectangles (row1 left->right, row2 left->right, ...)
      - rows: list of rows, each row = [left_slot, right_slot, ...]
    """
    doc = fitz.open(stream=template_bytes, filetype="pdf")
    page = doc[0]
    page_rect = page.rect

    drawings = page.get_drawings()
    rects = []

    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        if r.width > 20 and r.height > 20:  # ignore tiny lines
            rects.append(r)

    if not rects:
        raise RuntimeError("No card rectangles found in template PDF.")

    # Find most common rectangle size
    from collections import Counter
    size_counter = Counter((round(r.width, 1), round(r.height, 1)) for r in rects)
    main_size, _ = size_counter.most_common(1)[0]

    card_rects = [r for r in rects if (round(r.width, 1), round(r.height, 1)) == main_size]

    if len(card_rects) < 2:
        raise RuntimeError("Template does not contain enough card rectangles.")

    # Group by row (y0), PyMuPDF origin = top-left
    from collections import defaultdict
    rows_dict = defaultdict(list)

    for r in card_rects:
        key_y = round(r.y0, 1)
        rows_dict[key_y].append(r)

    # Sort rows top->bottom, columns left->right
    row_keys = sorted(rows_dict.keys())
    rows = []

    for y in row_keys:
        row_rects = sorted(rows_dict[y], key=lambda rc: rc.x0)
        rows.append(row_rects)

    slots_flat = [rc for row in rows for rc in row]
    return page_rect, slots_flat, rows


def rect_to_reportlab_coords(r, page_height):
    """Convert PyMuPDF top-left coordinates to ReportLab bottom-left coordinates."""
    x = r.x0
    y = page_height - r.y1
    w = r.width
    h = r.height
    return x, y, w, h


def gerar_pdf_final(template_bytes, card_files):
    """
    Creates the final PDF:
    - Detects rectangles in the template.
    - For each player card PDF (two sides side-by-side):
        * Split the page vertically
        * Place left half in left slot, right half in right slot
    - Each row = 1 player card (front+back)
    """
    page_rect, slots_flat, rows = detectar_slots_template(template_bytes)
    page_width, page_height = page_rect.width, page_rect.height

    slots_per_row = len(rows[0])
    if slots_per_row < 2 or slots_per_row % 2 != 0:
        raise RuntimeError(
            f"Template row has {slots_per_row} rectangles; expected an even number (2 per card)."
        )

    # Render template as image for reuse
    doc_template = fitz.open(stream=template_bytes, filetype="pdf")
    page_template = doc_template[0]

    zoom_template = 300 / 72
    mat_template = fitz.Matrix(zoom_template, zoom_template)
    pix_template = page_template.get_pixmap(matrix=mat_template, alpha=False)
    template_img = ImageReader(BytesIO(pix_template.tobytes("png")))

    # Read all cards into memory
    card_bytes_list = [f.read() for f in card_files]
    total_cards = len(card_bytes_list)
    card_idx = 0

    output_buffer = BytesIO()
    c = canvas.Canvas(output_buffer, pagesize=(page_width, page_height))

    while card_idx < total_cards:

        # draw template background
        c.drawImage(template_img, 0, 0, width=page_width, height=page_height)

        for row_rects in rows:
            if card_idx >= total_cards:
                break
            if len(row_rects) < 2:
                continue

            slot_left = row_rects[0]
            slot_right = row_rects[1]

            card_bytes = card_bytes_list[card_idx]
            doc_card = fitz.open(stream=card_bytes, filetype="pdf")
            page_card = doc_card[0]

            # split card in half
            card_w = page_card.rect.width
            card_h = page_card.rect.height
            mid_x = card_w / 2.0

            zoom_card = 300 / 72
            mat_card = fitz.Matrix(zoom_card, zoom_card)

            # left half
            clip_L = fitz.Rect(0, 0, mid_x, card_h)
            pix_L = page_card.get_pixmap(matrix=mat_card, clip=clip_L, alpha=False)
            img_L = ImageReader(BytesIO(pix_L.tobytes("png")))

            # right half
            clip_R = fitz.Rect(mid_x, 0, card_w, card_h)
            pix_R = page_card.get_pixmap(matrix=mat_card, clip=clip_R, alpha=False)
            img_R = ImageReader(BytesIO(pix_R.tobytes("png")))

            # coordinates
            xL, yL, wL, hL = rect_to_reportlab_coords(slot_left, page_height)
            xR, yR, wR, hR = rect_to_reportlab_coords(slot_right, page_height)

            # fill the full rectangle (NO aspect ratio)
            c.drawImage(img_L, xL, yL, width=wL, height=hL,
                        preserveAspectRatio=False, anchor="sw")

            c.drawImage(img_R, xR, yR, width=wR, height=hR,
                        preserveAspectRatio=False, anchor="sw")

            card_idx += 1

        c.showPage()

    c.save()
    return output_buffer.getvalue()


# ---------- Streamlit UI ----------

st.title("🪪 Automatic Card Generator – Two Sides on PDF Template")

st.markdown("""
Upload a card sheet template (PDF with card rectangles), then upload multiple
player card PDFs (each PDF containing the **front and back side side-by-side**).

Each row of the template becomes **one complete card** (left rectangle = front,
right rectangle = back).  
Cards now fill **100% of the rectangle area** (no empty space).
""")

template_file = st.file_uploader("1️⃣ Upload card template PDF", type=["pdf"])
card_files = st.file_uploader("2️⃣ Upload player card PDFs", type=["pdf"], accept_multiple_files=True)

if st.button("3️⃣ Generate final PDF"):
    if not template_file:
        st.error("Please upload the template PDF.")
    elif not card_files:
        st.error("Please upload at least one player card PDF.")
    else:
        try:
            template_bytes = template_file.read()
            pdf_bytes = gerar_pdf_final(template_bytes, card_files)

            st.success("PDF generated successfully! 🎉")
            st.download_button(
                "⬇️ Download final PDF",
                data=pdf_bytes,
                file_name="cards_on_template.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"Error: {e}")
