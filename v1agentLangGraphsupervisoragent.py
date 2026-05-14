
import re as _re
from typing import AsyncGenerator, Dict, List,  Literal, TypedDict

from langchain_core.messages import HumanMessage
#from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from v1tools import rag
from v1settings import get_llm

MODEL_ID = "ragwire-supervisor-agent"
#llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
llm = get_llm() 

SPECIALISTS = {
    "financial":  "revenue income profit margin financial statements cash flow",
    "legal_risk": "risk factors legal proceedings regulatory compliance liabilities",
    "technical":  "product technology research development innovation strategy",
    "summary":    "overview business strategy key highlights performance",
}

SUPERVISOR_PROMPT = """
You manage specialized document analysis agents.
Agents: financial | legal_risk | technical | summary
Query: {query}
Already called: {called}
Outputs so far: {outputs}
Which agent to call next, or FINISH if you have enough information?
Rules: do not repeat an agent; FINISH when sufficient.
Respond with one word only: financial | legal_risk | technical | summary | FINISH
"""

class State(TypedDict):
    query: str
    next_agent: str
    agent_outputs: Dict[str, str]
    final_answer: str
    iteration: int
    file_filter: dict

async def supervisor(state:State)->State:
    called = list(state["agent_outputs"].keys())
    outputs = "\n".join(f"- {k}: {v[:100]}..." for k, v in state["agent_outputs"].items()) or "none"
    result = await llm.ainvoke([HumanMessage(SUPERVISOR_PROMPT.format(query=state["query"], called=called or "none", outputs=outputs))])
    decision = result.content.strip().lower()
    next_agent = decision if decision in SPECIALISTS else "FINISH"
    return {
        **state,
        "next_agent": next_agent,
        "iteration": state["iteration"]+1
    }  #update stato shared

def make_specialist(name: str):
    focus = SPECIALISTS[name]
    async def node(state:State)->State:
        query = f"{focus} {state['query']}"
        filters = state.get("file_filter") or rag.extract_filters(query)
        results = rag.retrieve(query, filters=filters)
        if not results:
            output = f"No relevant {name} information found"
        else:
            context = "\n\n---\n\n".join(
                f"[{doc.metadata.get('file_name', 'unknown')}]\n{doc.page_content}"
                for doc in results
            )
            result = await llm.ainvoke([HumanMessage(f"You are a {name} specialist.\nAnswer using only the provided context. Bold all figures using **value**.\nNever wrap your response in code blocks or backticks.\n\nQuery: {state['query']}\n\nContext:\n{context}")])
            output = result.content
        return {
            **state,
            "agent_outputs": {**state["agent_outputs"], name: output}
        }
    return node

async def synthesize(state:State)->State:
    if not state["agent_outputs"]:
        return {**state, "final_answer": "No relevant information found."}
    combined = "\n\n".join(f"{k}: {v}" for k, v in state["agent_outputs"].items())
    result = await llm.ainvoke([HumanMessage(f"Synthesize these analyses into one comprehensive answer.\nBold all figures using **value**. Cite sources. Never use code blocks or backticks.\nReferences format: '1. filename, p.XX'\n\nQuery: {state['query']}\n\n{combined}")])
    return {**state, "final_answer": result.content}

def route(state:State)->Literal["financial","legal_risk","technical","summary","synthesize"]:
    if state["next_agent"] == "FINISH" or state["iteration"]>=4:
        return "synthesize"
    return state["next_agent"]

def build_graph():
    graph = StateGraph(State)
    graph.add_node("supervisor",supervisor)
    graph.add_node("synthesize", synthesize)
    for name in SPECIALISTS:
        graph.add_node(name, make_specialist(name))
        graph.add_edge(name, "supervisor")
    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route,
        {**{n: n for n in SPECIALISTS}, "synthesize": "synthesize"}, #è semplicemente che route() ritorna una str e.g."financial" e quindi {**{n: n for n in SPECIALISTS} vuol dire e.g."se route() ritorna "financial" vai a "financial"(preso da SPECIALISTS). infine "synthesize": "synthesize" vuol dire se route() ritorna "synthesize" vai a "synthesize", è solo un'aggiunta
    )
    graph.add_edge("synthesize", END)
    return graph.compile()

graph = build_graph()

async def stream(messages: List[dict])->AsyncGenerator[str, None]:
    raw = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    file_match = _re.search(r'\[uploaded_files: ([^\]]+)\]', raw)
    file_filter: dict = {}
    if file_match:
        names = [n.strip() for n in file_match.group(1).split(',')]  #sopo un match, group() è entire match, group(1) è quello dentro le parentesi, quindi i nomi dei file. split(',') per ottenere una lista di nomi, strip() per rimuovere spazi extra 
        if len(names) == 1:
            file_filter = {"file_name": names[0]}
    query = _re.sub(r'\s*\[uploaded_files: [^\]]+\]', '', raw).strip()
    yielded_any = False  #flag per sapere se abbiamo prodotto almeno un token o chunk dal modello.
    fallback_answer = ""
    async for event in graph.astream_events(State(query=query, next_agent="", agent_outputs={}, final_answer="", iteration=0, file_filter=file_filter), version="v1"):  #AVVIA GRAPH IN MODALITA STREAMING
        if event["event"] == "on_chain_end" and event.get("name") == "synthesize":  #se un nodo del grafo finisce (on_chain_end) ed è il nodo synthesize QUINDI NON FINITO CORRETTAMENTE FLOW
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                fallback_answer = output.get("final_answer", "")  #prendiamo l’output finale del grafo: final_answer
        elif event["event"] == "on_chat_model_stream" and event.get("metadata",{}).get("langgraph_node") == "synthesize":
            chunk = event["data"]["chunk"]
            if chunk.content:
                yielded_any = True
                yield chunk.content
    if not yielded_any and fallback_answer:
        yield fallback_answer



