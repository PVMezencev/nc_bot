import shutil
import zipfile
from datetime import datetime
import base64
from io import BytesIO
from PIL import Image
from pdf2image import convert_from_bytes


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


def pdf_to_jpg_base64(pdf_bytes) -> list:
    """
    Конвертирует PDF в список JPEG (base64) — по одному на страницу.
    Каждая страница ограничена MAX_WIDTH × MAX_HEIGHT, чтобы не получить 413.
    Возвращает List[str] (список base64-строк).
    """
    MAX_DIM = 2000  # пикселей — ограничение по стороне для каждой страницы

    images = convert_from_bytes(pdf_bytes, poppler_path='/usr/bin')
    if not images:
        return []

    result = []
    for img in images:
        # Масштабируем страницу если слишком большая
        scale = 1.0
        if img.width > MAX_DIM or img.height > MAX_DIM:
            scale = min(MAX_DIM / img.width, MAX_DIM / img.height)
            if scale < 1.0:
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)

        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=80, optimize=True)
        result.append(base64.b64encode(buffer.getvalue()).decode("utf-8"))

    return result

