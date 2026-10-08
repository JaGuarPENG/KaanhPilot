import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from ..asr import ASRError
from ..config import Settings
from ..bootstrap import build_services, build_tts
from ..schemas import AgentResponse, ChatRequest, SpeechRequest
from ..tts import TTSError


def create_app(settings: Settings | None = None, llm=None, asr=None, tts=None) -> FastAPI:
    settings = settings or Settings()
    agent, provider, transcriber = build_services(settings, llm=llm, asr=asr)
    speech_provider = build_tts(settings, tts=tts)

    @asynccontextmanager
    async def lifespan(app):
        yield
        try:
            agent.audit.record("service.stopped")
            await provider.close()
        finally:
            try:
                await speech_provider.close()
            finally:
                agent.audit.close()

    app = FastAPI(title="MiniMax Voice Agent", version="0.6.0", lifespan=lifespan)
    app.state.agent = agent

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        agent.audit.record("http.validation_error", method=request.method, path=request.url.path)
        return JSONResponse(status_code=422, content=agent.error("请求参数不符合格式。").model_dump())

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        agent.audit.record("http.error", method=request.method, path=request.url.path,
                           status_code=exc.status_code)
        return JSONResponse(status_code=exc.status_code,
                            content=agent.error("请求无法处理。").model_dump())

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        agent.audit.record("http.internal_error", method=request.method, path=request.url.path,
                           error_type=type(exc).__name__)
        return JSONResponse(status_code=500,
                            content=agent.error("服务内部错误，未能完成请求。").model_dump())

    @app.get("/health")
    async def health():
        return {"status": "ok", "llm_provider": settings.llm_provider,
                "asr_provider": settings.asr_provider, "tts_provider": settings.tts_provider,
                "executor": settings.executor_provider, "scene": settings.scene}

    @app.get("/schema/response")
    async def response_schema():
        return AgentResponse.model_json_schema()

    @app.post("/chat", response_model=AgentResponse)
    async def chat(body: ChatRequest):
        return await agent.handle(body.text, body.session_id)

    @app.post("/speech", response_class=Response)
    async def speech(body: SpeechRequest):
        try:
            audio = await speech_provider.synthesize(body.text)
        except TTSError as exc:
            agent.audit.record("tts.failed", error_type=type(exc).__name__)
            status_code = 503 if settings.tts_provider == "disabled" else 502
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})
        agent.audit.record("tts.finished", characters=len(body.text), audio_bytes=len(audio.data),
                           audio_format=audio.format)
        return Response(content=audio.data, media_type=audio.media_type,
                        headers={"Content-Disposition":
                                 f'inline; filename="agent-reply.{audio.format}"'})

    @app.post("/audio", response_model=AgentResponse)
    async def audio(file: UploadFile = File(...), session_id: str | None = Form(None)):
        try:
            data = await file.read(settings.max_audio_bytes + 1)
        finally:
            await file.close()
        if not data or len(data) > settings.max_audio_bytes:
            return JSONResponse(status_code=413,
                                content=agent.error("音频为空或超过大小限制。").model_dump())
        # Validate any supplied session before expensive ASR/model work.
        if session_id is not None:
            try:
                ChatRequest(text="audio", session_id=session_id)
                agent.sessions.get(session_id)
            except (ValidationError, ValueError):
                return JSONResponse(status_code=422,
                                    content=agent.error("会话不存在或格式不正确。").model_dump())
        try:
            transcript = await transcriber.transcribe(data)
        except ASRError as exc:
            return JSONResponse(status_code=422, content=agent.error(str(exc)).model_dump())
        if not transcript.strip() or len(transcript) > 8000:
            return agent.error("没有识别到有效语音，或文本过长。", session_id)
        response = await agent.handle(transcript, session_id)
        response.transcript = transcript
        return response

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        session_id = None
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    body = ChatRequest.model_validate_json(raw)
                except (ValidationError, ValueError, json.JSONDecodeError):
                    await websocket.send_json(agent.error("请发送 ChatRequest JSON。").model_dump())
                    continue
                response = await agent.handle(body.text, body.session_id or session_id)
                session_id = response.session_id
                await websocket.send_json(response.model_dump())
        except WebSocketDisconnect:
            pass

    return app
