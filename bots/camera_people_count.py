"""
Скрипт: захватить кадры со всех камер и посчитать уникальных людей в офисе.

Логика:
1. Захватываем кадры со всех камер параллельно через ffmpeg.
2. Запускаем YOLO на каждом кадре для детекции людей.
3. Сохраняем кадры с bbox'ами и структурированные отчёты.
4. Если обнаружены люди — отправляем сообщение с фото в Nextcloud Talk.

Запуск: PYTHONPATH=. python bots/camera_people_count.py
"""

import asyncio
import io
import json
import os
import sys
from datetime import datetime

import cv2
from PIL import Image, ImageDraw
import requests
from requests.auth import HTTPBasicAuth
from ultralytics import YOLO

from config import RTSP_BASE, NEXTCLOUD_URL, NEXTCLOUD_API_USER, NEXTCLOUD_API_PASSWORD
from bots.camera import CAMERAS, build_rtsp_url, capture_rtsp_frame

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

# YOLO
YOLO_CONFIDENCE = 0.3

# Сжатие
MAX_WIDTH = 1280
QUALITY = 90

# Nextcloud Talk
NEXTCLOUD_ROOM_TOKEN = "wsyrbhp7"


# ---------------------------------------------------------------------------
# Захват и сжатие кадров
# ---------------------------------------------------------------------------

def compress_jpeg(jpeg_bytes):
    """Сжать JPEG: масштабировать и пересохранить."""
    img = Image.open(io.BytesIO(jpeg_bytes))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    ratio = MAX_WIDTH / img.width
    new_height = int(img.height * ratio)
    img = img.resize((MAX_WIDTH, new_height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALITY, optimize=True)
    return buf.getvalue()


async def capture_all_cameras():
    """Параллельно захватить кадры со всех камер.

    Возвращает {camera_key: {"label": ..., "emoji": ..., "jpeg": bytes, "ts": str}}
    или {"error": str} при ошибке.
    """
    tasks = {}
    for key, camera in CAMERAS.items():
        rtsp_url = build_rtsp_url(camera["channel"])
        tasks[key] = asyncio.create_task(_capture_one(key, rtsp_url, camera))

    results = {}
    for key, task in tasks.items():
        try:
            results[key] = await task
        except Exception as e:
            results[key] = {"error": str(e)}
    return results


async def _capture_one(key, rtsp_url, camera):
    try:
        jpeg_data = capture_rtsp_frame(rtsp_url, timeout=10)
        compressed = compress_jpeg(jpeg_data)
        return {
            "label": camera["label"],
            "emoji": camera["emoji"],
            "jpeg": compressed,
            "ts": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# YOLO-детекция людей
# ---------------------------------------------------------------------------

def detect_people(jpeg_bytes, model):
    """Запустить YOLO на кадре и вернуть количество людей + bbox'и.

    Args:
        jpeg_bytes: JPEG-байты кадра.
        model: загруженная YOLO-модель.

    Returns:
        dict с ключами:
          count (int): число людей
          bboxes (list[list[int]]): [[x1,y1,x2,y2], ...] в пикселях исходного размера
          confidences (list[float]): уверенности детекции
    """
    import numpy as np
    # YOLO принимает numpy-матрицу (BGR) — декодируем JPEG через cv2
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    results = model(img, conf=YOLO_CONFIDENCE, verbose=False)

    bboxes = []
    confidences = []
    for box in results[0].boxes:
        cls_id = int(box.cls.item())
        conf = float(box.conf.item())
        # COCO class 0 = person
        if cls_id == 0:
            xyxy = box.xyxy[0].cpu().numpy().astype(int)
            bboxes.append(xyxy.tolist())
            confidences.append(conf)

    return {
        "count": len(bboxes),
        "bboxes": bboxes,
        "confidences": confidences,
    }


def draw_bboxes(image_bytes, bboxes, color='red', width=2):
    """
    Рисует bounding boxes на изображении.

    Args:
        image_bytes: изображение в формате bytes
        bboxes: список bbox'ов в формате [[x1,y1,x2,y2], ...]
        color: цвет рамки
        width: толщина линии

    Returns:
        bytes: изображение с нарисованными bbox'ами
    """
    if len(bboxes) == 0:
        return image_bytes

    image = Image.open(io.BytesIO(image_bytes))
    draw = ImageDraw.Draw(image)

    for bbox in bboxes:
        x1, y1, x2, y2 = bbox
        draw.rectangle([x1, y1, x2, y2], outline=color, width=width)

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format=image.format or 'JPEG')
    return img_byte_arr.getvalue()


# ---------------------------------------------------------------------------
# Сохранение изображений камер
# ---------------------------------------------------------------------------

def save_camera_images(frames, detection_results, snap_key):
    """Сохранить сжатые кадры (с bbox'ами) в data/office/images/."""
    images_dir = "data/office/images"
    os.makedirs(images_dir, exist_ok=True)

    saved = []
    for key in sorted(frames.keys()):
        frame = frames[key]
        if "error" in frame:
            continue
        filename = f"{snap_key}_{key}.jpg"
        filepath = os.path.join(images_dir, filename)

        # Рисуем bbox'и если есть детекция
        jpeg_out = frame["jpeg"]
        det = detection_results.get(key, {})
        if det.get("count", 0) > 0:
            jpeg_out = draw_bboxes(
                frame["jpeg"],
                det["bboxes"],
                color='red',
                width=2,
            )

        with open(filepath, "wb") as f:
            f.write(jpeg_out)
        saved.append((key, frame.get("label", key), frame.get("emoji", ""), len(frame["jpeg"])))
    return saved


# ---------------------------------------------------------------------------
# Отправка в Nextcloud Talk (от имени mountian_admin)
# ---------------------------------------------------------------------------

def send_text_message(room_token, message):
    """Отправить текстовое сообщение в чат Talk от имени mountian_admin."""
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{room_token}"
    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {"message": message}
    try:
        resp = requests.post(url, json=payload, headers=headers,
                             auth=HTTPBasicAuth(NEXTCLOUD_API_USER, NEXTCLOUD_API_PASSWORD),
                             timeout=30)
        resp.raise_for_status()
        print(f"  ✅ Текстовое сообщение отправлено от {NEXTCLOUD_API_USER}")
    except Exception as e:
        print(f"  ❌ Ошибка отправки текста: {e}")


def upload_and_share_image(room_token, jpeg_data, caption):
    """Загрузить JPEG на WebDAV mountian_admin и шарить в чат."""
    from nextcloud.nextcloudapi import NextcloudClient

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"office_people_{timestamp}.jpg"
    upload_dir = "camera_screenshots"

    nc_client = NextcloudClient(
        NEXTCLOUD_URL,
        NEXTCLOUD_API_USER,
        NEXTCLOUD_API_PASSWORD,
    )

    try:
        nc_client.create_directory_recursive(upload_dir)
    except Exception:
        pass

    remote_path = f"{upload_dir}/{filename}"
    url = f"{nc_client.webdav_base_url}/{remote_path}"

    resp = requests.put(url, data=jpeg_data, auth=HTTPBasicAuth(
        NEXTCLOUD_API_USER, NEXTCLOUD_API_PASSWORD
    ))
    if resp.status_code not in (201, 204):
        print(f"  ❌ WebDAV PUT failed: {resp.status_code}")
        return

    nc_client.share_file_to_chat(
        room_token=room_token,
        file_path=remote_path,
        caption=caption,
        silent=False,
    )
    print(f"  ✅ Изображение отправлено от {NEXTCLOUD_API_USER}: {caption}")


# ---------------------------------------------------------------------------
# Основной flow
# ---------------------------------------------------------------------------

async def main():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%H%M%S")
    snap_key = f"{date_str}_{ts_str}"

    print(f"[{now.isoformat()}] Захват кадров со всех камер...")

    # 1. Захват
    frames = await capture_all_cameras()

    # Показываем статус захвата
    success_count = 0
    for key, frame in sorted(frames.items()):
        if "error" in frame:
            print(f"  ERR {frame.get('emoji', '')} {frame.get('label', '')} ({key}): {frame['error']}")
        else:
            success_count += 1
            print(f"  OK   {frame.get('emoji', '')} {frame.get('label', '')} ({key}) — {len(frame['jpeg'])} байт (сжато)")

    if success_count == 0:
        print("Не удалось захватить ни одного кадра.")
        sys.exit(1)

    # 2. YOLO-детекция на каждом кадре
    print(f"\n[{now.isoformat()}] YOLO-детекция людей...")
    detection_results = {}
    for key in sorted(frames.keys()):
        frame = frames[key]
        if "error" in frame:
            detection_results[key] = {"error": frame["error"]}
            continue
        try:
            result = detect_people(frame["jpeg"], model)
            detection_results[key] = result
            emoji = frame.get("emoji", "")
            label = frame.get("label", key)
            print(f"  {emoji} {label}: {result['count']} чел. (conf: {[f'{c:.2f}' for c in result['confidences']]})")
        except Exception as e:
            detection_results[key] = {"error": str(e)}
            print(f"  ERR {key}: {e}")

    # 3. Сохраняем изображения с bbox'ами
    save_camera_images(frames, detection_results, snap_key)
    print(f"\n[{now.isoformat()}] Изображения сохранены в data/office/images/")

    # 4. Формируем отчёт
    per_camera_counts = {}
    total_people = 0
    for key in sorted(frames.keys()):
        det = detection_results.get(key, {})
        count = det.get("count", 0) if isinstance(det, dict) and "error" not in det else 0
        per_camera_counts[key] = {"count": count}
        total_people += count

    print(f"\n{'=' * 50}")
    print(f"  ЛЮДЕЙ В ОФИСЕ: {total_people}")
    print(f"{'=' * 50}")
    print("\nПо камерам:")
    for key in sorted(per_camera_counts.keys()):
        frame = frames.get(key, {})
        info = per_camera_counts.get(key, {})
        count = info.get("count", 0) if isinstance(info, dict) else 0
        emoji = frame.get("emoji", "")
        label = frame.get("label", key)
        print(f"  {emoji} {label}: {count}")

    # 5. Сохраняем результат в data/office/
    os.makedirs("data/office", exist_ok=True)

    summary = {
        "timestamp": now.isoformat(),
        "total_unique": total_people,
        "per_camera": {k: v.get("count", 0) for k, v in per_camera_counts.items()},
    }
    summary_path = "data/office/latest.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    archive_path = f"data/office/{snap_key}.json"
    archive = dict(summary)
    archive["per_camera_full"] = detection_results
    with open(archive_path, "w") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)

    csv_path = "data/office/history.csv"
    csv_header = "timestamp,total_unique,camera_key,count"
    csv_lines = []
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            existing = f.read()
        if not existing.endswith("\n"):
            existing += "\n"
        csv_lines.append(existing)
    else:
        csv_lines.append(csv_header + "\n")

    for key in sorted(per_camera_counts.keys()):
        info = per_camera_counts[key]
        count = info.get("count", 0)
        csv_lines.append(f"{now.isoformat()},{total_people},{key},{count}\n")

    with open(csv_path, "w") as f:
        f.writelines(csv_lines)

    print(f"\nОтчёты сохранены:")
    print(f"  Сводка:  {summary_path}")
    print(f"  Снимок:  {archive_path}")
    print(f"  История: {csv_path}")
    print(f"  Фото:    data/office/images/{snap_key}_*.jpg")

    # 6. Уведомление
    if total_people > 0:
        print(f"\n[{now.isoformat()}] Обнаружены люди — отправляем уведомление в Nextcloud Talk...")

        camera_lines = []
        for key in sorted(per_camera_counts.keys()):
            info = per_camera_counts[key]
            count = info.get("count", 0)
            if count > 0:
                frame = frames.get(key, {})
                label = frame.get("label", key)
                emoji = frame.get("emoji", "")
                camera_lines.append(f"  {emoji} {label}: {count}")

        text_msg = (
            f"👥 В офисе обнаружено людей: {total_people}\n\n"
            f"По камерам:\n" + "\n".join(camera_lines) + "\n\n"
            f"📸 Фото с камер: data/office/images/{snap_key}_*.jpg"
        )
        send_text_message(NEXTCLOUD_ROOM_TOKEN, text_msg)

        for key in sorted(per_camera_counts.keys()):
            info = per_camera_counts[key]
            count = info.get("count", 0)
            if count > 0 and key in frames and "error" not in frames[key]:
                frame = frames[key]
                label = frame.get("label", key)
                emoji = frame.get("emoji", "")
                img_caption = f"{emoji} {label}: {count} чел. — {now.strftime('%H:%M')}"
                # Рисуем bbox'и перед отправкой
                img_with_bboxes = draw_bboxes(
                    frame["jpeg"],
                    detection_results[key]["bboxes"],
                    color='red',
                    width=2,
                )
                upload_and_share_image(NEXTCLOUD_ROOM_TOKEN, img_with_bboxes, img_caption)
    else:
        print(f"\n[{now.isoformat()}] Людей не обнаружено — уведомление не отправляем.")

if __name__ == "__main__":
    model = YOLO("yolo26n.pt")
    asyncio.run(main())
