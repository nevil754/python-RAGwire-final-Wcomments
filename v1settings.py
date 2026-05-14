
import os
import yaml  #x read files .yaml
from dotenv import load_dotenv  #x read .env files
load_dotenv()

def load_config(path="v1config.yaml"):
    with open(path, "r") as f:   #apre file in modalita only lettura(read), ovviamente con w gestisce automaticamente apertura e shutdown
        return yaml.safe_load(f)

config = load_config()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL_OLLAMA = os.getenv("BASE_URL_OLLAMA")

def get_llm():
    provider = config["llm"]["provider"]
    model = config["llm"]["model"]
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=OPENAI_API_KEY,
            temperature=0.2  #x quanto è creativo il modello, >1 diventa tanto random la risposta.
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=GOOGLE_API_KEY,
            temperature=0.2
        )
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            temperature=0.2,
            base_url=BASE_URL_OLLAMA
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
    
#ora in v1config.yaml puoi cambiare 'provider' e 'model' come vuoi ( e.g. openai  e gpt-4.1-mini) (dentro il config.yaml) e quindi automaticamente verranno scaricati/settati auto in questo file.  poi in fies target fai semplicemente  llm = get_llm()  