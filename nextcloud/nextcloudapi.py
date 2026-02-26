import urllib.parse
from datetime import datetime

import requests
from requests.auth import HTTPBasicAuth
import os
import xml.etree.ElementTree as ET


class NextcloudClient:
    def __init__(self, nextcloud_url, username, password):
        self.nextcloud_url = nextcloud_url
        self.username = username
        self.password = password
        self.auth = HTTPBasicAuth(self.username, self.password)
        self.webdav_base_path = f"/remote.php/dav/files/{self.username}"
        self.webdav_base_url = f"{self.nextcloud_url}{self.webdav_base_path}"

    def get_files_recursive(self, directory):
        """1. Получить список файлов в каталоге рекурсивно."""
        url = f"{self.webdav_base_url}/{directory}"
        headers = {
            "Depth": "infinity"
        }
        try:
            response = requests.request("PROPFIND", url, headers=headers, auth=self.auth)
        except Exception as e:
            raise Exception(f"Ошибка: {e}")
        if response.status_code == 207:
            return self.parse_webdav_response(response.text)  # Возвращает XML с информацией о файлах
        else:
            raise Exception(f"Ошибка: {response.status_code}")

    def download_file(self, file_path, local_dir, freeze_path=False, encode_path=False):
        if encode_path:
            file_path = str(urllib.parse.quote(file_path)).lower()
        if freeze_path:
            file_dir = os.path.dirname(file_path)
            if file_dir.startswith('/'):
                file_dir = file_dir[1:]
            local_dir = os.path.join(local_dir, file_dir)
        os.makedirs(local_dir, exist_ok=True)
        file_url = f"{self.webdav_base_url}/{file_path}"

        try:
            response = requests.get(file_url, auth=self.auth)
        except Exception as e:
            raise Exception(f"Ошибка: {e}")
        if response.status_code == 200:
            dst = os.path.basename(file_path)
            dst = urllib.parse.unquote(dst)
            with open(os.path.join(local_dir, dst), "wb") as f:
                f.write(response.content)
        else:
            raise Exception(f"Ошибка скачивания: {response.status_code}")

    def download_files_from_directory(self, directory, local_dir, prefix='', suffix='', freeze_path=False):
        os.makedirs(local_dir, exist_ok=True)
        """2. Скачать файлы из каталога."""
        files = self.get_files_recursive(directory)[1]
        # Парсинг XML и скачивание файлов
        # (здесь нужно реализовать парсинг XML и скачивание каждого файла)
        for f in files:
            if prefix != '':
                if not os.path.basename(f).startswith(prefix):
                    continue
            if suffix != '':
                if not os.path.basename(f).endswith(suffix):
                    continue
            self.download_file(f.replace(self.webdav_base_path, ''), local_dir, freeze_path)

    def move_file(self, source_path, destination_path):
        self.create_directory_recursive(os.path.dirname(destination_path))
        """3. Переместить файл в другой каталог без скачивания."""
        source_url = f"{self.webdav_base_url}/{source_path}"
        destination_url = f"{self.webdav_base_url}/{destination_path}"
        headers = {
            "Destination": destination_url
        }
        try:
            response = requests.request("MOVE", source_url, headers=headers, auth=self.auth)
        except Exception as e:
            raise Exception(f"Ошибка: {e}")
        if response.status_code != 201:
            raise Exception(f"Ошибка перемещения: {response.status_code}")

    def delete_file(self, file_path):
        """4. Удалить файл."""
        url = f"{self.webdav_base_url}/{file_path}"
        try:
            response = requests.delete(url, auth=self.auth)
        except Exception as e:
            raise Exception(f"Ошибка: {e}")
        if response.status_code != 204:
            raise Exception(f"Ошибка удаления: {response.status_code}")

    def delete_directory(self, directory_path):
        """5. Удалить каталог."""
        self.delete_file(directory_path)  # Удаление каталога аналогично удалению файла

    def upload_file(self, local_file_path, remote_directory):
        """6. Загрузить файл в каталог."""
        url = f"{self.webdav_base_url}/{remote_directory}/{os.path.basename(local_file_path)}"
        with open(local_file_path, "rb") as f:
            try:
                response = requests.put(url, data=f, auth=self.auth)
            except Exception as e:
                raise Exception(f"Ошибка: {e}")
        if response.status_code != 201 and response.status_code != 204:
            raise Exception(f"Ошибка загрузки: {response.status_code}")

    def create_directory_recursive(self, directory_path):
        """7. Создать каталоги рекурсивно."""
        parts = directory_path.split("/")
        current_path = ""
        for part in parts:
            current_path += f"{part}/"
            url = f"{self.webdav_base_url}/{current_path}"
            try:
                response = requests.request("MKCOL", url, auth=self.auth)
            except Exception as e:
                raise Exception(f"Ошибка: {e}")
            if response.status_code != 201 and response.status_code != 405:  # 405 если каталог уже существует
                raise Exception(f"Ошибка создания каталога: {response.status_code}")

    def create_file(self, directory, file_name, content):
        """Создать файл в каталоге."""
        url = f"{self.webdav_base_url}/{directory}/{file_name}"
        try:
            response = requests.put(url, data=content, auth=self.auth)
        except Exception as e:
            raise Exception(f"Ошибка: {e}")
        if response.status_code == 201 or response.status_code == 204:
            print(f"Файл {file_name} успешно создан в каталоге {directory}.")
        else:
            raise Exception(f"Ошибка создания файла: {response.status_code}")

    def parse_webdav_response(self, xml_response):
        """Парсит XML-ответ WebDAV и возвращает списки каталогов и файлов."""
        namespaces = {
            'd': 'DAV:',
            's': 'http://sabredav.org/ns',
            'oc': 'http://owncloud.org/ns',
            'nc': 'http://nextcloud.org/ns'
        }

        root = ET.fromstring(xml_response)
        directories = []
        files = []
        directories_info = []
        files_info = []

        for response in root.findall('d:response', namespaces):
            href = response.find('d:href', namespaces).text
            propstat = response.find('d:propstat', namespaces)
            prop = propstat.find('d:prop', namespaces)
            resourcetype = prop.find('d:resourcetype', namespaces)
            getlastmodified = prop.find('d:getlastmodified', namespaces).text
            getlastmodified_prepared = reformat_datetime(getlastmodified)

            # Проверяем, является ли ресурс каталогом
            if resourcetype.find('d:collection', namespaces) is not None:
                directories.append(href)

                quota_used_bytes = prop.find('d:quota-used-bytes', namespaces).text
                size = 0
                try:
                    size = int(quota_used_bytes)
                except:
                    pass
                directories_info.append({
                    'href': href,
                    'lastmodified': getlastmodified_prepared,
                    'size': size,
                })
            else:
                files.append(href)

                quota_used_bytes = prop.find('d:getcontentlength', namespaces).text
                size = 0
                try:
                    size = int(quota_used_bytes)
                except:
                    pass
                files_info.append({
                    'href': href,
                    'lastmodified': getlastmodified_prepared,
                    'size': size,
                })

        return directories, files, directories_info, files_info


def parse_datetime(src: str) -> datetime | None:
    try:
        dt = datetime.strptime(src, "%a, %d %b %Y %H:%M:%S %Z")
        return dt
    except Exception as e:
        return


def reformat_datetime(src: str) -> str:
    dt = parse_datetime(src)
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%d %H:%M:%S')


if __name__ == "__main__":
    api_client = NextcloudClient("https://x70-cloud-002-nobel.fo7.ru", "", "")

    # # api_client.download_files_from_directory("bakeropt/ОБЩАЯ/", "./bakeropt", suffix=".xlsx")
    # api_client.download_file("/bakeropt/ОБЩАЯ/график поставок.xlsx", './bakeropt', encode_path=True)
    # #
    # # # Примеры вызова методов
    # api_client.create_directory_recursive("Documents/new_dir/sub_dir")
    info_dir = api_client.get_files_recursive("bakeropt/ОБЩАЯ/")
    # Вывод результатов
    print("Каталоги:")
    for directory in info_dir[0]:
        print(urllib.parse.unquote(directory))
    #
    print("\nФайлы:")
    for file in info_dir[1]:
        print(urllib.parse.unquote(file))
    # Вывод результатов
    print("Каталоги с информацией:")
    for directory in info_dir[2]:
        print(f"Каталог: {urllib.parse.unquote(directory.get('href'))}")
        print(f"Модифицирован: {directory.get('lastmodified')}")
        print(f"Размер: {directory.get('size')}")
    #
    print("\nФайлы:")
    for file in info_dir[3]:
        print(f"Файл: {urllib.parse.unquote(file.get('href'))}")
        print(f"Модифицирован: {file.get('lastmodified')}")
        print(f"Размер: {file.get('size')}")
    # api_client.create_file("bakeropt/ОБЩАЯ/", "ФАЙЛ.txt", "")
    # api_client.move_file("Documents/file.txt", "Backups/file.txt")
    # api_client.delete_file("Backups/file.txt")
    # api_client.delete_directory("Documents")
    # api_client.upload_file("./requirements.txt", "EEE/new_dir/sub_dir")
