"""内集卡调度业务规则：派车底线、派车单、状态口径都收在这里。

派车底线（按顺序拦下，任一不满足都不能派发，原因原样返回）：
1. 已报废的车不参与派发；
2. 车辆在维修期内不能派发——油量与维修期冲突时，一律以维修期为准；
3. 燃油余量低于本车队自定下限不能派发（各车队按自家尺度）；
4. 已连续作业超过上限（默认 8 小时）的车先安排归队，不再派新任务；
5. 同一辆车、同一趟次重复提交只算一次（幂等），不产生新派车单。

车牌号码、司机姓名以车辆主档为唯一来源，列表与详情统一走 ``serialize``，
避免两处读出的内容对不上。看板里的可用车数每次都按未完成派车单现算。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "truck"
DISPATCH_MODULE = "_truck_dispatch"
REQUIRED_FIELDS = ["集卡编号", "车牌号码", "所属车队"]
STATUS_ORDER = ["待命", "执行中", "维修中", "已报废"]
SCRAPPED = "已报废"
REPAIRING = "维修中"
RUNNING = "执行中"

# 各车队按自家尺度定的燃油下限（百分比）；未列出的车队走默认值
FLEET_FUEL_FLOORS: dict[str, int] = {"一队": 20, "二队": 25, "三队": 15}
DEFAULT_FUEL_FLOOR = 20
# 连续作业上限（小时）：超过就先安排归队，不派新任务
OVERTIME_LIMIT_HOURS = 8
# 登记维修时不传截止时间，默认维修时长（小时）
DEFAULT_REPAIR_HOURS = 24
LIST_FIELDS = ["集卡编号", "车牌号码", "所属车队", "当前任务", "当前位置", "司机姓名", "燃油余量", "集卡状态"]


def _parse_dt(value: Any) -> datetime | None:
    """把 ISO 格式字符串解析成时间；空值或解析不了返回 None。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_fuel(value: Any) -> float | None:
    """燃油余量按百分比数值处理；兼容「62%」这类字符串，解析不了返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%").strip()
    try:
        return float(text)
    except ValueError:
        return None


class TruckService:
    # ---------- 派车单 ----------
    def _dispatches(self) -> list[dict[str, Any]]:
        return store.rows(DISPATCH_MODULE)

    def _open_dispatch(self, truck_id: int) -> dict[str, Any] | None:
        """该车当前未归队的派车单（同一辆车最多一条未完成单）。"""
        for order in self._dispatches():
            if int(order.get("卡车id", 0)) == truck_id and not order.get("结束时间"):
                return order
        return None

    # ---------- 车况判定 ----------
    def _repair_deadline(self, entry: dict[str, Any]) -> datetime | None:
        return _parse_dt(entry.get("维修截止"))

    def _in_repair(self, entry: dict[str, Any], *, now: datetime | None = None) -> bool:
        """是否还在维修期内；维修截止时间已过即视为维修期结束。"""
        deadline = self._repair_deadline(entry)
        return deadline is not None and deadline > (now or datetime.now())

    def _canonical_status(self, entry: dict[str, Any], *, now: datetime | None = None) -> str:
        """以车辆主档和派车单为准的派车状态，列表和详情都读这一个口径。

        报废优先；其次维修期（含历史遗留的“维修中”且没到期）；
        有未归队派车单即为执行中；维修期已过的老状态自动恢复待命。
        """
        if entry.get("status") == SCRAPPED:
            return SCRAPPED
        current = now or datetime.now()
        if self._in_repair(entry, now=current) or (
            entry.get("status") == REPAIRING and self._repair_deadline(entry) is None
        ):
            return REPAIRING
        if self._open_dispatch(int(entry.get("id", 0))):
            return RUNNING
        return "待命"

    def _fuel_floor(self, entry: dict[str, Any]) -> int:
        fleet = str(entry.get("所属车队") or "").strip()
        return FLEET_FUEL_FLOORS.get(fleet, DEFAULT_FUEL_FLOOR)

    def _working_hours(self, order: dict[str, Any], *, now: datetime | None = None) -> float | None:
        started = _parse_dt(order.get("开始时间"))
        if started is None:
            return None
        return round(((now or datetime.now()) - started).total_seconds() / 3600, 1)

    def _dispatch_blocker(
        self, entry: dict[str, Any], *, now: datetime | None = None
    ) -> str | None:
        """派发任务的底线检查；返回非空字符串即为拦下原因，None 表示可以派发。"""
        current = now or datetime.now()
        truck_id = int(entry.get("id", 0))
        plate = entry.get("车牌号码") or "该集卡"

        if self._canonical_status(entry, now=current) == SCRAPPED:
            return f"{plate} 已报废，报废车辆不参与派发"

        # 油量与维修期冲突时以维修期为准，所以维修期先判
        if self._in_repair(entry, now=current):
            deadline = self._repair_deadline(entry)
            until = deadline.strftime("%Y-%m-%d %H:%M") if deadline else "待定"
            return f"{plate} 仍在维修期内（预计修到 {until}），维修期内不能派发任务"

        fuel = _parse_fuel(entry.get("燃油余量"))
        floor = self._fuel_floor(entry)
        if fuel is None:
            return f"{plate} 的燃油余量缺失或无法识别（当前：{entry.get('燃油余量')!s}），无法判定能否派发，请先补登油量"
        if fuel < floor:
            return (
                f"{plate} 燃油余量 {fuel:g}% 低于{entry.get('所属车队') or '本队'}派车下限 {floor}%，"
                "不能派发任务，请先加油"
            )

        open_order = self._open_dispatch(truck_id)
        if open_order:
            hours = self._working_hours(open_order, now=current)
            if hours is not None and hours > OVERTIME_LIMIT_HOURS:
                return (
                    f"{plate} 已连续作业 {hours:g} 小时（超过 {OVERTIME_LIMIT_HOURS} 小时上限），"
                    "请先安排完成归队，再派新任务"
                )
            return f"{plate} 当前已有未完成任务（趟次 {open_order.get('趟次编号') or '—'}），请先归队再派新任务"
        return None

    def _find_dispatch(self, truck_id: int, trip_no: str) -> dict[str, Any] | None:
        """同一辆车的同一趟次派车单（含已归队的历史单），用于幂等去重。"""
        for order in self._dispatches():
            if int(order.get("卡车id", 0)) == truck_id and str(order.get("趟次编号") or "") == trip_no:
                return order
        return None

    # ---------- 序列化：列表/详情唯一出口 ----------
    def serialize(self, entry: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """统一组装对外字段：车牌、司机只从主档读，派车状态只按派车单算。"""
        current = now or datetime.now()
        open_order = self._open_dispatch(int(entry.get("id", 0)))
        status = self._canonical_status(entry, now=current)
        hours = self._working_hours(open_order, now=current) if open_order else None
        overtime = bool(hours is not None and hours > OVERTIME_LIMIT_HOURS)
        fuel = _parse_fuel(entry.get("燃油余量"))
        fuel_text = f"{fuel:g}%" if fuel is not None else str(entry.get("燃油余量") or "—")

        result = {
            "id": entry.get("id"),
            "集卡编号": entry.get("集卡编号", ""),
            "车牌号码": entry.get("车牌号码", ""),
            "所属车队": entry.get("所属车队", ""),
            "当前任务": (open_order or {}).get("任务说明") or "",
            "当前位置": entry.get("当前位置", ""),
            "司机姓名": entry.get("司机姓名", ""),
            "燃油余量": fuel_text,
            "集卡状态": status,
            "派车状态": status,
            "连续作业小时": hours,
            "超时连续作业": overtime,
            "油量下限": self._fuel_floor(entry),
            "维修截止": entry.get("维修截止"),
            "趟次编号": (open_order or {}).get("趟次编号") or "",
        }
        return result

    def serialize_dispatch(self, order: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """派车单明细；车牌、司机回填车辆主档，保证与列表/详情一致。"""
        current = now or datetime.now()
        truck = store.find(MODULE, int(order.get("卡车id", 0)))
        hours = self._working_hours(order, now=current)
        return {
            "id": order.get("id"),
            "卡车id": order.get("卡车id"),
            "集卡编号": (truck or {}).get("集卡编号", ""),
            "车牌号码": (truck or {}).get("车牌号码", ""),
            "所属车队": (truck or {}).get("所属车队", ""),
            "司机姓名": (truck or {}).get("司机姓名", ""),
            "趟次编号": order.get("趟次编号", ""),
            "任务说明": order.get("任务说明", ""),
            "开始时间": order.get("开始时间"),
            "结束时间": order.get("结束时间"),
            "已归队": bool(order.get("结束时间")),
            "连续作业小时": hours,
            "超时连续作业": bool(hours is not None and hours > OVERTIME_LIMIT_HOURS),
            "归队备注": order.get("归队备注", ""),
        }

    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        current = datetime.now()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("集卡编号", ""))]
        if status:
            # 状态过滤也走统一口径，避免列表过滤状态和行内派车状态对不上
            rows = [row for row in rows if self._canonical_status(row, now=current) == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.serialize(row, now=current) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        current = datetime.now()
        result = self.serialize(entry, now=current)
        result["派车单"] = [
            self.serialize_dispatch(order, now=current)
            for order in self._dispatches()
            if int(order.get("卡车id", 0)) == entry_id
        ]
        return result

    def board(self) -> dict[str, Any]:
        """派车看板：可用车数等指标每次都按当前派车单和车况现算。"""
        current = datetime.now()
        rows = store.rows(MODULE)
        serialised = [self.serialize(row, now=current) for row in rows]
        available = [item for item in serialised if item["集卡状态"] == "待命"]
        fleets: dict[str, dict[str, Any]] = {}
        for item in serialised:
            fleet_name = item["所属车队"] or "未分配车队"
            bucket = fleets.setdefault(
                fleet_name,
                {"车队": fleet_name, "总数": 0, "可用": 0, "执行中": 0, "维修中": 0, "已报废": 0,
                 "油量下限": FLEET_FUEL_FLOORS.get(fleet_name, DEFAULT_FUEL_FLOOR)},
            )
            bucket["总数"] += 1
            if item["集卡状态"] in ("执行中", "维修中", "已报废"):
                bucket[item["集卡状态"]] += 1
            if item["集卡状态"] == "待命":
                bucket["可用"] += 1
        return {
            "连续作业上限小时": OVERTIME_LIMIT_HOURS,
            "cards": [
                {"label": "可用集卡", "value": len(available)},
                {"label": "执行中", "value": sum(1 for item in serialised if item["集卡状态"] == "执行中")},
                {"label": "维修中", "value": sum(1 for item in serialised if item["集卡状态"] == "维修中")},
                {"label": "已报废", "value": sum(1 for item in serialised if item["集卡状态"] == "已报废")},
                {"label": "超时待归队", "value": sum(1 for item in serialised if item["超时连续作业"])},
            ],
            "available_trucks": available,
            "fleets": sorted(fleets.values(), key=lambda item: item["车队"]),
        }

    # ---------- 写入 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 车牌、司机等登记字段原样落库（含可空字段），避免司机姓名整条丢失
        for field in LIST_FIELDS:
            if field == "集卡状态":
                continue  # 派车状态只能由派车单/维修期推导，不接受登记时写死
            entry[field] = values.get(field, "")
        entry["status"] = "待命"
        entry["pending"] = True
        entry["abnormal"] = False
        deadline = _parse_dt(values.get("维修截止"))
        if deadline is not None:
            entry["维修截止"] = deadline.isoformat()
            entry["status"] = REPAIRING
            entry["pending"] = False
        rows.append(entry)
        return self.serialize(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"内集卡 {entry_id} 不存在或已归档"
        action = str(action or "").strip()
        if action == "派发任务":
            return self._dispatch(entry, values)
        if action == "完成归队":
            return self._return(entry, values)
        if action == "登记维修":
            return self._register_repair(entry, values)
        return None, f"动作「{action}」不属于内集卡调度可执行范围"

    def _dispatch(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        trip_no = str(values.get("趟次编号") or "").strip()
        if not trip_no:
            return None, "派发任务必须填写趟次编号，用于同趟次去重"
        task_desc = str(values.get("任务说明") or values.get("当前任务") or "").strip()
        now = datetime.now()

        # 同车同趟次重复提交一律幂等：不产生新派车单，重复提交算成功，原样带回已有单
        existing = self._find_dispatch(int(entry.get("id", 0)), trip_no)
        if existing is not None:
            state = "已在执行，" if not existing.get("结束时间") else "已完成归队，"
            return self.serialize(entry, now=now), (
                f"{entry.get('车牌号码')} 的趟次 {trip_no} {state}同一趟次派车只算一次"
            )

        blocker = self._dispatch_blocker(entry, now=now)
        if blocker:
            return None, blocker

        # 司机以车辆主档为准；派车时显式指定了司机才更新主档，两处始终读到同一人
        driver = str(values.get("司机姓名") or "").strip()
        if driver:
            entry["司机姓名"] = driver
        location = str(values.get("当前位置") or "").strip()
        if location:
            entry["当前位置"] = location

        orders = self._dispatches()
        order = {
            "id": max((int(row.get("id", 0)) for row in orders), default=0) + 1,
            "卡车id": int(entry.get("id", 0)),
            "趟次编号": trip_no,
            "任务说明": task_desc or f"趟次 {trip_no}",
            "司机姓名": entry.get("司机姓名", ""),
            "车牌号码": entry.get("车牌号码", ""),
            "开始时间": now.replace(microsecond=0).isoformat(),
            "结束时间": None,
            "归队备注": "",
        }
        orders.append(order)
        entry["status"] = RUNNING
        entry["当前任务"] = order["任务说明"]
        entry["pending"] = True
        entry["abnormal"] = False
        entry.pop("维修截止", None)
        return self.serialize(entry, now=now), f"已派车：{entry.get('车牌号码')} 承担趟次 {trip_no}"

    def _return(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        now = datetime.now()
        order = self._open_dispatch(int(entry.get("id", 0)))
        if order is None:
            # 维修期内 / 报废的车也不允许走归队动作
            status = self._canonical_status(entry, now=now)
            if status == REPAIRING:
                return None, f"{entry.get('车牌号码') or '该集卡'} 仍在维修期内，不能办理归队"
            if status == SCRAPPED:
                return None, f"{entry.get('车牌号码') or '该集卡'} 已报废，无需归队"
            return None, f"{entry.get('车牌号码') or '该集卡'} 当前没有在执行的任务，无需归队"
        overtime = self._working_hours(order, now=now)
        order["结束时间"] = now.replace(microsecond=0).isoformat()
        order["归队备注"] = str(values.get("归队备注") or "").strip()
        entry["status"] = "待命"
        entry["当前任务"] = ""
        entry["pending"] = True
        entry["abnormal"] = False
        suffix = ""
        if overtime is not None and overtime > OVERTIME_LIMIT_HOURS:
            suffix = f"（连续作业 {overtime:g} 小时，已超时，归队后优先休整）"
        return self.serialize(entry, now=now), f"{entry.get('车牌号码')} 已完成归队{suffix}"

    def _register_repair(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        now = datetime.now()
        if entry.get("status") == SCRAPPED:
            return None, f"{entry.get('车牌号码') or '该集卡'} 已报废，不再登记维修"
        open_order = self._open_dispatch(int(entry.get("id", 0)))
        if open_order:
            return None, (
                f"{entry.get('车牌号码') or '该集卡'} 正在执行趟次 "
                f"{open_order.get('趟次编号') or '—'}，请先完成归队再登记维修"
            )
        deadline = _parse_dt(values.get("维修截止"))
        if deadline is None:
            hours_raw = values.get("维修时长") or DEFAULT_REPAIR_HOURS
            try:
                hours = float(hours_raw)
            except (TypeError, ValueError):
                hours = DEFAULT_REPAIR_HOURS
            deadline = now + timedelta(hours=hours)
        if deadline <= now:
            return None, "维修截止时间必须晚于当前时间，请重新登记"
        entry["维修截止"] = deadline.replace(microsecond=0).isoformat()
        entry["status"] = REPAIRING
        entry["当前任务"] = ""
        entry["pending"] = False
        entry["abnormal"] = False
        return self.serialize(entry, now=now), (
            f"{entry.get('车牌号码')} 已登记维修，预计 {deadline.strftime('%Y-%m-%d %H:%M')} 完工"
        )

    def recall_overtime(self) -> dict[str, Any]:
        """超时连续作业的车先安排归队：批量把超时未归队的派车单置为归队。"""
        now = datetime.now()
        recalled: list[dict[str, Any]] = []
        for entry in list(store.rows(MODULE)):
            order = self._open_dispatch(int(entry.get("id", 0)))
            if order is None:
                continue
            hours = self._working_hours(order, now=now)
            if hours is not None and hours > OVERTIME_LIMIT_HOURS:
                order["结束时间"] = now.replace(microsecond=0).isoformat()
                order["归队备注"] = f"连续作业 {hours:g} 小时超时，系统安排优先归队"
                entry["status"] = "待命"
                entry["当前任务"] = ""
                entry["pending"] = True
                recalled.append(self.serialize(entry, now=now))
        return {
            "count": len(recalled),
            "message": f"已安排 {len(recalled)} 辆超时集卡归队" if recalled else "当前没有超时连续作业的集卡",
            "trucks": recalled,
        }
