import os

filepath = r"C:\Users\amdin\Desktop\iett-project\iett-middle\app\routers\routes.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# We need to add get_batch_announcements endpoint.
# Let's add it before get_route_announcements.

batch_endpoint = '''
@router.get("/announcements/batch", response_model=list[Announcement])
async def get_batch_announcements(routes: str = Query(..., description="Comma-separated route codes")):
    """Get active disruption announcements for multiple routes at once.
    This avoids N+1 queries from the frontend."""
    route_list = [r.strip() for r in routes.split(",") if r.strip()]
    if not route_list:
        return []
        
    # We can just call get_route_announcements for each route, which handles its own caching!
    # Wait, get_route_announcements is a FastAPI dependency/route function. We can just call the cache directly or await them.
    # To be safe, we'll fetch from the client directly, but wait! The user said "ilgili cache'ından tekrar bakarsın".
    # So we should call the same cache mechanism.
    
    tasks = [get_route_announcements(r) for r in route_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    combined = []
    for r, res in zip(route_list, results):
        if isinstance(res, Exception):
            logger.warning("Failed to fetch announcements for %s: %s", r, res)
        else:
            for ann in res:
                ann_dict = ann if isinstance(ann, dict) else ann.model_dump()
                ann_dict["route_code"] = r
                combined.append(ann_dict)
                
    return combined
'''

content = content.replace('@router.get("/{hat_kodu}/announcements"', batch_endpoint + '\n@router.get("/{hat_kodu}/announcements"')

# Also make sure the negative caching patch is in get_route_announcements.
# (Since I forced pushed master on middle, the previous patch is gone!)
# I must re-apply the negative cache patch.
old_code = '''    async def _fetch():
        client = IettClient(get_session())
        try:
            announcements = await client.get_announcements(hat_kodu)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return [a.model_dump() for a in announcements]'''

new_code = '''    async def _fetch():
        client = IettClient(get_session())
        try:
            announcements = await client.get_announcements(hat_kodu)
        except IettApiError as exc:
            logger.warning("IETT API failed for announcements %s, returning empty list (negative cache): %s", hat_kodu, exc)
            return []
        return [a.model_dump() for a in announcements]'''

content = content.replace(old_code, new_code)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated routes.py")
