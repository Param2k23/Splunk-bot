import requests
import time
import os
from dotenv import load_dotenv
import pprint

load_dotenv()

SPLUNK_BASE = os.getenv("SPLUNK_API_BASE")
SPLUNK_USERNAME = os.getenv("SPLUNK_USERNAME")
SPLUNK_PASSWORD = os.getenv("SPLUNK_PASSWORD")

def splunk_login():
    url = f"{SPLUNK_BASE}/services/auth/login"
    print(f"[DEBUG] Trying to connect to Splunk: {url}")
    try:
        response = requests.post(
            url,
            data={"username": SPLUNK_USERNAME, "password": SPLUNK_PASSWORD},
            verify=False,
            timeout=10
        )
        response.raise_for_status()
        print("Login done")
        return response.text.split("<sessionKey>")[1].split("</sessionKey>")[0]
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Splunk login failed: {e}")
        return None

def splunk_submit_search(session_key, search_query):
    headers = {"Authorization": f"Splunk {session_key}"}
    response = requests.post(
        f"{SPLUNK_BASE}/services/search/jobs",
        headers=headers,
        data={"search": search_query, "output_mode": "json"},
        verify=False
    )
    return response.json().get("sid")

def splunk_wait_for_job(session_key, sid):
    headers = {"Authorization": f"Splunk {session_key}"}
    while True:
        response = requests.get(
            f"{SPLUNK_BASE}/services/search/jobs/{sid}",
            headers=headers,
            params={"output_mode": "json"},
            verify=False
        )
        state = response.json()["entry"][0]["content"]["dispatchState"]
        if state == "DONE":
            break
        time.sleep(1)

def splunk_get_results(session_key, sid):
    headers = {"Authorization": f"Splunk {session_key}"}
    response = requests.get(
        f"{SPLUNK_BASE}/services/search/jobs/{sid}/results",
        headers=headers,
        params={"output_mode": "json"},
        verify=False
    )
    return response.json()