import urllib.request
try:
    url = "https://news.google.com/rss/search?q=AAPL"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as response:
        content = response.read()
        print(f"Successfully fetched {len(content)} bytes from Google News RSS")
except Exception as e:
    print(f"Failed to fetch: {e}")
