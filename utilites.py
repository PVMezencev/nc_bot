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
    # Конвертируем PDF страницы в список PIL Image
    images = convert_from_bytes(pdf_bytes, poppler_path='/usr/bin')
    # Вычисляем размеры для итогового изображения
    total_height = sum(img.height for img in images)
    max_width = max(img.width for img in images)

    # Создаем единое вертикальное изображение
    combined_image = Image.new("RGB", (max_width, total_height), color="white")

    # Вставляем все страницы вертикально
    y_offset = 0
    for img in images:
        combined_image.paste(img, (0, y_offset))
        y_offset += img.height

    # Конвертируем в JPG и получаем байты
    buffer = BytesIO()
    combined_image.save(buffer, format="JPEG", quality=85, optimize=True)
    image_bytes = buffer.getvalue()

    # Кодируем в base64
    return base64.b64encode(image_bytes).decode("utf-8")

