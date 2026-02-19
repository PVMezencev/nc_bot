import asyncio

import httpx

import config


async def get_user_profile(user_id):
    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }
    url = f'{config.NEXTCLOUD_URL}/ocs/v1.php/cloud/users/{user_id}'
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                url,
                headers=headers,
                params={'format': 'json'},
                auth=(config.NEXTCLOUD_API_USER, config.NEXTCLOUD_API_PASSWORD),
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error sending to Nextcloud: {e}")


async def get_users_ids():
    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }
    url = f'{config.NEXTCLOUD_URL}/ocs/v1.php/cloud/users'
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                url,
                headers=headers,
                params={'format': 'json'},
                auth=(config.NEXTCLOUD_API_USER, config.NEXTCLOUD_API_PASSWORD),
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error sending to Nextcloud: {e}")


async def users():
    users = []
    resp = await get_users_ids()
    ocs = resp.get('ocs')
    data = ocs.get('data')
    users_ids = data.get('users')
    for id in users_ids:
        user_resp = await get_user_profile(id)
        user_ocs = user_resp.get('ocs')
        profile = user_ocs.get('data')
        users.append(profile)

    return users


if __name__ == "__main__":
    asyncio.run(users())
