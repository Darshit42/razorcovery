"""Call recording via LiveKit room-composite egress.

Egress must write to object storage — set these in .env to enable it:
    EGRESS_S3_BUCKET, EGRESS_S3_REGION, EGRESS_S3_ACCESS_KEY, EGRESS_S3_SECRET
    EGRESS_S3_PREFIX   (optional, default "recordings")
    EGRESS_PUBLIC_BASE (optional, e.g. https://cdn.example.com — else the
                        s3 https URL is used)

Without them, recording is skipped (calls still work; no audio file).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("voice.recording")


def _s3_config() -> dict | None:
    b = os.environ.get("EGRESS_S3_BUCKET")
    region = os.environ.get("EGRESS_S3_REGION")
    key = os.environ.get("EGRESS_S3_ACCESS_KEY")
    secret = os.environ.get("EGRESS_S3_SECRET")
    if not (b and region and key and secret):
        return None
    return {"bucket": b, "region": region, "access_key": key, "secret": secret,
            "prefix": os.environ.get("EGRESS_S3_PREFIX", "recordings")}


def recording_url(event_id: str) -> str | None:
    cfg = _s3_config()
    if not cfg:
        return None
    path = f"{cfg['prefix']}/{event_id}.ogg"
    base = os.environ.get("EGRESS_PUBLIC_BASE")
    if base:
        return f"{base.rstrip('/')}/{path}"
    return f"https://{cfg['bucket']}.s3.{cfg['region']}.amazonaws.com/{path}"


async def start_recording(ctx, event_id: str) -> str | None:
    """Start audio-only egress for the room. Returns the egress id, or
    None if S3 isn't configured / start failed."""
    cfg = _s3_config()
    if not cfg:
        return None
    from livekit import api

    req = api.RoomCompositeEgressRequest(
        room_name=ctx.room.name,
        audio_only=True,
        file_outputs=[
            api.EncodedFileOutput(
                file_type=api.EncodedFileType.OGG,
                filepath=f"{cfg['prefix']}/{event_id}.ogg",
                s3=api.S3Upload(
                    bucket=cfg["bucket"], region=cfg["region"],
                    access_key=cfg["access_key"], secret=cfg["secret"],
                ),
            )
        ],
    )
    lk = api.LiveKitAPI()
    try:
        res = await lk.egress.start_room_composite_egress(req)
        logger.info("recording started: egress=%s", res.egress_id)
        return res.egress_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not start recording: %s", exc)
        return None
    finally:
        await lk.aclose()


async def stop_recording(egress_id: str | None) -> None:
    if not egress_id:
        return
    from livekit import api

    lk = api.LiveKitAPI()
    try:
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    except Exception as exc:  # already stopped when room closed, usually
        logger.debug("stop_egress: %s", exc)
    finally:
        await lk.aclose()
