"""内集卡调度接口：维护内集卡，覆盖派发任务、完成归队、登记维修等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.truck import TruckService

router = APIRouter(prefix="/api/truck", tags=["内集卡调度"])

service = TruckService()

LIST_FIELDS = [
    "集卡编号", "车牌号码", "所属车队", "当前任务", "当前位置",
    "司机姓名", "燃油余量", "连续作业时长", "维修截止日", "派车单号", "派车状态",
]
STATUSES = ["待命", "执行中", "维修中", "已报废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按集卡编号检索"),
    status: str | None = Query(default=None, description="待命、执行中、维修中、已报废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按集卡编号与状态过滤内集卡调度列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/board")
def dispatch_board() -> dict[str, Any]:
    """派车看板：可用车数随派车单实时重算，附超时待归队与不可派清单。"""
    return service.board()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出内集卡调度清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "truck", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条内集卡明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"内集卡 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条内集卡，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="内集卡已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条内集卡执行派发任务、完成归队、登记维修、维修放行、车辆报废；不允许的动作会被拦下并说明原因。"""
    values = dict(payload.values)
    action = str(values.pop("action", "") or "").strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
