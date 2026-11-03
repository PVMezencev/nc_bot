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


def pdf_to_jpg_base64(pdf_bytes):
    """Конвертирует PDF в единое JPEG (base64). Ограничивает размер, чтобы не получить 413."""
    MAX_HEIGHT = 8000  # пикселей — ограничение по высоте
    MAX_WIDTH = 2500   # пикселей — ограничение по ширине

    # Конвертируем PDF страницы в список PIL Image
    images = convert_from_bytes(pdf_bytes, poppler_path='/usr/bin')
    if not images:
        return base64.b64encode(b'').decode('utf-8')

    total_height = sum(img.height for img in images)
    max_width = max(img.width for img in images)

    # Если изображение слишком большое — масштабируем
    scale = 1.0
    if max_width > MAX_WIDTH:
        scale = MAX_WIDTH / max_width
    if total_height * scale > MAX_HEIGHT:
        scale = min(scale, MAX_HEIGHT / total_height)

    new_w = int(max_width * scale)
    new_h = int(total_height * scale)

    # Создаем единое вертикальное изображение
    combined_image = Image.new("RGB", (new_w, new_h), color="white")

    # Вставляем все страницы вертикально (с масштабированием)
    y_offset = 0
    for img in images:
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        combined_image.paste(img, (0, y_offset))
        y_offset += img.height

    # Конвертируем в JPG и получаем байты
    buffer = BytesIO()
    combined_image.save(buffer, format="JPEG", quality=75, optimize=True)
    image_bytes = buffer.getvalue()

    # Кодируем в base64
    return base64.b64encode(image_bytes).decode("utf-8")

