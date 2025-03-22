import os
import requests
from dotenv import load_dotenv

load_dotenv()


def search_movie(query: str) -> list:
    api_key = 'AIzaSyBIOOqCAUWqWGejyR6wPJCyT2G19p9H1YM' # os.getenv("GOOGLE_API_KEY")
    cx = '66bcaae60c0d148f7' # os.getenv("GOOGLE_CX_ID")
    url = "https://www.googleapis.com/customsearch/v1"

    params = {
        "key": api_key,
        "cx": cx,
        "q": f"{query} фильм",
        "num": 1,  # Количество результатов
        "lr": "lang_ru"  # Язык: русский
    }

    response = requests.get(url, params=params)
    results = response.json().get("items", [])

    return results


if __name__ == "__main__":
    query = ""
    results = search_movie(query)
    for item in results:
        print(f"Заголовок: {item['title']}")
        print(f"Ссылка: {item['link']}")
        print(f"Описание: {item['snippet']}")
        print("-" * 50)