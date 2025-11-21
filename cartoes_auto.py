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


def trim_pixmap(pix, bg_threshold=250):
    """
    Content-aware trim: remove white borders around the card.
    - bg_threshold: 0..255, higher = more aggressive trim (treat near-white as background)
    Returns a new Pixmap (cropped) or the original if nothing is found.
    """
    w, h, n = pix.width, pix.height, pix.n
    samples = pix.samples  # bytes

    min_x, min_y = w, h
    max_x, max_y = -1, -1

    # Iterate over all pixels
    for y in range(h):
        row_index = y * w * n
        for x in range(w):
            idx = row_index + x * n
            # check RGB channels only (ignore alpha if present)
            r = samples[idx]
            g = samples[idx + 1] if n > 1 else samples[idx]
            b = samples[idx + 2] if n > 2 else samples[idx]

            # pixel considered "content" if any channel is darker than bg_threshold
            if r < bg_threshold or g < bg_threshold or b < bg_threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    # If no content found, return original pixmap
    if max_x < 0 or max_y < 0:
        return pix

    # Create crop rectangle in pixel coordinates
    # +1 on max_x / max_y to include the last pixel
    rect = fitz.Rect(min_x, min_y, max_x + 1, max_y + 1)
    cropped = fitz.Pixmap(pix, rect)
    return cropped


def fit_and_center(slot_x, slot_y, slot_w, slot_h, pix, margin_factor=0.95):
    """
    Fit the pixmap inside the given slot:
      - keep aspect ratio
      - apply a global margin factor (e.g. 0.95 = 5% padding)
      - center horizontally and vertically.
    Returns (x, y, render_w, render_h).
    """
    img_w = pix.width
    img_h = pix.height

    # scale to fit inside slot
    scale = min(slot_w / img_w, slot_h / img_h)

    # apply margin factor to leave uniform borders
    scale *= margin_factor

    render_w = img_w * scale
    render_h = img_h * scale

    x = slot_x + (slot_w - render_w) / 2.0
    y = slot_y + (slot_h - render_h) / 2.0

    return x, y, render_w, render_h


def gerar_pdf_final(template_bytes, card_files):
    """
    Creates the final PDF:
    - Detects rectangles in the template.
    - For each player card PDF (two sides side-by-side):
        * Split the page vertically into left/right halves
        * Render each half as an image
        * Trim white borders (content-aware crop)
        * Fit and center each half in the corresponding slot
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

            card_w = page_card.rect.width
            card_h = page_card.rect.height
            half_w = card_w / 2.0

            zoom_card = 300 / 72
            mat_card = fitz.Matrix(zoom_card, zoom_card)

            # left half (0 .. half_w)
            clip_L = fitz.Rect(0, 0, half_w, card_h)
            pix_L = page_card.get_pixmap(matrix=mat_card, clip=clip_L, alpha=False)
            pix_L = trim_pixmap(pix_L)  # remove white borders
            img_L = ImageReader(BytesIO(pix_L.tobytes("png")))

            # right half (half_w .. card_w)
            clip_R = fitz.Rect(half_w, 0, card_w, card_h)
            pix_R = page_card.get_pixmap(matrix=mat_card, clip=clip_R, alpha=False)
            pix_R = trim_pixmap(pix_R)  # remove white borders
            img_R = ImageReader(BytesIO(pix_R.tobytes("png")))

            # slot coordinates (ReportLab)
            xL_slot, yL_slot, wL_slot, hL_slot = rect_to_reportlab_coords(slot_left, page_height)
            xR_slot, yR_slot, wR_slot, hR_slot = rect_to_reportlab_coords(slot_right, page_height)

            # compute centered positions/sizes with margin
            xL, yL, wL, hL = fit_and_center(xL_slot, yL_slot, wL_slot, hL_slot, pix_L)
            xR, yR, wR, hR = fit_and_center(xR_slot, yR_slot, wR_slot, hR_slot, pix_R)

            # draw images, centered in the slot
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
Upload a **card sheet template** (PDF with the card rectangles), then upload multiple
**player card PDFs** (each PDF containing the **front and back side side-by-side**).

Each row of the template becomes **one complete card** (left rectangle = front,
right rectangle = back).  

The app now:
- **removes white borders** around the card artwork, and  
- **centers** the card horizontally and vertically inside each rectangle.
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
