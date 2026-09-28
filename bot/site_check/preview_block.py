"""«Ссылка в мессенджерах» (ТЗ, 5.7): картинка и название превью из <head> своей загрузки, как их берёт мессенджер.

«Плохо» не бывает — превью витрина, не поломка. Картинку, которую не удалось проверить (сеть, срок), находкой не
считаем; «нет картинки» и «нет названия» — только если head дочитан до конца.
"""
from bot.site_check.findings import Block, BlockVerdict, Finding, FindingItem, Grade, graded, not_checked
from bot.site_check.head_tags import HeadTags
from bot.site_check.page_fetch import ImageState, PagePreview

IMAGE_FINDINGS = {ImageState.BROKEN: Finding.PREVIEW_IMAGE_BROKEN, ImageState.NOT_IMAGE: Finding.PREVIEW_IMAGE_BROKEN,
                  ImageState.SVG: Finding.PREVIEW_IMAGE_SVG, ImageState.RELATIVE: Finding.PREVIEW_IMAGE_RELATIVE}


def judge_preview(preview: PagePreview | None) -> BlockVerdict:
    """Главный источник — своя загрузка (ТЗ, 6.1): не удалась — «неизвестно», блок не печатается."""
    if preview is None or preview.head is None:
        return not_checked(Block.PREVIEW)
    return graded(Block.PREVIEW, [*_image_findings(preview), *_title_findings(preview.head)])


def _image_findings(preview: PagePreview) -> list[FindingItem]:
    if preview.image is None:
        return [FindingItem(Finding.NO_PREVIEW_IMAGE, Grade.FIX)] if preview.head.complete else []
    finding = IMAGE_FINDINGS.get(preview.image.state)
    if finding is None:
        return []
    broken = preview.image.state is ImageState.BROKEN and preview.image.status
    return [FindingItem(finding, Grade.FIX, detail=str(preview.image.status) if broken else None)]


def _title_findings(head: HeadTags) -> list[FindingItem]:
    if head.preview_title or not head.complete:
        return []
    return [FindingItem(Finding.NO_PREVIEW_TITLE, Grade.FIX)]
