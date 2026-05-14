
import importlib, json, os, tempfile, time, uuid  #importlib x importare moduli dinamicamente
from typing import List
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel  #x schema req/res validazione dati

from v1tools import rag

agent = importlib.import_module(
    f"{os.getenv('AGENT', 'v1agentLangGraphsupervisoragent')}" #legge quella env var, altrimenti fallback
    )

router = APIRouter()

class Message(BaseModel):  #definisce struttura dei mexs
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str = agent.MODEL_ID  #e.g.MODEL_ID = "gpt-4-ragwire"
    messages: List[Message]

def chunk(cid, ts, content="", finish_reason=None):  #chunk streaming OpenAI compatible. cid=completion id, finish_reason=stop/error/timeout
    delta = {"content":content} if content else ({"role":"assistant", "content":""} if finish_reason is None else {})
    payload = {  #risposta json
        "id": cid,
        "object": "chat.completion.chunk", #compatibilità OpenAI API
        "created": ts,
        "model": agent.MODEL_ID,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }
        ]
    }
    return f"data: {json.dumps(payload)}\n\n"  #convert python obj->json formatted string,formato SSE (Server-Sent Events)

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": agent.MODEL_ID,"object":"model", "created": int(time.time()), "owned_by":"ragwire"}]}

@router.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    return {"id": model_id, "object":"model", "created": int(time.time()), "owned_by":"ragwire"}

@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]  #model_dump() x convertire pydantic obj in dict
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")
    cid, ts = f"chatcmpl-{uuid.uuid4().hex}", int(time.time())  #genera id univoco e timestamp
    async def stream():
        yield chunk(cid, ts) #invia inizializzazione stream, primo chunck vuoto
        try:
            async for text in agent.stream(messages):  #l'agent produce token/chunk progressivamente
                yield chunk(cid, ts, content=text)
        except Exception as exc: 
            yield chunk(cid, ts, content=f"\n[Error: {exc}]")
        yield chunk(cid, ts, finish_reason="stop")  #chuck per segnalare fine stream
        yield "data: [DONE]\n\n"  #ultimo chunk openAI-compatible finale.
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})  #return streaming http continuo, text/event-stream è tipo SSE, no-cache no cache browser, "X-Accel-Buffering":"no" disabilita buffering NGINX (super da fare x fare realtime!)

@router.post("/upload")   #here ingest_directory() ma solo x file temporaneo!! non ho creato un folder statico dove tenere i files
async def upload_documents(files: List[UploadFile] = File(...)):  #accetta multipli file upload, File(...) significa field obbligatorio
    with tempfile.TemporaryDirectory() as tmpdir:  #temp dir
        for f in files:
            with open(os.path.join(tmpdir, f.filename), "wb") as out:  #wb è write binary
                out.write(await f.read())  #legge upload async e salva
        stats = rag.ingest_directory(tmpdir)
        return {"message": f"Ingested {stats['chunks_created']} chunks from {stats['processed']} file(s) ({stats['skipped']} skipped).", "stats": stats}
    