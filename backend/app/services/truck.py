"""内集卡调度业务规则：派车底线、状态流转、字段校验与筛选口径都收在这里。

派车底线（按判定先后，命中即拦，缘由原样带出）：
1. 已报废车辆一律不参与派发；
2. 车辆在维修期内（含已到维修截止日但未放行）不能派发，维修期与油量冲突时以维修期为准；
3. 车辆已有执行中的任务（已有有效派车单）不能重复派发，同一辆车同一条任务重复提交只算一次；
4. 燃油余量低于所属车队自定的油量下限不能派发；
5. 连续作业时长超过所属车队自定的作业时限，必须先完成归队才能再次承接任务。

每个车队按自家的尺度判定；未配置车队尺度时走平台默认值。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "truck"
ORDERS_MODULE = "_truck_orders"
TODAY = date(2026, 9, 30)

REQUIRED_FIELDS = ["集卡编号", "车牌号码", "所属车队"]
# 列表与详情共用同一份投影，保证两处读到的车牌号码、司机、派车状态完全一致
VIEW_FIELDS = [
    "id", "集卡编号", "车牌号码", "所属车队", "当前任务", "当前位置",
    "司机姓名", "燃油余量", "连续作业时长", "维修截止日", "派车单号",
]

STATUS_ORDER = ["待命", "执行中", "维修中", "已报废"]
ACTION_RULES = {
    "派发任务": "执行中",
    "完成归队": "待命",
    "登记维修": "维修中",
    "维修放行": "待命",
    "车辆报废": "已报废",
}

# 平台默认尺度：油量下限 25%，连续作业时限 8 小时
DEFAULT_FLEET_RULE = {"油量下限": 25, "连续作业时限": 8}
# 各车队自家尺度
FLEET_RULES: dict[str, dict[str, int]] = {
    "一队": {"油量下限": 20, "连续作业时限": 12},
    "二队": {"油量下限": 30, "连续作业时限": 10},
}


def _to_int(value: Any, default: int = 0) -> int:
    """把燃油余量、连续作业时长这类字段稳妥解析成数字，解析不了按 0 处理。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _active_orders() -> list[dict[str, Any]]:
    return [order for order in store.rows(ORDERS_MODULE) if order.get("状态") == "执行中"]


class TruckService:
    # ---------- 读取口径 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("集卡编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._to_view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._to_view(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 业务字段按提交内容原样落库，不再只留必填字段——司机姓名等字段不能整条丢失
        for field in ("集卡编号", "车牌号码", "所属车队", "当前任务", "当前位置",
                      "司机姓名", "燃油余量", "连续作业时长", "维修截止日"):
            if field in values:
                entry[field] = values[field]
        entry.setdefault("当前任务", "")
        entry.setdefault("当前位置", "")
        entry.setdefault("司机姓名", "")
        entry.setdefault("燃油余量", 0)
        entry.setdefault("连续作业时长", 0)
        entry.setdefault("维修截止日", "")
        entry["派车单号"] = ""
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._to_view(entry), []

    # ---------- 状态流转 ----------

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"内集卡 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于内集卡调度可执行范围"
        values = values or {}

        if action == "派发任务":
            return self._dispatch(entry, values)
        if action == "完成归队":
            return self._return(entry)
        if action == "登记维修":
            return self._register_repair(entry, values)
        if action == "维修放行":
            return self._release_repair(entry)
        if action == "车辆报废":
            return self._scrap(entry)
        return None, f"动作「{action}」不属于内集卡调度可执行范围"

    def _dispatch(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        task_id = str(values.get("任务编号") or values.get("当前任务") or "").strip()
        task_name = str(values.get("任务名称") or "").strip()
        if not task_id:
            return None, "派发任务缺少必填信息：任务编号"

        # 幂等优先：同一辆车同一条任务重复提交，不管车况如何都只算一次
        existing = next(
            (order for order in _active_orders()
             if order.get("集卡编号") == entry.get("集卡编号")),
            None,
        )
        if existing is not None:
            if existing.get("任务编号") == task_id:
                return self._to_view(entry), (
                    f"该任务已派发，派车单号 {existing.get('派车单号')}，无需重复派车"
                )
            return None, (
                f"车辆已在执行任务 {existing.get('任务编号')}（派车单号 "
                f"{existing.get('派车单号')}），请先完成归队"
            )

        reason = self.dispatch_block_reason(entry)
        if reason:
            return None, reason
        order = self._create_order(entry, task_id, task_name)
        return self._to_view(entry), f"派车成功，派车单号 {order['派车单号']}"

    def _create_order(
        self, entry: dict[str, Any], task_id: str, task_name: str
    ) -> dict[str, Any]:
        orders = store.rows(ORDERS_MODULE)
        order_id = max((int(row.get("id", 0)) for row in orders), default=0) + 1
        order_no = f"DISP-{1000 + order_id}"
        full_task = f"{task_id} {task_name}".strip()
        order = {
            "id": order_id,
            "派车单号": order_no,
            "集卡编号": entry.get("集卡编号"),
            "车牌号码": entry.get("车牌号码"),
            "司机姓名": entry.get("司机姓名"),
            "所属车队": entry.get("所属车队"),
            "任务编号": task_id,
            "任务名称": task_name,
            "派发时间": TODAY.isoformat(),
            "状态": "执行中",
        }
        orders.append(order)
        entry["status"] = "执行中"
        entry["当前任务"] = full_task
        entry["派车单号"] = order_no
        entry.setdefault("连续作业时长", 0)
        entry["pending"] = True
        entry["abnormal"] = False
        return order

    def _return(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") != "执行中":
            return None, f"车辆当前为「{entry.get('status')}」，没有执行中的任务，无需归队"
        order = next(
            (item for item in _active_orders() if item.get("集卡编号") == entry.get("集卡编号")),
            None,
        )
        if order is not None:
            order["状态"] = "已归队"
        entry["status"] = "待命"
        entry["当前任务"] = ""
        entry["派车单号"] = ""
        entry["连续作业时长"] = 0
        entry["pending"] = True
        entry["abnormal"] = False
        return self._to_view(entry), "车辆已完成归队，连续作业时长已清零"

    def _register_repair(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") == "执行中":
            return None, "车辆执行任务中，请先完成归队再登记维修"
        if entry.get("status") == "已报废":
            return None, "已报废车辆不能再登记维修"
        deadline = str(values.get("维修截止日") or entry.get("维修截止日") or "").strip()
        if not deadline:
            return None, "登记维修缺少必填信息：维修截止日"
        entry["status"] = "维修中"
        entry["维修截止日"] = deadline
        entry["当前任务"] = ""
        entry["派车单号"] = ""
        entry["连续作业时长"] = 0
        entry["pending"] = False
        entry["abnormal"] = True
        return self._to_view(entry), f"已登记维修，维修截止日 {deadline}，期间不予派车"

    def _release_repair(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") != "维修中":
            return None, f"车辆当前为「{entry.get('status')}」，不在维修期，不能放行"
        entry["status"] = "待命"
        entry["维修截止日"] = ""
        entry["pending"] = True
        entry["abnormal"] = False
        return self._to_view(entry), "维修已放行，车辆回到待命，可重新承接派发"

    def _scrap(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") == "执行中":
            return None, "车辆执行任务中，请先完成归队再办理报废"
        entry["status"] = "已报废"
        entry["当前任务"] = ""
        entry["派车单号"] = ""
        entry["连续作业时长"] = 0
        entry["pending"] = False
        entry["abnormal"] = False
        return self._to_view(entry), "车辆已报废，后续不再参与派发"

    # ---------- 派车看板 ----------

    def dispatch_block_reason(self, entry: dict[str, Any]) -> str:
        """返回不能派发的缘由；可以派发时返回空串。维修期优先于油量判定。"""
        fleet = str(entry.get("所属车队") or "")
        rule = FLEET_RULES.get(fleet, DEFAULT_FLEET_RULE)
        fuel_limit = int(rule["油量下限"])
        work_limit = int(rule["连续作业时限"])
        fleet_desc = f"{fleet}尺度" if fleet in FLEET_RULES else "平台默认尺度"
        status = str(entry.get("status") or "")

        if status == "已报废":
            return "已报废车辆不参与派发"
        if status == "执行中":
            # 正常情况下执行中必有有效派车单（幂等分支会先命中）；这里只兜异常数据
            return "车辆已在执行任务，请先完成归队"
        # 维修期：状态为维修中，或维修截止日未过（含当天未放行）
        deadline = str(entry.get("维修截止日") or "").strip()
        in_repair = status == "维修中"
        if not in_repair and deadline:
            try:
                in_repair = date.fromisoformat(deadline) >= TODAY
            except ValueError:
                in_repair = False
        if in_repair:
            till = f"，维修截止日 {deadline}" if deadline else ""
            return f"车辆在维修期内{till}，维修期内不能派发"
        fuel = _to_int(entry.get("燃油余量"))
        if fuel < fuel_limit:
            # 维修期检查在油量之前，冲突时缘由只会报维修期
            return f"燃油余量 {fuel}% 低于{fleet_desc}油量下限 {fuel_limit}%，加油前不能派发"
        worked = _to_int(entry.get("连续作业时长"))
        if worked > work_limit:
            return (
                f"已连续作业 {worked} 小时，超过{fleet_desc}时限 {work_limit} 小时，"
                "请先安排完成归队"
            )
        return ""

    def board(self) -> dict[str, Any]:
        """派车看板：可用车数随派车单实时重算，并给出超时归队与不可派清单。"""
        rows = store.rows(MODULE)
        active = _active_orders()
        active_codes = {order.get("集卡编号") for order in active}

        status_count = {name: 0 for name in STATUS_ORDER}
        available: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        overtime: list[dict[str, Any]] = []

        for row in rows:
            status = str(row.get("status") or "")
            status_count[status] = status_count.get(status, 0) + 1
            code = row.get("集卡编号")
            view = self._to_view(row)
            if status == "执行中" or code in active_codes:
                rule = FLEET_RULES.get(str(row.get("所属车队") or ""), DEFAULT_FLEET_RULE)
                worked = _to_int(row.get("连续作业时长"))
                if worked > int(rule["连续作业时限"]):
                    overtime.append(view)
                continue
            reason = self.dispatch_block_reason(row)
            if reason:
                view["不可派缘由"] = reason
                blocked.append(view)
            else:
                available.append(view)

        overtime.sort(key=lambda item: _to_int(item.get("连续作业时长")), reverse=True)
        blocked.sort(key=lambda item: str(item.get("集卡编号")))

        return {
            "cards": [
                {"label": "车队总数", "value": len(rows)},
                {"label": "可用车数", "value": len(available)},
                {"label": "在途任务", "value": len(active)},
                {"label": "超时待归队", "value": len(overtime)},
                {"label": "维修中", "value": status_count.get("维修中", 0)},
                {"label": "已报废", "value": status_count.get("已报废", 0)},
            ],
            "fleetRules": [
                {"所属车队": fleet, **rule} for fleet, rule in sorted(FLEET_RULES.items())
            ] + [{"所属车队": "其他车队（默认）", **DEFAULT_FLEET_RULE}],
            "available": available,
            "overtimeReturn": overtime,
            "blocked": blocked,
            "orders": [dict(order) for order in active],
        }

    # ---------- 统一投影 ----------

    def _to_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表与详情的唯一出口：派车状态只认 status，车牌、司机同源同值。"""
        view = {field: entry.get(field, "") for field in VIEW_FIELDS}
        view["status"] = entry.get("status")
        # 派车状态与车辆状态机同源，杜绝列表页、详情页两处对不上
        view["派车状态"] = self._dispatch_status(entry)
        return view

    def _dispatch_status(self, entry: dict[str, Any]) -> str:
        status = str(entry.get("status") or "")
        if status == "执行中":
            return "执行中"
        if status == "维修中":
            return "维修禁派"
        if status == "已报废":
            return "报废禁派"
        if status == "待命":
            return "可派发" if not self.dispatch_block_reason(entry) else "暂不可派"
        return status
