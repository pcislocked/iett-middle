import asyncio
from fastapi.testclient import TestClient
from app.main import app

def test():
    client = TestClient(app)
    r = client.get("/v1/stops/260211/announcements")
    print(r.status_code, r.text)

if __name__ == '__main__':
    test()
