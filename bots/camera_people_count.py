"""
Скрипт: захватить кадры со всех камер и посчитать уникальных людей в офисе.

Логика:
1. Захватываем кадры со всех камер параллельно через ffmpeg.
2. Сжимаем кадры, сохраняем в data/office/images/ и отправляем в llama.cpp vision-модель.
3. Модель для каждой камеры говорит, кого видит.
4. Затем отправляем все кадры вместе для deduplication.
5. Если обнаружены люди — отправляем сообщение с фото в Nextcloud Talk.
6. Сохраняем структурированные отчёты в data/office/.

Запуск: PYTHONPATH=. python bots/camera_people_count.py
"""

import asyncio
import base64
import io
import json
import os
import re
import sys
from datetime import datetime

from PIL import Image
import httpx
import requests
from requests.auth import HTTPBasicAuth

from config import RTSP_BASE, NEXTCLOUD_URL, NEXTCLOUD_API_USER, NEXTCLOUD_API_PASSWORD
from bots.camera import CAMERAS, build_rtsp_url, capture_rtsp_frame

# ---------------------------------------------------------------------------
# llama.cpp vision-сервер
# ---------------------------------------------------------------------------

LLAMA_URL = "http://192.168.128.226:8080/v1/chat/completions"
LLAMA_TOKEN = "llama.cpp"
LLAMA_MODEL = "/var/lib/llama.cpp/models/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf"

# Сжатие
MAX_WIDTH = 640
QUALITY = 50

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


def jpeg_to_base64(jpeg_bytes):
    return base64.b64encode(jpeg_bytes).decode("utf-8")


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
# Vision-анализ через llama.cpp
# ---------------------------------------------------------------------------

async def count_people_single_camera(frame):
    """Посчитать людей на одном кадре."""
    label = frame.get("label", "")
    emoji = frame.get("emoji", "")

    b64 = jpeg_to_base64(frame["jpeg"])

    system_prompt = (
        "Ты — система подсчёта людей по видеокамере. "
        "Посчитай ТОЧНОЕ количество людей на изображении. "
        "Не считай постеры, фотографии, отражения в стекле, тени. "
        "Считай только реальных людей.\n\n"
        "Ответь ТОЛЬКО JSON: {\"count\": число, \"details\": \"кто и где на изображении\"}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Камера: {emoji} {label}. Сколько людей на этом изображении?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        },
    ]

    payload = {
        "model": LLAMA_MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    headers = {"Authorization": f"Bearer {LLAMA_TOKEN}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(LLAMA_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def dedup_across_cameras(frames_with_counts):
    """Финальный запрос: отправить все кадры и попросить deduplicate."""
    image_parts = []
    camera_descs = []

    for key in sorted(frames_with_counts.keys()):
        frame = frames_with_counts[key]
        if "error" in frame:
            continue
        b64 = jpeg_to_base64(frame["jpeg"])
        image_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
        label = frame.get("label", key)
        emoji = frame.get("emoji", "")
        count = frames_with_counts[key].get("count", "?")
        camera_descs.append(f"- {emoji} {label} ({key}): модель насчитала {count} чел.")

    labels_text = "\n".join(camera_descs)

    system_prompt = (
        "Ты — система подсчёта уникальных людей в офисе по нескольким камерам. "
        "Каждая камера показывает свою зону. "
        "Коридор может перехватывать людей из других зон. "
        "Тебе нужно посчитать УНИКАЛЬНЫХ людей.\n\n"
        "Правила:\n"
        "1. Один человек в коридоре + в другой зоне = 1 человек.\n"
        "2. Один человек в переговорке + в коридоре = 1 человек.\n"
        "3. Серверная обычно пуста.\n\n"
        "Сначала перечисли всех людей, которых видишь (по одежде/положению), "
        "затем дай итоговый JSON: "
        '{"total_unique": число, "dedup_notes": "текст"}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Результаты по камерам:\n{labels_text}\n\n"
                        f"Учитывая overlap между зонами, сколько УНИКАЛЬНЫХ людей в офисе?"
                    ),
                },
                *image_parts,
            ],
        },
    ]

    payload = {
        "model": LLAMA_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    headers = {"Authorization": f"Bearer {LLAMA_TOKEN}"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(LLAMA_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Сохранение изображений камер
# ---------------------------------------------------------------------------

def save_camera_images(frames, snap_key):
    """Сохранить сжатые кадры в data/office/images/."""
    images_dir = "data/office/images"
    os.makedirs(images_dir, exist_ok=True)

    saved = []
    for key in sorted(frames.keys()):
        frame = frames[key]
        if "error" in frame:
            continue
        filename = f"{snap_key}_{key}.jpg"
        filepath = os.path.join(images_dir, filename)
        with open(filepath, "wb") as f:
            f.write(frame["jpeg"])
        saved.append((key, frame.get("label", key), frame.get("emoji", ""), len(frame["jpeg"])))
    return saved


# ---------------------------------------------------------------------------
# Отправка в Nextcloud Talk (от имени mountian_admin)
# ---------------------------------------------------------------------------

def send_text_message(room_token, message):
    """Отправить текстовое сообщение в чат Talk от имени mountian_admin."""
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{room_token}/message"
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

    # 2. Сохраняем изображения камер
    save_camera_images(frames, snap_key)
    print(f"\n[{now.isoformat()}] Изображения сохранены в data/office/images/")

    # 3. Посчёт людей на каждой камере отдельно
    print(f"\n[{now.isoformat()}] Анализ каждой камеры отдельно...")
    per_camera_counts = {}
    for key in sorted(frames.keys()):
        frame = frames[key]
        if "error" in frame:
            per_camera_counts[key] = {"error": frame["error"]}
            continue
        try:
            raw = await count_people_single_camera(frame)
            # Парсим JSON
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'\{\s*\"count"', raw)
                if m:
                    end = raw.rfind("}")
                    parsed = json.loads(raw[m.start():end + 1]) if end != -1 else None
                else:
                    parsed = {"count": -1, "details": raw}

            count = parsed.get("count", -1)
            details = parsed.get("details", "")
            per_camera_counts[key] = {"count": count, "details": details}
            emoji = frame.get("emoji", "")
            label = frame.get("label", key)
            print(f"  {emoji} {label}: {count} чел. — {details}")
        except Exception as e:
            per_camera_counts[key] = {"error": str(e)}
            print(f"  ERR {key}: {e}")

    # 4. Deduplication: отправляем все кадры вместе
    print(f"\n[{now.isoformat()}] Deduplication: анализ overlap между камерами...")
    try:
        dedup_raw = await dedup_across_cameras({k: v for k, v in frames.items() if "error" not in v})
    except Exception as e:
        print(f"Ошибка deduplication: {e}")
        dedup_raw = None

    print(f"\n[{now.isoformat()}] Анализ завершён.\n")
    print("Ответ модели (deduplication):")
    print(dedup_raw)

    # 5. Формируем отчёт
    total_unique = "N/A"
    dedup_notes = ""

    if dedup_raw:
        try:
            dedup_result = json.loads(dedup_raw)
        except json.JSONDecodeError:
            m = re.search(r'\{\s*\"total_unique"', dedup_raw)
            if m:
                end = dedup_raw.rfind("}")
                if end != -1:
                    dedup_result = json.loads(dedup_raw[m.start():end + 1])
                else:
                    dedup_result = {}
            else:
                dedup_result = {}
        total_unique = dedup_result.get("total_unique", "N/A")
        dedup_notes = dedup_result.get("dedup_notes", "")

    # Если dedup не сработал, суммируем по камерам
    if total_unique == "N/A":
        print("\n⚠️ Deduplication не дал результата, суммируем по камерам.")
        total = sum(v.get("count", 0) for v in per_camera_counts.values() if isinstance(v, dict) and "count" in v)
        total_unique = total

    print(f"\n{'=' * 50}")
    print(f"  ЛЮДЕЙ В ОФИСЕ: {total_unique}")
    print(f"{'=' * 50}")
    print("\nПо камерам:")
    for key in sorted(per_camera_counts.keys()):
        frame = frames.get(key, {})
        label = frame.get("label", key)
        emoji = frame.get("emoji", "")
        info = per_camera_counts.get(key, {})
        count = info.get("count", "?") if isinstance(info, dict) else "?"
        print(f"  {emoji} {label}: {count}")

    if dedup_notes:
        print(f"\nЗаметки:\n{dedup_notes}")

    # 6. Сохраняем результат в data/office/
    os.makedirs("data/office", exist_ok=True)

    # 1) Сводный файл: последний результат (перезаписывается каждый раз)
    summary = {
        "timestamp": now.isoformat(),
        "total_unique": total_unique,
        "per_camera": {k: v.get("count", "?") if isinstance(v, dict) else "?" for k, v in per_camera_counts.items()},
        "dedup_notes": dedup_notes,
    }
    summary_path = "data/office/latest.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 2) Архивный файл: отдельный снимок по дате и времени
    archive_path = f"data/office/{snap_key}.json"
    archive = dict(summary)
    archive["per_camera_full"] = per_camera_counts
    archive["raw_dedup_answer"] = dedup_raw
    with open(archive_path, "w") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)

    # 3) CSV-файл: история в формате строк (дописываем)
    csv_path = "data/office/history.csv"
    csv_header = "timestamp,total_unique,camera_key,count,details"
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
        count = info.get("count", "?") if isinstance(info, dict) else "?"
        details = (info.get("details", "") if isinstance(info, dict) else "").replace("\n", " ")
        csv_lines.append(f"{now.isoformat()},{total_unique},{key},{count},\"{details}\"\n")

    with open(csv_path, "w") as f:
        f.writelines(csv_lines)

    print(f"\nОтчёты сохранены:")
    print(f"  Сводка:  {summary_path}")
    print(f"  Снимок:  {archive_path}")
    print(f"  История: {csv_path}")
    print(f"  Фото:    data/office/images/{snap_key}_*.jpg")

    # 7. Если обнаружены люди — отправляем уведомление в Nextcloud Talk
    if total_unique and int(total_unique) > 0:
        print(f"\n[{now.isoformat()}] Обнаружены люди — отправляем уведомление в Nextcloud Talk...")

        # Текстовое сообщение
        camera_lines = []
        for key in sorted(per_camera_counts.keys()):
            info = per_camera_counts[key]
            count = info.get("count", 0) if isinstance(info, dict) else 0
            if count > 0:
                frame = frames.get(key, {})
                label = frame.get("label", key)
                emoji = frame.get("emoji", "")
                camera_lines.append(f"  {emoji} {label}: {count}")

        text_msg = (
            f"👥 В офисе обнаружено людей: {total_unique}\n\n"
            f"По камерам:\n" + "\n".join(camera_lines) + "\n\n"
            f"📸 Фото с камер: data/office/images/{snap_key}_*.jpg"
        )
        send_text_message(NEXTCLOUD_ROOM_TOKEN, text_msg)

        # Изображение с камеры, где обнаружен человек (первая с count > 0)
        for key in sorted(per_camera_counts.keys()):
            info = per_camera_counts[key]
            count = info.get("count", 0) if isinstance(info, dict) else 0
            if count > 0 and key in frames and "error" not in frames[key]:
                frame = frames[key]
                label = frame.get("label", key)
                emoji = frame.get("emoji", "")
                img_caption = f"{emoji} {label}: {count} чел. — {now.strftime('%H:%M')}"
                upload_and_share_image(NEXTCLOUD_ROOM_TOKEN, frame["jpeg"], img_caption)
                break

    else:
        print(f"\n[{now.isoformat()}] Людей не обнаружено — уведомление не отправляем.")


if __name__ == "__main__":
    asyncio.run(main())
