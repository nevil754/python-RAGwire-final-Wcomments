
from langchain.tools import tool

from ragwire import RAGWire
import ragwire
from typing import Optional #per parametro che puo esserci o no

CONFIG_PATH = "./v1config.yaml"
rag = RAGWire(CONFIG_PATH)

@tool
def get_filter_context(query: str)-> str:
    """Get available metadata fields and filter suggestions for a query.
    Call this first when the user mentions a company name, year, or document type."""
    return rag.get_filter_context(query)  #estrae e.g. {"company": "Tesla", "year": 2022,"doc_type": "annual_report"}

@tool
def search_documents(query:str, filters: Optional[dict] = None)-> str:  #x passare filtri metadata e.g.{ "company": "Tesla","year": 2023}
    """Search the document knowledge base and return relevant text chunks.
    For multi-company comparisons, call once per company. For multi-year analyses, call once per year.
    Each call should be a focused query targeting one company, one year, or one specific aspect."""
    results = rag.retrieve(query, top_k=5, filters=filters)  #retrieve, top 5 chunks
    if not results:
        return "No relevant documents found"
    chunks = [  #crei list
        f"[{doc.metadata.get('file_name', 'unknown')}]\n{doc.page_content}"
        for doc in results
    ]
    return "\n\n---\n\n".join(chunks)  #return una stringa con 2 spaces a capo tra ogni chunks
