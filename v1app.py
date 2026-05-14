
#execute 'chainlit create-secret' su powershell -> aggiungi nel .env  CHAINLIT_AUTH_SECRET=xxxxx_generato_dal_comando

from dotenv import load_dotenv
load_dotenv()

import io, json, os, re
from typing import Optional

import httpx   #http async, x fastapi backend
import markdown2
from xhtml2pdf import pisa

import chainlit as cl
import chainlit.data as cl_data  #storage chat persistence
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer #storage chat persistence

from chainlit.types import ThreadDict  #Struttura conversazione salvata


_STATUS_LINE = re.compile(r"`\[[^\]]+\s+working\.\.\.\]`")
_JSON_PREAMBLE = re.compile(r"^\s*```json\s*\{[^`]*?\}\s*```\s*", re.DOTALL)

def clean_display(text:str)->str:
    return _JSON_PREAMBLE.sub("", text).lstrip()

def md_to_pdf(text: str)->bytes:
    cleaned = "\n".join(
        line for line in text.splitlines()
        if not _STATUS_LINE.search(line)
    ).strip()  #rimuove linee di stato come `[working...]` che non sono utili nel PDF
    body = markdown2.markdown(cleaned, extras=["tables", "fenced-code-blocks","cuddled-lists"])  #converte markdown in html, tables → supporto tabelle fenced-code-blocks → code cuddled-lists → liste compatte
    html = f"""<html><head><meta charset="utf-8"><style>
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.25; padding: 15px; }}
        h1 {{ font-size: 21px; margin: 8px 0 4px 0; line-height: 1.2; }}
        h2 {{ font-size: 18px; margin: 6px 0 3px 0; line-height: 1.2; }}
        h3 {{ font-size: 17px; margin: 5px 0 3px 0; line-height: 1.2; }}
        p {{ margin: 3px 0; line-height: 1.25; }}
        ul, ol {{ margin: 2px 0; padding-left: 20px; }}
        li {{ margin: 1px 0; padding: 0; line-height: 1.25; }}
        li p {{ display: inline; margin: 0; }}
        strong {{ font-weight: bold; }}
        code {{ background: #f4f4f4; padding: 2px 4px; font-size: 13px; }}
        pre {{ background: #f4f4f4; padding: 6px; margin: 4px 0; font-size: 13px; line-height: 1.2; }}
        table {{ border-collapse: collapse; width: 100%; margin: 5px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 4px; font-size: 13px; line-height: 1.2; }}
        th {{ background: #f0f0f0; font-weight: bold; }}
    </style></head><body>{body}</body></html>"""  #setta style shared x pdfs(perche puoi anche creare pdf)
    buf = io.BytesIO()  #crea buffer in memoria
    pisa.CreatePDF(html, dest=buf)  #converte html->pdf
    return buf.getvalue()  #ritorna il buffer come bytes

API_URL = os.getenv("FASTAPI_URL", "http://localhost:8080")  #stessa porta di quella usata da fastapi in 'v1main.py'
API_KEY = os.getenv("API_KEY","")

cl_data._data_layer = SQLAlchemyDataLayer(conninfo="sqlite+aiosqlite:///./data/chat_history.db")  #db x chainlit

#auth chainlit
@cl.password_auth_callback   
def auth_callback(username: str, password: str)->Optional[cl.User]:
    if username == os.getenv("APP_USER","admin") and password == os.getenv("APP_PASSWORD", "admin"): 
        return cl.User(identifier=username, metadata={"role":"user"})
    return None

#attivato quando utente apre chat
@cl.on_chat_start
async def on_start():
    cl.user_session.set("history", [])
    cl.user_session.set("last_response_msg", None)
    await cl.Message(content="Hello! Upload documents (drag & drop) or ask me a question.").send()

#quando utente ritorna su chat salvata
@cl.on_chat_resume
async def on_resume(thread: ThreadDict):
    history = []
    for step in thread.get("steps", []):
        if step.get("type")=="user_message":
            history.append({"role":"user", "content":step.get("output","")})
        elif step.get("type") == "assistant_message":
            history.append({"role": "assistant", "content": step.get("output", "")})
        #formato OpenAI-style
    cl.user_session.set("history", history)

#trigger ad ogni messaggio send
@cl.on_message
async def on_message(message: cl.Message):
    history = cl.user_session.get("history", [])
    if message.elements:
        handles = [open(elem.path, "rb") for elem in message.elements]  #apre file caricati dall'utente in binary mode, quindi handles diventa una lista di file obj
        files = [("files", (elem.name, fh)) for elem, fh in zip(message.elements, handles)]  #costruisce una struttura compatibile con multipart/form-data di httpx, xk il formato richiesto da fastapi x upload file è [("files", (filename1, file1)), ("files", (filename2, file2)), ...]
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                msg= cl.Message(content="Ingesting documents...")
                await msg.send()
                resp = await client.post(f"{API_URL}/upload", files=files, headers={"Authorization": f"Bearer {API_KEY}"})
                resp.raise_for_status()  #resp.raise_for_status() → lancia eccezione se il server risponde con errore (ad esempio 400 o 500)
                msg.content = resp.json()["message"]
                await msg.update()
        finally:
            for fh in handles: #Chiude tutti i file aperti
                fh.close()
        if not message.content or not message.content.strip():
            return

    if message.elements:
        file_names = [elem.name for elem in message.elements]
        user_content = message.content + f"\n[uploaded_files: {', '.join(file_names)}]"
    else:
        user_content = message.content
    history.append({"role": "user", "content": user_content})
    response_msg = cl.Message(content="")
    await response_msg.send()
    full_response = ""
    async with httpx.AsyncClient(timeout=300) as client:
        headers = {}
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"
        async with client.stream(  #chiamata POST in streaming al backend FastAPI /v1/chat/completions
            "POST",
            f"{API_URL}/v1/chat/completions",
            json={"messages":history, "stream": True},
            #headers={"Authorization": f"Bearer {API_KEY}" },
            headers=headers
        ) as resp:
            async for line in resp.aiter_lines():  #Iteriamo linea per linea nello stream SSE, Ignora linee che non iniziano con data: (protocollo SSE standard).
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    continue
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"]
                token = delta.get("content", "")
                if token:  #aggiunge token alla variabile cumulativa full_response, aggiorna il messaggio UI in tempo reale, token per token.
                    full_response += token
                    await response_msg.stream_token(token)

    prev_msg = cl.user_session.get("last_response_msg")
    if prev_msg:
        prev_msg.actions = []
        await prev_msg.update()
    #or ok se esiste un messaggio precedente, rimosso eventuali bottoni rimasti
    display_response = clean_display(full_response)
    response_msg.content = display_response
    response_msg.actions = [
        cl.Action(name="download_pdf", payload={"text":display_response}, label="Download PDF", icon="download")
    ]
    await response_msg.update()
    cl.user_session.set("last_response_msg", response_msg)
    history.append({"role":"assistant", "content": display_response})
    cl.user_session.set("history", history)

#download pdf action callback
@cl.action_callback("download_pdf")
async def download_pdf(action: cl.Action):
    pdf_bytes = md_to_pdf(action.payload["text"])
    await cl.Message(
        content = "",
        elements = [cl.File(name="response.pdf", content=pdf_bytes, mime="application/pdf")]
    ).send()
 