"""
ppt_generator.py
Generates presentation from weather_template.pptx:
- Title Slide with Date Range & Cities.
- Multi-day Weather Tables: One slide per date listing all selected cities (Arial 14, no broken words, GO/LTD GO/NO GO).
- Multi-state IMD Warning Map slides with transparent Google Map layer and official IMD Color Code Legend.
- Clear notification when forecast exceeds IMD's 6-day horizon.
"""

import copy
import os
from typing import List, Dict, Any, Optional

import pptx
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

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

def _update_slide_title(slide, title_text: str, font_size_pt: Optional[float] = None):
    """Update title heading within the red banner without altering shape position or layout."""
    if font_size_pt is None:
        font_size_pt = 16.0 if len(title_text) > 30 else 22.0

    for shape in slide.shapes:
        if shape.has_text_frame and ("WX UPDATE" in shape.text_frame.text or "IMD" in shape.text_frame.text):
            tf = shape.text_frame
            tf.word_wrap = False
            for para in tf.paragraphs:
                para.text = title_text
                para.alignment = PP_ALIGN.CENTER
                if para.runs:
                    r = para.runs[0]
                    r.font.name = "Arial"
                    r.font.size = Pt(font_size_pt)
                    r.font.bold = True
                    r.font.underline = True
                    r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            return

    # If no matching title banner exists, create full-width red banner rectangle matching template
    banner = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(10.0), Inches(0.6))
    banner.fill.solid()
    banner.fill.fore_color.rgb = RGBColor(0xFF, 0x00, 0x00)
    banner.line.fill.background()
    tf = banner.text_frame
    tf.word_wrap = False
    p = tf.paragraphs[0]
    p.text = title_text
    p.alignment = PP_ALIGN.CENTER
    r = p.runs[0] if p.runs else p.add_run()
    r.font.name = "Arial"
    r.font.size = Pt(font_size_pt)
    r.font.bold = True
    r.font.underline = True
    r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)


def _fill_2line_cell(cell, top_text: str, bottom_text: str, font_size_pt: float = 12.0):
    """Fill a clean two-line cell (Windy on line 1, Accuwx on line 2) with compact spacing."""
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
    p0.space_after = Pt(1)
    r0 = p0.add_run()
    r0.text = top_text
    r0.font.name = "Arial"
    r0.font.size = Pt(font_size_pt)

    p1 = tf.paragraphs[1]
    p1.text = ""
    p1.space_before = Pt(1)
    r1 = p1.add_run()
    r1.text = bottom_text
    r1.font.name = "Arial"
    r1.font.size = Pt(font_size_pt)

def _fill_remarks_cell(cell, w_remark: str, a_remark: str, font_size_pt: float = 12.0):
    """Fill remarks cell in Arial Bold with color-coded GO/LTD GO/NO GO without text wrapping."""
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

    # Prevent long text wrapping that causes row height explosion
    disp_w = "DATA N/A" if w_remark == "DATA UNAVAILABLE" else w_remark
    disp_a = "DATA N/A" if a_remark == "DATA UNAVAILABLE" else a_remark

    p0 = tf.paragraphs[0]
    p0.text = ""
    p0.space_after = Pt(1)
    r0 = p0.add_run()
    r0.text = disp_w
    r0.font.name = "Arial"
    r0.font.size = Pt(font_size_pt)
    r0.font.bold = True
    r0.font.color.rgb = COLOR_MAP.get(w_remark, RGBColor(0x00, 0x00, 0x00))

    p1 = tf.paragraphs[1]
    p1.text = ""
    p1.space_before = Pt(1)
    r1 = p1.add_run()
    r1.text = disp_a
    r1.font.name = "Arial"
    r1.font.size = Pt(font_size_pt)
    r1.font.bold = True
    r1.font.color.rgb = COLOR_MAP.get(a_remark, RGBColor(0x00, 0x00, 0x00))

def _populate_table(slide, weather_data: List[Dict[str, Any]]):
    """Populates the 6-column table and positions the Legend table without overlap."""
    table_shape = None
    for shape in slide.shapes:
        if shape.has_table and len(shape.table.columns) == 6:
            table_shape = shape
            break

    if not table_shape:
        return

    table_shape.top = 475000
    tbl = table_shape.table

    # Column widths: 0.77in, 2.42in, 1.38in, 1.87in, 1.76in, 1.86in
    column_widths = [700000, 2200000, 1250000, 1700000, 1600000, 1694025]
    for i, w in enumerate(column_widths):
        tbl.columns[i].width = w

    num_locs = len(weather_data)
    if num_locs <= 5:
        row_height = Inches(0.48)
        font_size = 12.0
        header_height = Inches(0.38)
    elif num_locs <= 7:
        row_height = Inches(0.42)
        font_size = 11.0
        header_height = Inches(0.36)
    else:
        row_height = Inches(0.36)
        font_size = 10.0
        header_height = Inches(0.34)

    # Format header row (Row 0)
    tbl.rows[0].height = header_height
    for c_idx in range(6):
        cell = tbl.cell(0, c_idx)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = Inches(0.04)
        cell.margin_right = Inches(0.04)
        cell.margin_top = Inches(0.02)
        cell.margin_bottom = Inches(0.02)
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.name = "Arial"
                r.font.size = Pt(13)
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
        row.height = row_height

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
        r0.font.size = Pt(font_size)
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
        r1.font.size = Pt(font_size + 0.5)
        r1.font.bold = True

        # Col 2: Wx App (Windy on line 1, Accuwx on line 2)
        _fill_2line_cell(row.cells[2], "Windy", "Accuwx", font_size_pt=font_size)

        # Col 3: Forecast Rain mm/%
        w_rain = _fmt_num(item.get("windy_rain"), "mm")
        if item.get("accu_rain_prob") is not None:
            a_rain = f"{int(item['accu_rain_prob'])}%"
        elif item.get("accu_rain_mm") is not None:
            a_rain = f"{item['accu_rain_mm']}mm"
        else:
            a_rain = "N/A"
        _fill_2line_cell(row.cells[3], w_rain, a_rain, font_size_pt=font_size)

        # Col 4: Cloud cover %
        w_cloud = _fmt_num(item.get("windy_cloud"), "%")
        a_cloud = _fmt_num(item.get("accu_cloud"), "%")
        _fill_2line_cell(row.cells[4], w_cloud, a_cloud, font_size_pt=font_size)

        # Col 5: Remarks (Color-coded)
        w_rmk = item.get("windy_remark", "DATA UNAVAILABLE")
        a_rmk = item.get("accu_remark", "DATA UNAVAILABLE")
        _fill_remarks_cell(row.cells[5], w_rmk, a_rmk, font_size_pt=font_size)

    # Safely position Legend table below main table
    legend_shape = None
    for shape in slide.shapes:
        if shape.has_table and len(shape.table.columns) == 3:
            legend_shape = shape
            break

    if legend_shape:
        legend_shape.height = 880000
        tbl_bottom = table_shape.top + header_height + (num_locs * row_height)
        legend_shape.left = 4906737
        # Ensure comfortable spacing below table, bounded by slide height
        legend_shape.top = max(tbl_bottom + Inches(0.15), 4150000)
        max_top = 5143500 - 880000 - Inches(0.08)
        legend_shape.top = min(legend_shape.top, max_top)

HORIZONTAL_LEGEND_PATH = os.path.join(os.path.dirname(__file__), "template", "imd_legend_horizontal.png")

def _add_imd_legend(slide):
    """Adds the exact horizontal IMD warning legend (Green No Warning, Yellow Watch, Orange Alert, Red Warning) as requested."""
    if os.path.exists(HORIZONTAL_LEGEND_PATH):
        slide.shapes.add_picture(
            HORIZONTAL_LEGEND_PATH,
            Inches(1.4), Inches(5.12),
            width=Inches(7.2), height=Inches(0.32)
        )
    else:
        # Vector fallback if image missing
        tbl_shape = slide.shapes.add_table(1, 4, Inches(1.5), Inches(5.12), Inches(7.0), Inches(0.32))
        tbl = tbl_shape.table
        items = [
            ("■ No Warning", RGBColor(0x00, 0x90, 0x00)),
            ("■ Watch", RGBColor(0xE6, 0xB8, 0x00)),
            ("■ Alert", RGBColor(0xF9, 0x73, 0x16)),
            ("■ Warning", RGBColor(0xDC, 0x26, 0x26))
        ]
        for col_idx, (text, col) in enumerate(items):
            cell = tbl.cell(0, col_idx)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.text = text
            p.font.name = "Arial"
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = col
            p.alignment = PP_ALIGN.CENTER

def _create_imd_slide(prs, template_imd_slide, template_table_slide, state_name: str, date_str: str, image_path: Optional[str], available: bool):
    """Creates an IMD slide for a specific state and date with full-size centered map, red header banner, and horizontal legend."""
    blank_layout = prs.slide_layouts[10] if len(prs.slide_layouts) > 10 else template_imd_slide.slide_layout
    new_slide = prs.slides.add_slide(blank_layout)

    # ── 1. Red header banner — built fresh as a filled rectangle (NOT copied from template)
    # Copying the template text-box brings <a:spAutoFit/> which auto-expands the box
    # and makes the IMD title overflow the slide. A fresh RECTANGLE has noAutofit by default.
    BANNER_H = Inches(0.54)
    banner = new_slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(10.0), BANNER_H)
    banner.fill.solid()
    banner.fill.fore_color.rgb = RGBColor(0xFF, 0x00, 0x00)
    banner.line.fill.background()  # no border

    btf = banner.text_frame
    btf.word_wrap = False
    btf.vertical_anchor = MSO_ANCHOR.MIDDLE

    title_text = f"IMD DISTRICT-WISE WARNING — {state_name.upper()} ({date_str})"
    bp = btf.paragraphs[0]
    bp.text = ""
    bp.alignment = PP_ALIGN.CENTER
    br = bp.add_run()
    br.text = title_text
    br.font.name = "Arial"
    br.font.size = Pt(15)
    br.font.bold = True
    br.font.underline = True
    br.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    # ── 2. Map image (starts just below banner)
    MAP_TOP = Inches(0.60)
    if available and image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 5000:
        new_slide.shapes.add_picture(
            image_path,
            Inches(1.25), MAP_TOP,
            width=Inches(7.5), height=Inches(4.35)
        )
    else:
        # Message box when IMD data exceeds horizon or unavailable
        box = new_slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(1.25), MAP_TOP, Inches(7.5), Inches(4.35)
        )
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0xFE, 0xF2, 0xF2)
        box.line.color.rgb = RGBColor(0xFE, 0xCA, 0xCA)
        btf = box.text_frame
        btf.vertical_anchor = MSO_ANCHOR.MIDDLE
        btf.word_wrap = True
        bp0 = btf.paragraphs[0]
        bp0.text = "DATA NOT AVAILABLE ON IMD WEBSITE"
        bp0.font.name = "Arial"
        bp0.font.size = Pt(22)
        bp0.font.bold = True
        bp0.font.color.rgb = RGBColor(0xDC, 0x26, 0x26)
        bp0.alignment = PP_ALIGN.CENTER

        bp1 = btf.add_paragraph()
        bp1.text = "(Exceeds 6-day forecast horizon)\nIMD provides daily district-wise GIS warnings up to 6 days ahead."
        bp1.font.name = "Arial"
        bp1.font.size = Pt(13)
        bp1.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
        bp1.alignment = PP_ALIGN.CENTER
        bp1.space_before = Pt(12)

    # ── 3. Horizontal IMD Legend below the map
    _add_imd_legend(new_slide)


def generate_ppt(date_str_or_job: Any, weather_data: Optional[list] = None, imd_image: Optional[str] = None) -> str:
    """
    Main PPT generation entry point.
    Supports both unified job_result dict and legacy (date_str, weather_data, imd_image).
    """
    if isinstance(date_str_or_job, str):
        job_result = {
            "date_range_str": date_str_or_job,
            "dates": [
                {
                    "date_str": date_str_or_job,
                    "weather_data": weather_data or [],
                    "imd_maps": [
                        {"state": "Maharashtra", "image_path": imd_image, "available": bool(imd_image)}
                    ]
                }
            ],
            "locations": [x.get("location", "").upper() for x in (weather_data or [])]
        }
    else:
        job_result = date_str_or_job

    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")

    prs = pptx.Presentation(TEMPLATE_PATH)
    date_range_str = job_result.get("date_range_str", "")
    dates_list = job_result.get("dates", [])
    loc_names = job_result.get("locations", [])

    # Template has 3 slides:
    # Slide 0: Title
    # Slide 1: Weather Table
    # Slide 2: IMD Map
    template_title_slide = prs.slides[0]
    template_table_slide = prs.slides[1]
    template_imd_slide = prs.slides[2]

    # 1. Update Title Slide (Slide 0)
    title_label = f"WX UPDATE {date_range_str}" if date_range_str else "WX UPDATE"
    _update_slide_title(template_title_slide, title_label)

    # Remove any location text from Title Slide (Slide 0) as requested
    for shp in list(template_title_slide.shapes):
        if shp.has_text_frame and "locations" in shp.text_frame.text.lower():
            try:
                sp_elem = shp._element
                sp_elem.getparent().remove(sp_elem)
            except Exception:
                pass

    # 2. Weather Table Slides
    # For each date, create/populate table slide
    table_slides = []
    for d_idx, day_info in enumerate(dates_list):
        d_str = day_info["date_str"]
        w_data = day_info["weather_data"]

        if d_idx == 0:
            # Use template_table_slide for first date
            curr_slide = template_table_slide
        else:
            # Duplicate template_table_slide for subsequent dates
            slide_layout = template_table_slide.slide_layout
            curr_slide = prs.slides.add_slide(slide_layout)
            for shp in template_table_slide.shapes:
                curr_slide.shapes._spTree.append(copy.deepcopy(shp._element))

        _update_slide_title(curr_slide, f"WX UPDATE {d_str}")
        _populate_table(curr_slide, w_data)
        table_slides.append(curr_slide)

    # 3. IMD Map Slides
    # Remove original template_imd_slide at the end, replacing with dynamic ones
    created_imd_slides = []
    for day_info in dates_list:
        d_str = day_info["date_str"]
        for imd_item in day_info.get("imd_maps", []):
            st_name = imd_item["state"]
            img_p = imd_item.get("image_path")
            is_avail = imd_item.get("available", True)
            _create_imd_slide(prs, template_imd_slide, template_table_slide, st_name, d_str, img_p, is_avail)

    # Now remove template_imd_slide (slide index 2 in original) so there are no orphan template slides
    slide_id_list = prs.slides._sldIdLst
    rId = slide_id_list[2].rId
    prs.part.drop_rel(rId)
    del slide_id_list[2]

    out_name = f"WX UPDATE {date_range_str}.pptx".replace(" - ", "_TO_").replace(" ", "_")
    out_path = os.path.join(OUTPUT_DIR, out_name)
    prs.save(out_path)
    return out_path

