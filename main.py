from fastapi import FastAPI, HTTPException

from app.agent import SearchAgent
from app.schemas import QueryRequest, QueryResponse

app = FastAPI(title="Medical Booking Agentic Search", version="2.0.0")
agent = SearchAgent()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query_endpoint(payload: QueryRequest) -> QueryResponse:
    try:
        return agent.run(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
