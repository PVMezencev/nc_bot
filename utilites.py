import shutil
import zipfile
import logging
from datetime import datetime
import base64
from io import BytesIO

from PIL import Image, ImageOps
from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)


def unzip_archive(zip_file_path, to_dir):
    remove = True
    with zipfile.ZipFile(zip_file_path, "r") as zf:
        crc_test = zf.testzip()
        if crc_test is None:
            remove = False
        else:
            print(f"{datetime.now().isoformat()}: CRC or file headers: {crc_test}")
    if remove:
        shutil.rmtree(zip_file_path)
        return "битый архив"

    with zipfile.ZipFile(zip_file_path, mode='r', strict_timestamps=False) as zip_ref:
        zip_ref.extractall(to_dir)


def _encode_jpeg_base64(img: Image.Image) -> str:
    """Кодирует PIL-изображение в base64 JPEG."""
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=88, optimize=True, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def pdf_to_jpg_base64(pdf_bytes) -> list:
    """
    Конвертирует PDF в список JPEG (base64) — по одному на страницу.
    Без масштабирования — сохраняем исходное разрешение для точного распознавания.
    Поворот страниц НЕ делается здесь (tesseract OSD не работает на сканах) —
    ориентацию определяет LLM/OCR-сервис в пайплайне LlamaClient.
    Возвращает List[str] (список base64-строк).
    """
    images = convert_from_bytes(pdf_bytes, dpi=200, grayscale=True, poppler_path='/usr/bin')

    result = []
    for img in images:
        img = ImageOps.exif_transpose(img)
        result.append(_encode_jpeg_base64(img))

    return result


def pdf_to_jpg_base64_iter(pdf_bytes, dpi=200):
    images = convert_from_bytes(pdf_bytes, dpi=dpi, grayscale=True, poppler_path='/usr/bin')
    for img in images:
        img = ImageOps.exif_transpose(img)
        yield _encode_jpeg_base64(img)


def image_to_jpg_base64(image_bytes: bytes) -> str:
    """
    Конвертирует изображение (bytes) в JPEG base64.
    Поворот НЕ делается здесь — ориентацию определяет LLM/OCR-сервис.
    Возвращает base64-строку, или '' при ошибке.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        # Конвертируем RGBA/P в RGB для корректного сохранения в JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = ImageOps.exif_transpose(img)
        return _encode_jpeg_base64(img)
    except Exception as e:
        logger.warning(f"[image] Не удалось обработать изображение: {e}")
        return ""
