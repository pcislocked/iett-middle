import requests
from app.config import settings

def _call_service(alias, data):
    url = "https://ntcapi.iett.istanbul/oauth2/v2/auth"
    payload = {
        "client_id": settings.ntcapi_client_id,
        "client_secret": settings.ntcapi_client_secret,
        "grant_type": "client_credentials",
        "scope": settings.ntcapi_scope,
    }
    r = requests.post(url, json=payload, headers={"User-Agent": "okhttp/5.0.0-alpha.11"})
    r.raise_for_status()
    token = r.json()["access_token"]
    
    r2 = requests.post(
        "https://ntcapi.iett.istanbul/service",
        json={"alias": alias, "data": data},
        headers={"Authorization": f"Bearer {token}", "User-Agent": "okhttp/5.0.0-alpha.11"}
    )
    r2.raise_for_status()
    return r2.json()

def main():
    print("Probing ybs point-passing...")
    payload_ybs = {
        "data": {
            "password": settings.ntcapi_ybs_password,
            "username": settings.ntcapi_ybs_username,
        },
        "method": "POST",
        "path": ["real-time-information", "point-passing", "285"], # hat_id for 14M or similar
    }
    try:
        raw_ybs = _call_service("ybs", payload_ybs)
        if raw_ybs:
            print("point-passing fields:", raw_ybs[0].keys())
    except Exception as e:
        print("ybs error", e)

    print("Probing mainGetBusLocation_basic...")
    payload_loc = {"AKYOLBILYENI.K_ARAC.KAPINUMARASI": "K1234"}
    try:
        raw_loc = _call_service("mainGetBusLocation_basic", payload_loc)
        if raw_loc:
            print("mainGetBusLocation_basic fields:", raw_loc[0].keys())
    except Exception as e:
        print("loc error", e)

if __name__ == "__main__":
    main()
