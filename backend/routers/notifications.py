"""Notification endpoints — SSE stream, history, dismiss, scan, trace."""
import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..schemas import MacroAlert, AlertResponse
from ..agent_policy_guardrails import ensure_capability
from ..deps import sse_clients, last_trace_data, news_scanner, notification_pipeline, db, knowledge_store

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/stream")
async def notification_stream():
    queue = asyncio.Queue()
    sse_clients.append(queue)
    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_clients.remove(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/history", response_model=AlertResponse)
async def get_notification_history():
    """Returns all unseen alerts from persistent store."""
    alerts = db.get_all_alerts(limit=30)
    unread = db.count_unread()
    return AlertResponse(alerts=[MacroAlert(**a) for a in alerts], total=len(alerts), unread=unread)


@router.post("/dismiss/{alert_id}")
async def dismiss_notification(alert_id: str):
    """Mark a single alert as seen."""
    db.mark_seen(alert_id)
    return {"status": "dismissed"}


@router.post("/mark-seen")
async def mark_all_seen():
    """Mark all alerts as seen (user opened the bell). Seen alerts are then deleted."""
    db.mark_all_seen()
    db.delete_seen()
    return {"status": "all_seen_and_cleared", "remaining": db.count_unread()}


@router.post("/scan")
async def manual_scan():
    data = await news_scanner.fetch_data()
    alerts = notification_pipeline.process(data.get("new_headlines", []))
    for alert in alerts:
        db.insert_alert(alert)
        for queue in sse_clients:
            await queue.put(json.dumps(alert))
    return {"scanned": data["total_scanned"], "new_alerts": len(alerts)}


@router.get("/trace")
async def notification_trace():
    """Return cached trace data from last background scan (instant, no network)."""
    if last_trace_data:
        last_trace_data["stored_alerts"] = db.get_all_alerts(limit=10)
        return last_trace_data
    return {
        "total_scanned": 0, "passed_filter": 0, "rejected": 0, "alerts_produced": 0,
        "headlines": [], "stored_alerts": db.get_all_alerts(limit=10), "alerts": [],
    }
