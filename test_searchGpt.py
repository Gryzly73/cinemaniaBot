import requests

def google_search(query, api_key, cse_id, num_results=10):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': api_key,
        'cx': cse_id,
        'num': num_results
    }
    response = requests.get(url, params=params)
    results = response.json().get('items', [])
    return results

if __name__ == "__main__":
   # api_key = "AIzaSyBIOOqCAUWqWGejyR6wPJCyT2G19p9H1YM"  # os.getenv("GOOGLE_API_KEY")
   # cx = "66bcaae60c0d148f7"
    API_KEY = 'AIzaSyBIOOqCAUWqWGejyR6wPJCyT2G19p9H1YM'
    CSE_ID = '6bcaae60c0d148f7'
    query = 'Новости технологий'

    search_results = google_search(query, API_KEY, CSE_ID)

    for i, result in enumerate(search_results, start=1):
        title = result.get('title')
        link = result.get('link')
        print(f"{i}. {title}\n   {link}\n")
