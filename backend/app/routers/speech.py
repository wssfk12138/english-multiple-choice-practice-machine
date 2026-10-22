from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..schemas import SpeechSynthesisRequest
from ..services.speech import SpeechError, get_speech_service


router = APIRouter(prefix="/speech", tags=["speech"])


@router.get("/status")
def status() -> dict[str, object]:
    return get_speech_service().status()


@router.post("/synthesize")
def synthesize(payload: SpeechSynthesisRequest) -> Response:
    try:
        result = get_speech_service().synthesize(payload.text, payload.accent)
    except SpeechError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
            headers={"X-Speech-Error": exc.code},
        ) from exc
    return Response(
        content=result["audio"],
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Speech-Accent": str(result["accent"]),
            "X-Sample-Rate": str(result["sample_rate"]),
        },
    )
