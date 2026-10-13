"""/api/data 라우터."""

from fastapi import APIRouter, Depends, Query, Request, Response

from ..models import DataCreate, DataList, DataOut, DataUpdate, RecordType, Summary
from ..service import DataService

router = APIRouter(prefix="/data", tags=["data"])


def get_service(request: Request) -> DataService:
    return request.app.state.service


# /summary 는 /{doc_id} 보다 먼저 선언해야 한다
@router.get("/summary", response_model=Summary, summary="현재 데이터 통계 요약")
def summary(run_id: str | None = None, metric: str | None = None,
            svc: DataService = Depends(get_service)):
    return svc.summary(run_id=run_id, metric=metric)


@router.get("", response_model=DataList, summary="데이터 목록 (date 오름차순)")
def list_data(run_id: str | None = None, metric: str | None = None,
              record_type: RecordType | None = None,
              limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
              svc: DataService = Depends(get_service)):
    items, total = svc.list(run_id=run_id, metric=metric, record_type=record_type, limit=limit, offset=offset)
    return DataList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DataOut, summary="데이터 1건")
def get_data(doc_id: str, svc: DataService = Depends(get_service)):
    return svc.get(doc_id)


@router.post("", response_model=DataOut, status_code=201, summary="manual 데이터 추가")
def create_data(body: DataCreate, svc: DataService = Depends(get_service)):
    return svc.create(body)


@router.put("/{doc_id}", response_model=DataOut, summary="manual 데이터 수정 (보낸 필드만)")
def update_data(doc_id: str, body: DataUpdate, svc: DataService = Depends(get_service)):
    return svc.update(doc_id, body)


@router.delete("/{doc_id}", status_code=204, summary="manual 데이터 삭제")
def delete_data(doc_id: str, svc: DataService = Depends(get_service)):
    svc.delete(doc_id)
    return Response(status_code=204)
