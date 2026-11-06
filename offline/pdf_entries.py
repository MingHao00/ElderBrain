"""英文 PDF 条目的公共字段。页码仍只在法术清单里补。"""

from html_entries import CatalogEntry, make_catalog_entry

LANG_EN = "en"
NAMED_FROM_HEADING = "named_from_heading"


def english_entry(
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    entry_type: str,
    english_name: str,
    pdf_name: str,
    line_number: int,
    text: str,
    align_status: str = NAMED_FROM_HEADING,
) -> CatalogEntry:
    """一条英文规则。位置先记行号，法术页码另算。"""
    return make_catalog_entry(
        version_token=version_token,
        rule_version=rule_version,
        source_id=source_id,
        source_pin=source_pin,
        lang=LANG_EN,
        entry_type=entry_type,
        english_name=english_name,
        title_zh="",
        location=f"{pdf_name}#line:{line_number}",
        text=text,
        align_status=align_status,
    )
