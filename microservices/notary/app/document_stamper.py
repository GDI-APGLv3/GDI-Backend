from datetime import datetime
from typing import Tuple
import functools
import io
import logging
from fontTools.ttLib import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black
from reportlab.lib.units import inch
from pypdf import PdfReader, PdfWriter
from pyhanko.pdf_utils import layout
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader as PyHankoPdfFileReader
from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.stamp import TextStamp, TextStampStyle
from . import config
from .pades_signer import count_pades_signatures

logger = logging.getLogger(__name__)


class StampError(Exception):
    pass


def format_date_spanish() -> str:
    now = datetime.now()
    
    months = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    
    month_name = months[now.month - 1]
    formatted_date = f"{now.day} de {month_name} de {now.year}"
    
    return formatted_date


def create_stamp_text(document_number: str, city: str = "Bogotá") -> Tuple[str, str]:
    document_line = document_number
    city_date_line = f"{city}, {format_date_spanish()}"
    
    return document_line, city_date_line


def create_stamp_overlay(document_number: str, city: str) -> bytes:
    buffer = io.BytesIO()
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(buffer, pagesize=A4)

    DOC_NUMBER_X = config.STAMP_DOC_NUMBER_X
    DOC_NUMBER_Y = config.STAMP_DOC_NUMBER_Y
    CITY_DATE_X = config.STAMP_CITY_DATE_X
    CITY_DATE_Y = config.STAMP_CITY_DATE_Y

    document_line, city_date_line = create_stamp_text(document_number, city)
    
    c.setFont("Helvetica", 11)
    
    c.setFillColor(black)
    c.drawString(DOC_NUMBER_X, DOC_NUMBER_Y, document_line)
    c.drawString(CITY_DATE_X, CITY_DATE_Y, city_date_line)
    
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def stamp_document(
    pdf_content: bytes,
    document_number: str,
    city: str = "Bogotá",
    page_position: str = "first"
) -> bytes:
    try:
        existing_signatures = count_pades_signatures(pdf_content)
        if existing_signatures > 0:
            logger.info(
                f"stamp_document ruteo=incremental firmas={existing_signatures} "
                f"page_position={page_position}"
            )
            return _stamp_document_incremental(
                pdf_content, document_number, city, page_position
            )
        logger.info(
            f"stamp_document ruteo=legacy_pypdf firmas={existing_signatures} "
            f"page_position={page_position}"
        )
        return _stamp_document_pypdf(pdf_content, document_number, city, page_position)

    except StampError:
        raise
    except Exception as e:
        raise StampError(f"Error applying stamp: {str(e)}")


def _stamp_document_pypdf(
    pdf_content: bytes,
    document_number: str,
    city: str,
    page_position: str,
) -> bytes:
    try:
        pdf_reader = PdfReader(io.BytesIO(pdf_content))
        pdf_writer = PdfWriter()
        pdf_writer.clone_document_from_reader(pdf_reader)

        stamp_overlay_bytes = create_stamp_overlay(document_number, city)
        stamp_reader = PdfReader(io.BytesIO(stamp_overlay_bytes))
        stamp_page = stamp_reader.pages[0]

        total_pages = len(pdf_writer.pages)

        for i, page in enumerate(pdf_writer.pages):
            if page_position == "last":
                is_target = (i == total_pages - 1)
            else:
                is_target = (i == 0)

            if is_target:
                page.merge_page(stamp_page)

        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        output_buffer.seek(0)

        return output_buffer.getvalue()

    except Exception as e:
        raise StampError(f"Error applying stamp: {str(e)}")


@functools.lru_cache(maxsize=4)
def _get_font_descender_ratio(font_path: str) -> float:
    try:
        tt = TTFont(font_path)
        descender = tt['hhea'].descender
        units_per_em = tt['head'].unitsPerEm
        return descender / units_per_em
    except Exception as e:
        logger.warning(
            f"notary.font_descender_read_failed: {type(e).__name__}: {e} "
            f"font_path={font_path} — usando fallback -0.25"
        )
        return -0.25


def _stamp_document_incremental(
    pdf_content: bytes,
    document_number: str,
    city: str,
    page_position: str,
) -> bytes:
    try:
        pyhanko_reader = PyHankoPdfFileReader(io.BytesIO(pdf_content))
        page_count = pyhanko_reader.root['/Pages'].get_object()['/Count']
        target_page = (page_count - 1) if page_position == "last" else 0

        pdf_writer = IncrementalPdfFileWriter(io.BytesIO(pdf_content))

        document_line, city_date_line = create_stamp_text(document_number, city)
        stamp_text = f"{city_date_line}\n{document_line}"

        font_factory = GlyphAccumulatorFactory(
            font_file=str(config.STAMP_INCREMENTAL_FONT_PATH),
            font_size=config.FONT_SIZE_STAMP,
        )

        leading = config.STAMP_CITY_DATE_Y - config.STAMP_DOC_NUMBER_Y
        box_height = leading + config.FONT_SIZE_STAMP
        box_width = 250

        text_box_style = TextBoxStyle(
            font=font_factory,
            font_size=config.FONT_SIZE_STAMP,
            leading=leading,
            border_width=0,
        )

        inner_layout = layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MIN,
            y_align=layout.AxisAlignment.ALIGN_MAX,
            margins=layout.Margins.uniform(0),
        )

        stamp_style = TextStampStyle(
            stamp_text="%(stamp_text)s",
            text_box_style=text_box_style,
            border_width=0,
            background_opacity=0.0,
            inner_content_layout=inner_layout,
        )

        stamp = TextStamp(
            writer=pdf_writer,
            style=stamp_style,
            text_params={"stamp_text": stamp_text},
            box=layout.BoxConstraints(width=box_width, height=box_height),
        )

        descender_ratio = _get_font_descender_ratio(str(config.STAMP_INCREMENTAL_FONT_PATH))
        y_bottom = config.STAMP_DOC_NUMBER_Y + (descender_ratio * config.FONT_SIZE_STAMP)
        stamp.apply(target_page, config.STAMP_DOC_NUMBER_X, y_bottom)

        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        output_buffer.seek(0)

        return output_buffer.getvalue()

    except Exception as e:
        raise StampError(f"Error applying incremental stamp: {str(e)}")


def validate_stamp_area(x: float, y: float, width: float, height: float) -> bool:
    if x < 0 or y < 0:
        return False
    
    if x + width > config.LETTER_WIDTH or y + height > config.LETTER_HEIGHT:
        return False
    
    return True