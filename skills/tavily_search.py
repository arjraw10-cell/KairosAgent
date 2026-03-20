import json
import os
import urllib.request


def run(context, query, search_depth="basic", max_results=5):
    """
    Search the web using the Tavily API.
    """
    url = "https://api.tavily.com/search"
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return {"ok": False, "error": "TAVILY_API_KEY is not set."}
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": True,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = response.read().decode("utf-8")
            return json.loads(res_data)
    except Exception as e:
        return {"ok": False, "error": str(e)}
