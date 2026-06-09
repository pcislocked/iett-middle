# -*- coding: utf-8 -*-

# Fix routes.py
with open('app/routers/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()
target = """                if soap_stops:
                    stops = soap_stops"""
replacement = """                if soap_stops:
                    stops = soap_stops
                    has_null_coords = any(s.latitude is None or s.longitude is None for s in stops)"""
content = content.replace(target, replacement)
with open('app/routers/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Fix stop_indexer.py
with open('app/services/stop_indexer.py', 'r', encoding='utf-8') as f:
    content = f.read()
target = """        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error in stop indexer")"""
replacement = """        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error in stop indexer")
            await asyncio.sleep(60)
            continue"""
content = content.replace(target, replacement)
with open('app/services/stop_indexer.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Fix mobiett_client.py
with open('app/services/mobiett_client.py', 'r', encoding='utf-8') as f:
    content = f.read()
target = """        except Exception as e:
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e"""
replacement = """        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                self._access_token = None
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e
        except Exception as e:
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e"""
content = content.replace(target, replacement)
with open('app/services/mobiett_client.py', 'w', encoding='utf-8') as f:
    f.write(content)
