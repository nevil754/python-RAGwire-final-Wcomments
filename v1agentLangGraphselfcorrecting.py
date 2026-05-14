

import re as _re
from typing import AsyncGenerator, List, Literal, TypedDict
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from v1settings import get_llm
from v1tools import rag

MODEL_ID = "ragwire-langgraph"
MAX_ITERATIONS = 3  #max 3 tentativi prima di arrendersi

SYSTEM_PROMPT = """
Answer precisely using only the provided context.
For multi-company or multi-year analyses, address each individually before forming a unified answer.
Cite the source document. Bold all specific numbers, percentages, dates, and key figures using **value**.
Never wrap your response in code blocks or backticks.
If you include a References section, format it as a numbered list: '1. filename, p.XX'
"""

#llm =ChatGoogleGenerativeAI(model="gemini-2.0-flash")
llm = get_llm()

class State(TypedDict):
    query: str
    current_query: str
    iteration: int
    context: str
    answer: str
    file_filter: dict

async def retrieve(state: State) -> State:
    if state["iteration"] == 0:  #se è il primo tenativo, usa filtri estratti dalla query /dal file. se è un retry(iteration>0) allora non applica filtri (quindi ricerca piu ampia)
        filters = state.get("file_filter") or rag.extract_filters(state["current_query"])
    else:
        filters = {}
    results = rag.retrieve(state["current_query"], filters=filters)  #RETRIEVE
    if results:
        context = "\n\n---\n\n".join(
            f"[{doc.metadata.get('file_name', 'unknown')}]\n{doc.page_content}"
            for doc in results
        )  #1 result per documento, con nome file in evidenza. Se non c'è il nome del file, si usa "unknown"
    else:
        context = ""
    return {**state, "context": context}  #update stato shared

async def generate(state:State)->State:
    if not state["context"]:
        if state["iteration"] >= MAX_ITERATIONS: 
            return {**state, "answer": "The documents don't contain sufficient information to answer this question confidently."}
        return {**state, "answer":""}
    result = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(f"Context:\n{state['context']}\n\nQuestion: {state['query']}")])  #invoke ma questa volta async
    return {**state, "answer": result.content}

async def rewrite(state: State)->State:
    result = await llm.ainvoke([HumanMessage(f"The search query did not return useful results.\nOriginal question: {state['query']}\nCurrent query: {state['current_query']}\n\nWrite a better, more specific search query. Respond with the query only.")])
    return {
        **state,
        "current_query": result.content.strip(),
        "iteration": state["iteration"]+1,
        "context": "",
        "answer": ""
    }  #update stato shared

def should_retry(state:State)-> Literal["rewrite", "done"]:
    if state["answer"]:
        return "done"
    return "rewrite"

def build_graph():
    graph = StateGraph(State)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("rewrite", rewrite)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate") #flusso base  
    graph.add_conditional_edges("generate", should_retry, {"rewrite":"rewrite", "done":END})
    graph.add_edge("rewrite","retrieve")  #loop
    return graph.compile()

graph = build_graph()

async def stream(messages: List[dict]) -> AsyncGenerator[str, None]:
    raw = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    file_match = _re.search(r'\[uploaded_files: ([^\]]+)\]', raw)
    file_filter: dict = {}
    if file_match:
        names = [n.strip() for n in file_match.group(1).split(',')]  #estrazione nomi file da filtro, se presente. Se c'è più di un nome, non applico il filtro (quindi ricerca più ampia), altrimenti filtro per quel file specifico
        if len(names) == 1:
            file_filter = {"file_name": names[0]}
    query = _re.sub(r'\s*\[uploaded_files: [^\]]+\]', '', raw).strip()
    initial_state = State(query=query, current_query=query, iteration=0, context="", answer="", file_filter=file_filter)  #stato iniziale con query, filtro file (se presente) e campi vuoti per contesto e risposta
    yielded_any = False
    fallback_answer = ""
    async for event in graph.astream_events(initial_state, version="v1"):
        if event["event"] == "on_chain_end" and event.get("name") == "generate":
            output = event["data"].get("output", {})  #
            if isinstance(output, dict):
                fallback_answer = output.get("answer", "")
        elif event["event"] == "on_chat_model_stream" and event.get("metadata", {}).get("langgraph_node") == "generate":  #streaming della risposta generata dal nodo "generate"
            chunk = event["data"]["chunk"]
            if chunk.content:
                yielded_any = True
                yield chunk.content
    if not yielded_any and fallback_answer:
        yield fallback_answer