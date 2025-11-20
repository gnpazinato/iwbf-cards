{\rtf1\ansi\ansicpg1252\cocoartf2867
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 import streamlit as st\
from io import BytesIO\
import fitz  # PyMuPDF\
from reportlab.pdfgen import canvas\
from reportlab.lib.utils import ImageReader\
\
\
# ---------- Fun\'e7\'f5es utilit\'e1rias ----------\
\
def detectar_slots_template(template_bytes):\
    """\
    L\'ea o PDF template, pega a primeira p\'e1gina, detecta ret\'e2ngulos desenhados\
    e devolve:\
      - page_rect: ret\'e2ngulo da p\'e1gina\
      - slots_flat: lista de fitz.Rect dos cart\'f5es na ordem linha1 esq->dir, linha2 esq->dir, ...\
      - rows: lista de linhas; cada linha \'e9 [Rect_col_esq, Rect_col_dir, ...]\
    Pressupostos:\
      - Os cart\'f5es s\'e3o desenhados como ret\'e2ngulos com mesmo tamanho.\
      - Est\'e3o organizados em grade (linhas / colunas).\
    """\
    doc = fitz.open(stream=template_bytes, filetype="pdf")\
    page = doc[0]\
    page_rect = page.rect\
\
    drawings = page.get_drawings()\
    rects = []\
    for d in drawings:\
        r = d.get("rect")\
        if r is None:\
            continue\
        # Ignora ret\'e2ngulos muito pequenos (linhas, detalhes, etc.)\
        if r.width > 20 and r.height > 20:\
            rects.append(r)\
\
    if not rects:\
        raise RuntimeError("N\'e3o foi poss\'edvel detectar ret\'e2ngulos de cart\'f5es no template.")\
\
    # Descobre o tamanho mais comum de ret\'e2ngulo (largura x altura)\
    from collections import Counter\
    size_counter = Counter((round(r.width, 1), round(r.height, 1)) for r in rects)\
    main_size, _ = size_counter.most_common(1)[0]\
\
    card_rects = [r for r in rects if (round(r.width, 1), round(r.height, 1)) == main_size]\
    if len(card_rects) < 2:\
        raise RuntimeError("Foram encontrados poucos ret\'e2ngulos com tamanho de cart\'e3o no template.")\
\
    # Agrupa por linha usando a coordenada y0 (origem topo-esquerda no PyMuPDF)\
    from collections import defaultdict\
    rows_dict = defaultdict(list)\
    for r in card_rects:\
        key_y = round(r.y0, 1)\
        rows_dict[key_y].append(r)\
\
    # Ordena linhas (de cima pra baixo) e, dentro de cada linha, colunas (esq->dir)\
    row_keys = sorted(rows_dict.keys())\
    rows = []\
    for y in row_keys:\
        row_rects = sorted(rows_dict[y], key=lambda rc: rc.x0)\
        rows.append(row_rects)\
\
    slots_flat = [rc for row in rows for rc in row]\
    return page_rect, slots_flat, rows\
\
\
def rect_to_reportlab_coords(r, page_height):\
    """\
    Converte fitz.Rect (origem topo-esquerda) para coords do reportlab\
    (origem canto inferior esquerdo).\
    """\
    x = r.x0\
    y = page_height - r.y1\
    w = r.width\
    h = r.height\
    return x, y, w, h\
\
\
def gerar_pdf_final(template_bytes, card_files):\
    """\
    - L\'ea o template e detecta slots\
    - Assume 2 slots por linha (esquerda/direita) = 1 cart\'e3o\
    - Para cada carteirinha (PDF com 2 lados lado a lado):\
        * corta a p\'e1gina ao meio\
        * encaixa lado esq. no slot esq.\
        * encaixa lado dir. no slot dir.\
    - Quando enche as linhas, come\'e7a nova p\'e1gina.\
    """\
    # Detecta slots\
    page_rect, slots_flat, rows = detectar_slots_template(template_bytes)\
    page_width, page_height = page_rect.width, page_rect.height\
\
    slots_por_linha = len(rows[0])\
    if slots_por_linha < 2 or slots_por_linha % 2 != 0:\
        raise RuntimeError(\
            f"Layout inesperado: a primeira linha tem \{slots_por_linha\} ret\'e2ngulos; "\
            "esperado n\'famero par (coluna esq/dir por cart\'e3o)."\
        )\
\
    # Prepara imagem do template para usar de fundo em todas as p\'e1ginas\
    doc_template = fitz.open(stream=template_bytes, filetype="pdf")\
    page_template = doc_template[0]\
    zoom_template = 300 / 72  # ~300 dpi\
    mat_template = fitz.Matrix(zoom_template, zoom_template)\
    pix_template = page_template.get_pixmap(matrix=mat_template, alpha=False)\
    template_img = ImageReader(BytesIO(pix_template.tobytes("png")))\
\
    # Cria o PDF de sa\'edda\
    output_buffer = BytesIO()\
    c = canvas.Canvas(output_buffer, pagesize=(page_width, page_height))\
\
    total_cards = len(card_files)\
    card_idx = 0\
\
    while card_idx < total_cards:\
        # desenha o template como plano de fundo\
        c.drawImage(template_img, 0, 0, width=page_width, height=page_height)\
\
        # percorre as linhas do template\
        for row_rects in rows:\
            if card_idx >= total_cards:\
                break\
            if len(row_rects) < 2:\
                continue  # seguran\'e7a\
\
            slot_esq = row_rects[0]     # ret\'e2ngulo da coluna esquerda (lado 1)\
            slot_dir = row_rects[1]     # ret\'e2ngulo da coluna direita (lado 2)\
\
            # l\'ea o PDF da carteirinha atual\
            card_file = card_files[card_idx]\
            card_bytes = card_file.read()\
            doc_card = fitz.open(stream=card_bytes, filetype="pdf")\
            page_card = doc_card[0]\
\
            # divide a p\'e1gina da carteirinha ao meio (vertical)\
            card_w, card_h = page_card.rect.width, page_card.rect.height\
            mid_x = card_w / 2.0\
            zoom_card = 300 / 72\
            mat_card = fitz.Matrix(zoom_card, zoom_card)\
\
            # lado esquerdo\
            clip_left = fitz.Rect(0, 0, mid_x, card_h)\
            pix_left = page_card.get_pixmap(matrix=mat_card, clip=clip_left, alpha=False)\
            img_left = ImageReader(BytesIO(pix_left.tobytes("png")))\
\
            # lado direito\
            clip_right = fitz.Rect(mid_x, 0, card_w, card_h)\
            pix_right = page_card.get_pixmap(matrix=mat_card, clip=clip_right, alpha=False)\
            img_right = ImageReader(BytesIO(pix_right.tobytes("png")))\
\
            # Converte posi\'e7\'f5es dos slots para coords do reportlab\
            xL, yL, wL, hL = rect_to_reportlab_coords(slot_esq, page_height)\
            xR, yR, wR, hR = rect_to_reportlab_coords(slot_dir, page_height)\
\
            # Desenha as duas metades nos respectivos ret\'e2ngulos\
            c.drawImage(img_left, xL, yL, width=wL, height=hL,\
                        preserveAspectRatio=True, anchor='sw')\
            c.drawImage(img_right, xR, yR, width=wR, height=hR,\
                        preserveAspectRatio=True, anchor='sw')\
\
            card_idx += 1\
\
        c.showPage()\
\
    c.save()\
    return output_buffer.getvalue()\
\
\
# ---------- Interface Streamlit ----------\
\
st.title("\uc0\u55358 \u57002  Montador autom\'e1tico de cart\'f5es em template PDF")\
\
st.markdown("""\
- Envie o **PDF template** da folha de cart\'e3o (ex.: Avery 8859).  \
- Envie os **PDFs individuais** das carteirinhas (cada um com **dois lados lado a lado**).  \
- O app detecta automaticamente os ret\'e2ngulos do template e coloca:\
  - lado esquerdo do cart\'e3o \uc0\u8594  ret\'e2ngulo da esquerda da linha  \
  - lado direito do cart\'e3o \uc0\u8594  ret\'e2ngulo da direita da mesma linha  \
- 10 ret\'e2ngulos \uc0\u8594  5 cart\'f5es por p\'e1gina, etc.\
""")\
\
template_file = st.file_uploader("1\uc0\u65039 \u8419  Envie o PDF *template* da folha de cart\'e3o", type=["pdf"])\
card_files = st.file_uploader(\
    "2\uc0\u65039 \u8419  Envie os PDFs individuais das carteirinhas (cada um com frente/verso lado a lado)",\
    type=["pdf"],\
    accept_multiple_files=True\
)\
\
if st.button("3\uc0\u65039 \u8419  Gerar PDF final"):\
    if not template_file:\
        st.error("Envie primeiro o PDF do template.")\
    elif not card_files:\
        st.error("Envie pelo menos um PDF de carteirinha.")\
    else:\
        try:\
            template_bytes = template_file.read()\
            pdf_bytes = gerar_pdf_final(template_bytes, card_files)\
\
            st.success("PDF gerado com sucesso! \uc0\u55356 \u57225 ")\
            st.download_button(\
                label="\uc0\u11015 \u65039  Baixar PDF final",\
                data=pdf_bytes,\
                file_name="cartoes_template_auto.pdf",\
                mime="application/pdf"\
            )\
        except Exception as e:\
            st.error(f"Ocorreu um erro ao gerar o PDF: \{e\}")}