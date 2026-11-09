import shutil
import zipfile
import logging
from datetime import datetime
import base64
from io import BytesIO

import cv2
import numpy as np
from PIL import Image
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


def _get_rotation_angle(img: Image.Image) -> float:
    """
    Определить угол поворота страницы через Tesseract OSD.

    Возвращает угол в градусах (0, 90, 180, 270), на который нужно повернуть
    изображение против часовой стрелки, чтобы текст стал читаемым.
    Возвращает 0.0 если OSD не смог определить ориентацию.
    """
    try:
        import pytesseract

        # OSD работает на ч/б изображении
        np_arr = np.array(img.convert("L"))
        _, thresh = cv2.threshold(np_arr, 127, 255, cv2.THRESH_BINARY)

        img_info = pytesseract.image_to_osd(thresh)

        for line in img_info.split('\n'):
            if ':' not in line:
                continue
            key, _, val = line.partition(':')
            if key.strip().lower() == 'image rotation':
                return float(val.strip())
    except Exception as e:
        logger.debug(f"[rotation] OSD не определил ориентацию: {e}")

    return 0.0


def _text_quality(img: Image.Image) -> int:
    """
    Быстрая оценка качества текста на изображении через tesseract.
    Возвращает количество распознанных букв (больше = лучше).
    """
    try:
        import pytesseract
        np_arr = np.array(img.convert("L"))
        _, thresh = cv2.threshold(np_arr, 127, 255, cv2.THRESH_BINARY)
        text = pytesseract.image_to_string(thresh)
        return sum(1 for ch in text if ch.isalpha())
    except Exception:
        return 0


def auto_rotate_image(img: Image.Image) -> Image.Image:
    """
    Автоматически повернуть изображение в правильное положение.

    1. Tesseract OSD для определения ориентации (90/180/270).
    2. Если OSD вернул 0 — проверяем качество OCR; если текст плохо
       распознаётся, пробуем поворот на 180° и сравниваем качество.
    """
    angle = _get_rotation_angle(img)
    if angle != 0:
        logger.info(f"[rotation] Поворот на {angle}° (OSD)")
        return img.rotate(-angle, expand=True)

    # OSD не определил поворот — проверяем, не перевернута ли страница на 180°.
    original_quality = _text_quality(img)
    rotated_180 = img.rotate(180, expand=True)
    rotated_quality = _text_quality(rotated_180)

    if rotated_quality > original_quality * 1.5:
        logger.info(f"[rotation] Поворот на 180° (качество: {original_quality} → {rotated_quality})")
        return rotated_180

    return img


def _encode_jpeg_base64(img: Image.Image) -> str:
    """Кодирует PIL-изображение в base64 JPEG."""
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=95, optimize=False)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def pdf_to_jpg_base64(pdf_bytes) -> list:
    """
    Конвертирует PDF в список JPEG (base64) — по одному на страницу.
    Без масштабирования — сохраняем исходное разрешение для точного распознавания.
    Каждая страница автоматически поворачивается в правильное положение.
    Возвращает List[str] (список base64-строк).
    """
    images = convert_from_bytes(pdf_bytes, poppler_path='/usr/bin')
    if not images:
        return []

    result = []
    for img in images:
        img = auto_rotate_image(img)
        result.append(_encode_jpeg_base64(img))

    return result


def image_to_jpg_base64(image_bytes: bytes) -> str:
    """
    Конвертирует изображение (bytes) в JPEG base64 с автоповоротом.
    Возвращает base64-строку, или '' при ошибке.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        # Конвертируем RGBA/P в RGB для корректного сохранения в JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = auto_rotate_image(img)
        return _encode_jpeg_base64(img)
    except Exception as e:
        logger.warning(f"[rotation] Не удалось обработать изображение: {e}")
        return ""

