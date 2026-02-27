"""
Live IETT API probe script.
Hits every known endpoint, captures real response shapes, and writes
probe_results.json with summaries + field analysis.

Run: python scripts/probe_apis.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import textwrap
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiohttp
from bs4 import BeautifulSoup

IETT_SOAP  = "https://api.ibb.gov.tr/iett"
IETT_REST  = "https://iett.istanbul/tr/RouteStation"
TRAFIK     = "https://trafik.ibb.gov.tr"
UA = "iett-probe/1.0"

SOAP_ENV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>{body}</soap:Body></soap:Envelope>"
)

results: dict = {}


async def soap_post(session, url, body, action):
    envelope = SOAP_ENV.format(body=body)
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action, "User-Agent": UA}
    try:
        async with session.post(url, data=envelope.encode(), headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)


async def http_get(session, url, params=None):
    try:
        async with session.get(url, params=params,
                               headers={"User-Agent": UA},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            ct = r.headers.get("Content-Type", "")
            body = await r.text()
            return r.status, ct, body
    except Exception as e:
        return 0, "", str(e)


_TEMPURI = "http://tempuri.org/"

def extract_soap_json(xml_text, tag):
    from xml.etree import ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
        el = root.find(f".//{{{_TEMPURI}}}{tag}")
        if el is not None and el.text:
            return json.loads(el.text)
    except Exception:
        pass
    return None


def summarise(label, status, data):
    entry = {"status": status, "label": label}
    if data is None:
        entry["result"] = "PARSE_ERROR"
    elif isinstance(data, list):
        entry["count"] = len(data)
        entry["sample"] = data[0] if data else None
        if data:
            entry["fields"] = list(data[0].keys()) if isinstance(data[0], dict) else "non-dict"
    elif isinstance(data, dict):
        entry["fields"] = list(data.keys())
        entry["sample"] = data
    else:
        entry["raw_preview"] = str(data)[:300]
    results[label] = entry


async def probe_all():
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as s:

        print("── 1. GetFiloAracKonum_json (all fleet) ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/FiloDurum/SeferGercaklesme.asmx",
            '<GetFiloAracKonum_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetFiloAracKonum_json"',
        )
        data = extract_soap_json(xml, "GetFiloAracKonum_jsonResult")
        summarise("1_all_fleet", status, data)
        print(f"  HTTP {status} | records={len(data) if data else 'err'} | fields={list(data[0].keys()) if data else 'n/a'}")

        print("── 2. GetHatOtoKonum_json (route 14M) ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/FiloDurum/SeferGercaklesme.asmx",
            '<GetHatOtoKonum_json xmlns="http://tempuri.org/"><HatKodu>14M</HatKodu></GetHatOtoKonum_json>',
            '"http://tempuri.org/GetHatOtoKonum_json"',
        )
        data = extract_soap_json(xml, "GetHatOtoKonum_jsonResult")
        summarise("2_route_fleet_14M", status, data)
        print(f"  HTTP {status} | records={len(data) if data else 'err'} | sample={data[0] if data else 'n/a'}")

        print("── 2b. GetHatOtoKonum_json (route 500T) ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/FiloDurum/SeferGercaklesme.asmx",
            '<GetHatOtoKonum_json xmlns="http://tempuri.org/"><HatKodu>500T</HatKodu></GetHatOtoKonum_json>',
            '"http://tempuri.org/GetHatOtoKonum_json"',
        )
        data = extract_soap_json(xml, "GetHatOtoKonum_jsonResult")
        summarise("2b_route_fleet_500T", status, data)
        print(f"  HTTP {status} | records={len(data) if data else 'err'}")

        print("── 3. GetStationInfo dcode=220602 ──")
        status, ct, html = await http_get(s, f"{IETT_REST}/GetStationInfo", {"dcode": "220602", "langid": "1"})
        soup = BeautifulSoup(html, "html.parser")
        arrivals_raw = []
        for item in soup.select("div.line-item div.content:not(.content-header)"):
            span = item.select_one("span")
            b = item.select_one("b")
            p = item.select_one("p")
            if span and b and p:
                m = re.search(r"(\d+)\s*dk", b.text)
                arrivals_raw.append({"route": span.text.strip(), "eta_raw": b.text.strip(),
                                     "eta_min": int(m.group(1)) if m else None,
                                     "dest": p.text.replace(b.text, "").strip()})
        summarise("3_arrivals_220602", status, arrivals_raw)
        print(f"  HTTP {status} | arrivals={len(arrivals_raw)} | {[a['route'] for a in arrivals_raw[:5]]}")

        print("── 3b. GetStationInfo dcode=220601 ──")
        status2, _, html2 = await http_get(s, f"{IETT_REST}/GetStationInfo", {"dcode": "220601", "langid": "1"})
        soup2 = BeautifulSoup(html2, "html.parser")
        arr2 = []
        for item in soup2.select("div.line-item div.content:not(.content-header)"):
            span = item.select_one("span"); b = item.select_one("b"); p = item.select_one("p")
            if span and b and p:
                m = re.search(r"(\d+)\s*dk", b.text)
                arr2.append({"route": span.text.strip(), "eta_min": int(m.group(1)) if m else None})
        summarise("3b_arrivals_220601", status2, arr2)
        print(f"  HTTP {status2} | arrivals={len(arr2)}")

        print("── 3c. GetStationInfo dcode=216572 (via stop) ──")
        status3, _, html3 = await http_get(s, f"{IETT_REST}/GetStationInfo", {"dcode": "216572", "langid": "1"})
        soup3 = BeautifulSoup(html3, "html.parser")
        arr3 = []
        for item in soup3.select("div.line-item div.content:not(.content-header)"):
            span = item.select_one("span"); b = item.select_one("b"); p = item.select_one("p")
            if span and b and p:
                m = re.search(r"(\d+)\s*dk", b.text)
                arr3.append({"route": span.text.strip(), "eta_min": int(m.group(1)) if m else None})
        summarise("3c_arrivals_216572", status3, arr3)
        print(f"  HTTP {status3} | arrivals={len(arr3)}")

        print("── 4. GetPlanlananSeferSaati_json (500T) ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/UlasimAnaVeri/PlanlananSeferSaati.asmx",
            '<GetPlanlananSeferSaati_json xmlns="http://tempuri.org/"><HatKodu>500T</HatKodu></GetPlanlananSeferSaati_json>',
            '"http://tempuri.org/GetPlanlananSeferSaati_json"',
        )
        data = extract_soap_json(xml, "GetPlanlananSeferSaati_jsonResult")
        summarise("4_schedule_500T", status, data)
        print(f"  HTTP {status} | records={len(data) if data else 'err'} | sample={data[0] if data else 'n/a'}")

        print("── 5. GetDuyurular_json (announcements) ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/UlasimDinamikVeri/Duyurular.asmx",
            '<GetDuyurular_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetDuyurular_json"',
        )
        data = extract_soap_json(xml, "GetDuyurular_jsonResult")
        summarise("5_announcements", status, data)
        print(f"  HTTP {status} | records={len(data) if data else 'err'} | sample={data[0] if data else 'n/a'}")

        print("── 6. DurakDetay_GYY (stop list for 14M) ──")
        from xml.etree import ElementTree as ET
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/ibb/ibb.asmx",
            '<DurakDetay_GYY xmlns="http://tempuri.org/"><hat_kodu>14M</hat_kodu></DurakDetay_GYY>',
            '"http://tempuri.org/DurakDetay_GYY"',
        )
        stops_raw = []
        try:
            root = ET.fromstring(xml)
            for tbl in root.iter("Table"):
                stops_raw.append({ch.tag: ch.text for ch in tbl})
        except Exception:
            pass
        summarise("6_route_stops_14M", status, stops_raw)
        print(f"  HTTP {status} | stops={len(stops_raw)} | fields={list(stops_raw[0].keys()) if stops_raw else 'n/a'}")

        print("── 7. GetSearchItems q=ahmet mithat ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetSearchItems", {"key": "ahmet mithat", "langid": "1"})
        try:
            sdata = json.loads(body)
            items = sdata.get("list", [])
        except Exception:
            items = []
        summarise("7_search_ahmet_mithat", status, items)
        print(f"  HTTP {status} | results={len(items)} | sample={items[0] if items else 'n/a'}")

        print("── 7b. GetSearchItems q=500T ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetSearchItems", {"key": "500T", "langid": "1"})
        try:
            sdata2 = json.loads(body)
            items2 = sdata2.get("list", [])
        except Exception:
            items2 = []
        summarise("7b_search_500T", status, items2)
        print(f"  HTTP {status} | results={len(items2)} | sample={items2[0] if items2 else 'n/a'}")

        print("── 8. GetRouteByStation dcode=220602 ──")
        status, ct, html = await http_get(s, f"{IETT_REST}/GetRouteByStation", {"dcode": "220602", "langid": "1"})
        soup = BeautifulSoup(html, "html.parser")
        routes_at_stop = {sp.text.strip() for item in soup.select("div.line-item") for sp in item.select("a > span:first-child")}
        summarise("8_routes_at_stop_220602", status, list(routes_at_stop))
        print(f"  HTTP {status} | ct={ct[:40]} | routes={sorted(routes_at_stop)}")

        print("── 9. GetStationForRoute hatkod=14M ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetStationForRoute", {"hatkod": "14M", "langid": "1"})
        try:
            sfr = json.loads(body)
        except Exception:
            sfr = body[:200]
        summarise("9_station_for_route_14M", status, sfr if isinstance(sfr, list) else [{"raw": str(sfr)[:200]}])
        print(f"  HTTP {status} | ct={ct[:40]} | type={type(sfr).__name__} | len={len(sfr) if isinstance(sfr, list) else '?'}")
        if isinstance(sfr, list) and sfr:
            print(f"  sample={sfr[0]}")

        print("── 10. GetFastStation routeid=14M ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetFastStation", {"routeid": "14M", "langid": "1"})
        try:
            fst = json.loads(body)
        except Exception:
            fst = body[:200]
        summarise("10_fast_station_14M", status, fst if isinstance(fst, list) else [{"raw": str(fst)[:200]}])
        print(f"  HTTP {status} | type={type(fst).__name__} | len={len(fst) if isinstance(fst, list) else '?'}")
        if isinstance(fst, list) and fst:
            print(f"  sample={fst[0]}")

        print("── 11. GetAllRoute rcode=14M ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetAllRoute", {"rcode": "14M"})
        try:
            ar = json.loads(body)
        except Exception:
            ar = body[:200]
        summarise("11_all_route_14M", status, ar if isinstance(ar, list) else [{"raw": str(ar)[:300]}])
        print(f"  HTTP {status} | type={type(ar).__name__} | data={ar}")

        print("── 12. GetRouteStation key empty ──")
        status, ct, body = await http_get(s, f"{IETT_REST}/GetRouteStation", {"key": "", "langid": "1"})
        try:
            rs = json.loads(body)
            rs_list = rs if isinstance(rs, list) else rs.get("list", [rs])
        except Exception:
            rs_list = [{"raw": body[:200]}]
        summarise("12_route_station_all", status, rs_list)
        print(f"  HTTP {status} | type={type(rs_list).__name__} | len={len(rs_list)}")
        if isinstance(rs_list, list) and rs_list:
            print(f"  sample={rs_list[0]}")

        print("── 13. IBB TrafficIndex ──")
        status, ct, body = await http_get(s, f"{TRAFIK}/TrafficIndex_Sc1_Cont")
        summarise("13_traffic_index", status, {"raw": body[:200], "content_type": ct})
        print(f"  HTTP {status} | ct={ct[:40]} | body={body[:100]}")

        print("── 14. IBB SegmentData ──")
        status, ct, body = await http_get(s, f"{TRAFIK}/SegmentData")
        try:
            segs = json.loads(body)
        except Exception:
            segs = None
        summarise("14_traffic_segments", status,
                  segs[:3] if isinstance(segs, list) else [{"raw": body[:200]}])
        size_kb = len(body) / 1024
        print(f"  HTTP {status} | ct={ct[:40]} | size={size_kb:.1f}kB | records={len(segs) if isinstance(segs, list) else '?'}")
        if isinstance(segs, list) and segs:
            print(f"  fields={list(segs[0].keys())} sample={segs[0]}")

        print("── 15. IBB TunnelSegments ──")
        status, ct, body = await http_get(s, f"{TRAFIK}/TunnelSegments")
        try:
            tun = json.loads(body)
        except Exception:
            tun = None
        summarise("15_tunnel_segments", status, tun if isinstance(tun, list) else [{"raw": body[:200]}])
        print(f"  HTTP {status} | ct={ct[:40]} | body={body[:200]}")

        print("── 15b. IBB StaticLayerVersion ──")
        status, ct, body = await http_get(s, f"{TRAFIK}/StaticLayerVersion")
        summarise("15b_static_layer_version", status, {"raw": body[:200]})
        print(f"  HTTP {status} | body={body[:200]}")

        print("── DEAD ENDPOINT CHECK: GetFiloDurum_json ──")
        status, xml = await soap_post(
            s, f"{IETT_SOAP}/FiloDurum/SeferGercaklesme.asmx",
            '<GetFiloDurum_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetFiloDurum_json"',
        )
        summarise("dead_GetFiloDurum", status, {"status": status, "preview": xml[:100]})
        print(f"  HTTP {status} (expected 500)")

        # Via-stop cross-filter sanity check
        print("── CROSS-FILTER: arrivals at 220602 via 216572 ──")
        routes_220602 = {sp.text.strip() for item in BeautifulSoup(
            (await http_get(s, f"{IETT_REST}/GetRouteByStation", {"dcode": "220602", "langid": "1"}))[2], "html.parser"
        ).select("div.line-item") for sp in item.select("a > span:first-child")}
        routes_216572 = {sp.text.strip() for item in BeautifulSoup(
            (await http_get(s, f"{IETT_REST}/GetRouteByStation", {"dcode": "216572", "langid": "1"}))[2], "html.parser"
        ).select("div.line-item") for sp in item.select("a > span:first-child")}
        common = routes_220602 & routes_216572
        summarise("cross_filter_via", 200, [{"common_routes": sorted(common),
                                              "count_origin": len(routes_220602),
                                              "count_via": len(routes_216572),
                                              "count_common": len(common)}])
        print(f"  origin routes={len(routes_220602)} via routes={len(routes_216572)} common={sorted(common)}")


async def main():
    print(f"\n{'='*60}")
    print(f"  IETT API Probe — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    await probe_all()
    out = "scripts/probe_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Results written → {out}")
    print(f"  {len(results)} endpoints probed\n")


if __name__ == "__main__":
    asyncio.run(main())
