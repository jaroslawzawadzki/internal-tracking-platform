import requests
from django.conf import settings

def stape_get(path, params=None):
    url = f"{settings.STAPE_API_BASE}{path}"
    headers = {
        "X-AUTH-TOKEN": settings.STAPE_API_KEY
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    return response.json()
