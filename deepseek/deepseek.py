from datetime import datetime
import json

from openai import OpenAI

from config import DEEPSEEK_TOKEN


def get_data_by_promt(promt, role, token, content=None, response_format: dict=None) -> str | None:
    client = OpenAI(
        api_key=token,
        base_url="https://api.deepseek.com")

    messages = [
            {"role": "system", "content": role},
            {"role": "user", "content": promt},
        ]
    if content:
        messages.append({"role": "user", "content": content})
    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=messages,
        stream=False,
        response_format=response_format,
        extra_body={"thinking": {"type": "disabled"}}
    )

    return response.choices[0].message.content


def get_joke_request(ds_token, weekday='понедельник', date=datetime.now().date()) -> str | None:
    promt = f'''
    Сегодня {weekday} - {date.strftime('%Y/%m/%d')}).
Сочини одну смешную шутку дня, которая подойдёт для всех сотрудников офиса.
У нас работают менеджеры, программисты, техническая поддержка для клиентов.
Или расскажи интересный исторический факт по сегодняшней дате.

Требования к ответу:
- Только на русском языке
- Смешной
- Пожелай удачного трудового дня
- Можно добавить emoji
- Если пятница - нужно всех поздравить с пятницей

    '''
    role = "Ты — весёлый человек, сотрудник офиса."
    result = get_data_by_promt(promt=promt, role=role, token=ds_token)

    return result


if __name__ == '__main__':
    today = datetime.now().date()
    weekday = today.weekday()
    weekday_title = ''
    if weekday == 0:
        weekday_title = 'Понедельник'
    elif weekday == 1:
        weekday_title = 'Вторник'
    elif weekday == 2:
        weekday_title = 'Среда'
    elif weekday == 3:
        weekday_title = 'Четверг'
    elif weekday == 4:
        weekday_title = 'Пятница'
    elif weekday == 5:
        weekday_title = 'Суббота'
    elif weekday == 6:
        weekday_title = 'Воскресенье'
    print(get_joke_request(ds_token=DEEPSEEK_TOKEN, date=today, weekday=weekday_title))