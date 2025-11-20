import streamlit as st
from io import BytesIO
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


# ---------- Utility functions ----------

def detectar_slots_template(template_bytes):
    """
    Reads the template PDF, takes the first page, detects drawn rectangles
    and returns:
      - page_rect: the full page rectangle
      - slots_flat: list of fitz.Rect (all card slots, row1 left->right, row2 left->right, ...)
      - rows: list of rows, each row is [Rect_col_left, Rect_col_right, ...]
    Assumptions:
      - Card slots are rectangles with the same size.
      - They are arranged in a grid (rows / columns).
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
        # Ignore very small rectangles (borders, details, etc.)
        if r.width > 20 and r.height > 20:
            rects.append(r)

    if not rects:
        raise RuntimeError("Could not detect any card-sized rectangles in the template.")

    # Find the most common rectangle size (width x height)
    from collections import Counter
    size_counter = Counter((round(r.width, 1), round(r.height, 1)) for r in rects)
    main_size, _ = size_counter.most_common(1)[0]

    card_rects = [r for r in rects if (round(r.width, 1), round(r.height, 1)) == main_size]
    if len(card_rects) < 2:
        raise RuntimeError("Found too few card rectangles in the template.")

    # Group rectangles by row using y0 (PyMuPDF origin is top-left; y increases downward)
    from collections import defaultdict
    rows_dict = defaultdict(list)
    for r in card_rects:
        key_y = round(r.y0, 1)
        rows_dict[key_y].append(r)

    # Sort rows (top -> bottom), and within each row sort columns (left -> right)
    row_keys = sorted(rows_dict.keys())
    rows = []
    for y in row_keys:
        row_rects = sorted(rows_dict[y], key=lambda rc: rc.x0)
        rows.append(row_rects)

    slots_flat = [rc for row in rows for rc in row]
    return page_rect, slots_flat, rows


def rect_to_reportlab_coords(r, page_height):
    """
    Convert a fitz.Rect (origin top-left) to ReportLab coordinates
    (origin bottom-left).
    """
    x = r.x0
    y = page_height - r.y1
    w = r.width
    h = r.height
    return x, y, w, h


def gerar_pdf_final(template_bytes, card_files):
    """
    - Reads the template and detects card slots.
    - Assumes 2 slots per row (left/right) = 1 full card (front + back).
    - For each player card PDF (with two sides side-by-side):
        * splits the page in half (vertical),
        * places left half into the left slot,
        * places right half into the right slot.
    - When all rows are used, starts a new page.
    Returns the final PDF as bytes.
    """
    # Detect slots on the template page
    page_rect, slots_flat, rows = detectar_slots_template(template_bytes)
    page_width, page_height = page_rect.width, page_rect.height

    slots_per_row = len(rows[0])
    if slots_per_row < 2 or slots_per_row % 2 != 0:
        raise RuntimeError(
            f"Unexpected layout: first row has {slots_per_row} rectangles; "
            "expected an even number (left/right per card)."
        )

    # Render the template page as an image (used as background on each page)
    doc_template = fitz.open(stream=template_bytes, filetype="pdf")
    page_template = doc_template[0]
    zoom_template = 300 / 72  # ~300 dpi
    mat_template = fitz.Matrix(zoom_template, zoom_template)
    pix_template = page_template.get_pixmap(matrix=mat_template, alpha=False)
    template_img = ImageReader(BytesIO(pix_template.tobytes("png")))

    # Read all player card PDFs into memory (bytes list)
    card_bytes_list = [f.read() for f in card_files]
    total_cards = len(card_bytes_list)
    card_idx = 0

    # Create output PDF with ReportLab
    output_buffer = BytesIO()
    c = canvas.Canvas(output_buffer, pagesize=(page_width, page_height))

    while card_idx < total_cards:
        # Draw the template as the background
        c.drawImage(template_img, 0, 0, width=page_width, height=page_height)

        # Iterate over template rows; each row holds one full card (2 slots)
        for row_rects in rows:
            if card_idx >= total_cards:
                break
            if len(row_rects) < 2:
                continue  # safety

            slot_left = row_rects[0]   # left column (first half of card)
            slot_right = row_rects[1]  # right column (second half of card)

            card_bytes = card_bytes_list[card_idx]
            doc_card = fitz.open(stream=card_bytes, filetype="pdf")
            page_card = doc_card[0]

            # Split the player card page in half (vertical)
            card_w, card_h = page_card.rect.width, page_card.rect.height
            mid_x = card_w / 2.0
            zoom_card = 300 / 72
            mat_card = fitz.Matrix(zoom_card, zoom_card)

            # Left side
            clip_left = fitz.Rect(0, 0, mid_x, card_h)
            pix_left = page_card.get_pixmap(matrix=mat_card, clip=clip_left, alpha=False)
            img_left = ImageReader(BytesIO(pix_left.tobytes("png")))

            # Right side
            clip_right = fitz.Rect(mid_x, 0, card_w, card_h)
            pix_right = page_card.get_pixmap(matrix=mat_card, clip=clip_right, alpha=False)
            img_right = ImageReader(BytesIO(pix_right.tobytes("png")))

            # Convert slot coordinates to ReportLab coordinates
            xL, yL, wL, hL = rect_to_reportlab_coords(slot_left, page_height)
            xR, yR, wR, hR = rect_to_reportlab_coords(slot_right, page_height)

            # Draw both halves into the slot rectangles.
            # IMPORTANT: preserveAspectRatio=False → fill the slot completely.
            c.drawImage(
                img_left,
                xL,
                yL,
                width=wL,
                height=hL,
                preserveAspectRatio=False,
                anchor="sw",
            )

            c.drawImage(
                img_right,
                xR,
                yR,
                width=wR,
                height=hR,
                preserveAspectRatio=False,
                anchor="sw",
            )

            card_idx += 1

        c.showPage()

    c.save()
    return output_buffer.getvalue()


# ---------- Streamlit UI ----------

st.title("🪪 Automatic card imposition on PDF template")

st.markdown(
    """
This app:

1. Takes a **PDF template** (e.g. Avery 8859) that contains the card grid.  
2. Detects all card rectangles automatically.  
3. Accepts multiple **player card PDFs**, each one with **two sides side-by-side**.  
4. For each card:
   - left half → left rectangle in a row  
   - right half → right rectangle in the same row  
5. Produces a final PDF ready to print on the card sheet.
"""
)

template_file = st.file_uploader("1️⃣ Upload the card sheet template PDF", type=["pdf"])

card_files = st.file_uploader(
    "2️⃣ Upload individual player card PDFs (each with front & back side-by-side)",
    type=["pdf"],
    accept_multiple_files=True,
)

if st.button("3️⃣ Generate final PDF"):
    if not template_file:
        st.error("Please upload the template PDF first.")
    elif not card_files:
        st.error("Please upload at least one player card PDF.")
    else:
        try:
            template_bytes = template_file.read()
            pdf_bytes = gerar_pdf_final(template_bytes, card_files)

            st.success("PDF generated successfully! 🎉")
            st.download_button(
                label="⬇️ Download final PDF",
                data=pdf_bytes,
                file_name="cards_on_template.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Error while generating PDF: {e}")
