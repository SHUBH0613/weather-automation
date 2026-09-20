"""
ppt_generator.py
Fills weather_template.pptx matching the exact visual structure,
Arial 14pt fonts, single-line text formatting, proper spacing without overlap,
and dynamic date updates across all slide titles.
"""

import copy
import os
from typing import Optional

import pptx
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template", "weather_template.pptx")
OUTPUT_DIR = os.path.dirname(__file__)

COLOR_MAP = {
    "GO": RGBColor(0x00, 0xB0, 0x50),         # Green
    "LTD GO": RGBColor(0xFF, 0xC0, 0x00),     # Amber / Gold
    "NO GO": RGBColor(0xFF, 0x00, 0x00),      # Red
    "DATA UNAVAILABLE": RGBColor(0xC0, 0x00, 0x00), # Dark Red
}

def _fmt_num(val, unit: str = "") -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val)}{unit}"
        return f"{round(val, 1)}{unit}"
    return f"{val}{unit}"

def _update_title_text_box(slide, new_date_str: str):
    """Update title heading without altering shape position or layout."""
    for shape in slide.shapes:
        if shape.has_text_frame and "WX UPDATE" in shape.text_frame.text:
            tf = shape.text_frame
            for para in tf.paragraphs:
                if "WX UPDATE" in para.text:
                    para.text = new_date_str
                    if para.runs:
                        r = para.runs[0]
                        r.font.name = "Arial"
                        r.font.size = Pt(24)
                        r.font.bold = True
                        r.font.underline = True
                        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            break

def _fill_2line_cell(cell, top_text: str, bottom_text: str):
    """Fill a clean two-line cell (Windy on line 1, Accuwx on line 2) in Arial 14."""
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = Inches(0.04)
    cell.margin_right = Inches(0.04)
    cell.margin_top = Inches(0.02)
    cell.margin_bottom = Inches(0.02)

    tf = cell.text_frame
    tf.word_wrap = False

    while len(tf.paragraphs) < 2:
        tf.add_paragraph()
    while len(tf.paragraphs) > 2:
        p_elem = tf.paragraphs[-1]._p
        p_elem.getparent().remove(p_elem)

    p0 = tf.paragraphs[0]
    p0.text = ""
    p0.space_after = Pt(2)
    r0 = p0.add_run()
    r0.text = top_text
    r0.font.name = "Arial"
    r0.font.size = Pt(14)

    p1 = tf.paragraphs[1]
    p1.text = ""
    p1.space_before = Pt(2)
    r1 = p1.add_run()
    r1.text = bottom_text
    r1.font.name = "Arial"
    r1.font.size = Pt(14)

def _fill_remarks_cell(cell, w_remark: str, a_remark: str):
    """Fill remarks cell in Arial 14 Bold with color-coded GO/LTD GO/NO GO."""
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = Inches(0.04)
    cell.margin_right = Inches(0.04)
    cell.margin_top = Inches(0.02)
    cell.margin_bottom = Inches(0.02)

    tf = cell.text_frame
    tf.word_wrap = False

    while len(tf.paragraphs) < 2:
        tf.add_paragraph()
    while len(tf.paragraphs) > 2:
        p_elem = tf.paragraphs[-1]._p
        p_elem.getparent().remove(p_elem)

    p0 = tf.paragraphs[0]
    p0.text = ""
    p0.space_after = Pt(2)
    r0 = p0.add_run()
    r0.text = w_remark
    r0.font.name = "Arial"
    r0.font.size = Pt(14)
    r0.font.bold = True
    r0.font.color.rgb = COLOR_MAP.get(w_remark, RGBColor(0x00, 0x00, 0x00))

    p1 = tf.paragraphs[1]
    p1.text = ""
    p1.space_before = Pt(2)
    r1 = p1.add_run()
    r1.text = a_remark
    r1.font.name = "Arial"
    r1.font.size = Pt(14)
    r1.font.bold = True
    r1.font.color.rgb = COLOR_MAP.get(a_remark, RGBColor(0x00, 0x00, 0x00))

def generate_ppt(date_str: str, weather_data: list, imd_image_path: str) -> str:
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")

    prs = pptx.Presentation(TEMPLATE_PATH)
    date_label = f"WX UPDATE {date_str}"

    # SLIDE 1 — Title Slide
    slide1 = prs.slides[0]
    _update_title_text_box(slide1, date_label)

    # SLIDE 2 — Weather Table + Title
    slide2 = prs.slides[1]
    _update_title_text_box(slide2, date_label)

    table_shape = None
    for shape in slide2.shapes:
        if shape.has_table and len(shape.table.columns) == 6:
            table_shape = shape
            break

    if table_shape:
        # Position table just below the red banner so there is ZERO overlap
        # Banner bottom is at -104075 + 550200 = 446125 EMU
        table_shape.top = 450000
        tbl = table_shape.table

        # Rebalance columns: Col 1 has 2.2 inches so AHMEDNAGAR/AURANGABAD fit on one line in Arial 14
        column_widths = [700000, 2200000, 1250000, 1700000, 1600000, 1694025]
        for i, w in enumerate(column_widths):
            tbl.columns[i].width = w

        # Format header row (Row 0)
        for c_idx in range(6):
            cell = tbl.cell(0, c_idx)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = Inches(0.04)
            cell.margin_right = Inches(0.04)
            cell.margin_top = Inches(0.04)
            cell.margin_bottom = Inches(0.04)
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.name = "Arial"
                    r.font.size = Pt(14)
                    r.font.bold = True
                    r.font.underline = True

        needed = 1 + len(weather_data)
        while len(tbl.rows) < needed:
            new_tr = copy.deepcopy(tbl._tbl.tr_lst[-1])
            tbl._tbl.append(new_tr)
        while len(tbl.rows) > needed:
            tbl._tbl.remove(tbl._tbl.tr_lst[-1])

        for idx, item in enumerate(weather_data):
            row = tbl.rows[idx + 1]
            row.height = 550000

            # Col 0: Ser No
            c0 = row.cells[0]
            c0.vertical_anchor = MSO_ANCHOR.MIDDLE
            c0.margin_left = Inches(0.04)
            c0.margin_right = Inches(0.04)
            c0.text = ""
            p0 = c0.text_frame.paragraphs[0]
            r0 = p0.add_run()
            r0.text = str(idx + 1)
            r0.font.name = "Arial"
            r0.font.size = Pt(14)
            r0.font.bold = True

            # Col 1: Loc (all words in one line, no wrapping)
            c1 = row.cells[1]
            c1.vertical_anchor = MSO_ANCHOR.MIDDLE
            c1.margin_left = Inches(0.04)
            c1.margin_right = Inches(0.04)
            c1.text_frame.word_wrap = False
            c1.text = ""
            p1 = c1.text_frame.paragraphs[0]
            r1 = p1.add_run()
            r1.text = item["location"].upper()
            r1.font.name = "Arial"
            r1.font.size = Pt(14)
            r1.font.bold = True

            # Col 2: Wx App (Windy on line 1, Accuwx on line 2)
            _fill_2line_cell(row.cells[2], "Windy", "Accuwx")

            # Col 3: Forecast Rain mm/%
            w_rain = _fmt_num(item.get("windy_rain"), "mm")
            if item.get("accu_rain_prob") is not None:
                a_rain = f"{int(item['accu_rain_prob'])}%"
            elif item.get("accu_rain_mm") is not None:
                a_rain = f"{item['accu_rain_mm']}mm"
            else:
                a_rain = "N/A"
            _fill_2line_cell(row.cells[3], w_rain, a_rain)

            # Col 4: Cloud cover %
            w_cloud = _fmt_num(item.get("windy_cloud"), "%")
            a_cloud = _fmt_num(item.get("accu_cloud"), "%")
            _fill_2line_cell(row.cells[4], w_cloud, a_cloud)

            # Col 5: Remarks (Color-coded)
            w_rmk = item.get("windy_remark", "DATA UNAVAILABLE")
            a_rmk = item.get("accu_remark", "DATA UNAVAILABLE")
            _fill_remarks_cell(row.cells[5], w_rmk, a_rmk)

    # SLIDE 3 — IMD Map Replacement
    slide3 = prs.slides[2]
    _update_title_text_box(slide3, date_label)

    pic_shape = None
    for shape in slide3.shapes:
        if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE:
            pic_shape = shape
            break

    if imd_image_path and os.path.exists(imd_image_path) and os.path.getsize(imd_image_path) > 5000:
        if pic_shape:
            left, top, width, height = pic_shape.left, pic_shape.top, pic_shape.width, pic_shape.height
            sp_tree = slide3.shapes._spTree
            sp_tree.remove(pic_shape._element)
            slide3.shapes.add_picture(imd_image_path, left, top, width, height)
    else:
        if pic_shape:
            left, top, width, height = pic_shape.left, pic_shape.top, pic_shape.width, pic_shape.height
            sp_tree = slide3.shapes._spTree
            sp_tree.remove(pic_shape._element)
            txb = slide3.shapes.add_textbox(left, top, width, height)
            tf = txb.text_frame
            tf.text = "IMD DATA UNAVAILABLE"
            tf.paragraphs[0].runs[0].font.size = Pt(28)
            tf.paragraphs[0].runs[0].font.bold = True
            tf.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0x00, 0x00)

    out_name = f"WX UPDATE {date_str}.pptx"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    prs.save(out_path)
    return out_path
