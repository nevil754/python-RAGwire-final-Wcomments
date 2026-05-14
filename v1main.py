

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from v1routes import router, agent

app = FastAPI(title="RAGWire OpenAI-Compatible API", version="1.0.0")

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"])

app.include_router(router)

print(f"[RAGWire] Using agent: {agent.MODEL_ID}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)

#python v1main.py  -> avvia backend
#chainlit run v1app.py -w   ->avvia frontend (Chainlit)

