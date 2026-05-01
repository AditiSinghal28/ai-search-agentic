from fastapi import FastAPI, HTTPException, Query

from app.agent import SearchAgent
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas import FeedbackRequest, FeedbackResponse, QueryRequest, QueryResponse

app = FastAPI(title="Medical Booking Agentic Search", version="3.0.0")
agent = SearchAgent()
repo = AnalyticsRepository()


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


@app.post("/feedback", response_model=FeedbackResponse)
def feedback_endpoint(payload: FeedbackRequest) -> FeedbackResponse:
    try:
        feedback_id = repo.insert_feedback(**payload.model_dump())
        return FeedbackResponse(id=feedback_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save feedback: {exc}")


@app.get("/logs")
def logs_endpoint(
    hospital_id: int,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    try:
        return {"logs": repo.list_recent_logs(hospital_id=hospital_id, status=status, limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read logs: {exc}")
