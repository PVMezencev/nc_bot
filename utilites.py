import shutil
import zipfile
from datetime import datetime


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
