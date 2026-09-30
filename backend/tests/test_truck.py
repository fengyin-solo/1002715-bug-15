"""内集卡派车规则自检：派车底线、车队尺度、幂等与看板口径。

直接运行：python3 -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.truck import FLEET_RULES, TruckService  # noqa: E402
from app.store import store  # noqa: E402


class TruckDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.service = TruckService()

    def find_id(self, code: str) -> int:
        row = next(item for item in store.rows("truck") if item.get("集卡编号") == code)
        return int(row["id"])

    def dispatch(self, code: str, task_id: str = "T-001", task_name: str = "测试任务"):
        return self.service.run_action(
            self.find_id(code), "派发任务", {"任务编号": task_id, "任务名称": task_name}
        )

    def test_initial_board_counts(self) -> None:
        board = self.service.board()
        cards = {item["label"]: item["value"] for item in board["cards"]}
        # 10 台车：2 台在途（其中 TRUC-0003 超时）、3 台维修、1 台报废、2 台可用
        self.assertEqual(cards["车队总数"], 10)
        self.assertEqual(cards["在途任务"], 2)
        self.assertEqual(cards["超时待归队"], 1)
        self.assertEqual(cards["维修中"], 3)
        self.assertEqual(cards["已报废"], 1)
        self.assertEqual(cards["可用车数"], 2)
        available_codes = {item["集卡编号"] for item in board["available"]}
        self.assertEqual(available_codes, {"TRUC-0001", "TRUC-0006"})

    def test_scrap_truck_cannot_dispatch(self) -> None:
        entry, message = self.dispatch("TRUC-0005")
        self.assertIsNone(entry)
        self.assertEqual(message, "已报废车辆不参与派发")

    def test_repair_period_beats_low_fuel(self) -> None:
        # TRUC-0009：油量 12%（低于默认 25%）且维修中，缘由必须报维修期
        entry, message = self.dispatch("TRUC-0009")
        self.assertIsNone(entry)
        self.assertIn("维修期内", message)
        self.assertIn("2026-10-01", message)

    def test_fleet_one_uses_own_fuel_limit(self) -> None:
        # 一队下限 20%：18% 被拦，缘由带一队尺度
        entry, message = self.dispatch("TRUC-0002")
        self.assertIsNone(entry)
        self.assertIn("一队尺度油量下限 20%", message)

    def test_fleet_two_uses_own_fuel_limit(self) -> None:
        # 二队在途车 TRUC-0007 油量 26%，若归队待命会被二队 30% 下限拦下
        self.service.run_action(self.find_id("TRUC-0007"), "完成归队")
        entry, message = self.dispatch("TRUC-0007", task_id="T-NEW")
        self.assertIsNone(entry)
        self.assertIn("二队尺度油量下限 30%", message)

    def test_unknown_fleet_uses_default_limit(self) -> None:
        # 三队未配置，走默认 25%：22% 被拦
        entry, message = self.dispatch("TRUC-0010")
        self.assertIsNone(entry)
        self.assertIn("平台默认尺度油量下限 25%", message)

    def test_overtime_truck_must_return_first(self) -> None:
        # TRUC-0003 已连续作业 13 小时，超一队 12 小时时限，进超时归队队列
        overtime = self.service.board()["overtimeReturn"]
        self.assertEqual([item["集卡编号"] for item in overtime], ["TRUC-0003"])
        # 它有有效派车单；换新任务派发必须先归队
        entry, message = self.dispatch("TRUC-0003", task_id="T-OTHER")
        self.assertIsNone(entry)
        self.assertIn("已在执行任务", message)
        # 先归队：作业时长清零、派车单失效
        entry, message = self.service.run_action(self.find_id("TRUC-0003"), "完成归队")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["连续作业时长"], 0)
        order = next(
            item for item in store.rows("_truck_orders")
            if item["派车单号"] == "DISP-1001"
        )
        self.assertEqual(order["状态"], "已归队")
        self.assertEqual(self.service.board()["cards"][3]["value"], 0)
        # 归队后同一任务重派可以成功（超时状态已解除）
        entry, message = self.dispatch("TRUC-0003", task_id="T-20260930-11")
        self.assertIsNotNone(entry)

    def test_duplicate_submit_is_idempotent(self) -> None:
        before = len(store.rows("_truck_orders"))
        # TRUC-0003 已有 DISP-1001 执行同任务，重复提交不新增记录
        entry, message = self.dispatch("TRUC-0003", task_id="T-20260930-11",
                                       task_name="后场箱区转运")
        self.assertIsNotNone(entry)
        self.assertEqual(len(store.rows("_truck_orders")), before)
        self.assertIn("DISP-1001", message)

    def test_available_count_recalculated_after_dispatch(self) -> None:
        self.assertEqual(self.service.board()["cards"][1]["value"], 2)
        entry, message = self.dispatch("TRUC-0001", task_id="T-20260930-20",
                                       task_name="前场接送箱")
        self.assertIsNotNone(entry)
        board = self.service.board()
        cards = {item["label"]: item["value"] for item in board["cards"]}
        # 派车后可用 -1、在途 +1，归队后恢复
        self.assertEqual(cards["可用车数"], 1)
        self.assertEqual(cards["在途任务"], 3)
        self.service.run_action(self.find_id("TRUC-0001"), "完成归队")
        board = self.service.board()
        cards = {item["label"]: item["value"] for item in board["cards"]}
        self.assertEqual(cards["可用车数"], 2)
        self.assertEqual(cards["在途任务"], 2)

    def test_dispatch_persists_plate_and_driver(self) -> None:
        entry, _ = self.dispatch("TRUC-0001", task_id="T-9", task_name="短驳")
        order = store.rows("_truck_orders")[-1]
        self.assertEqual(order["车牌号码"], "沪A-D1001")
        self.assertEqual(order["司机姓名"], "张建国")
        self.assertEqual(entry["车牌号码"], "沪A-D1001")
        self.assertEqual(entry["司机姓名"], "张建国")

    def test_list_and_detail_share_same_projection(self) -> None:
        items, _ = self.service.list_entries(page=1, size=100)
        for item in items:
            detail = self.service.get_entry(int(item["id"]))
            self.assertEqual(detail["车牌号码"], item["车牌号码"])
            self.assertEqual(detail["司机姓名"], item["司机姓名"])
            self.assertEqual(detail["派车状态"], item["派车状态"])
            self.assertEqual(detail["status"], item["status"])

    def test_create_keeps_optional_fields(self) -> None:
        entry, missing = self.service.create_entry({
            "集卡编号": "TRUC-0011", "车牌号码": "沪D-D4011", "所属车队": "一队",
            "司机姓名": "陈守信", "燃油余量": 80,
        })
        self.assertEqual(missing, [])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["司机姓名"], "陈守信")
        detail = self.service.get_entry(int(entry["id"]))
        self.assertEqual(detail["司机姓名"], "陈守信")

    def test_repair_register_requires_deadline_and_blocks_dispatch(self) -> None:
        entry, message = self.service.run_action(
            self.find_id("TRUC-0001"), "登记维修", {}
        )
        self.assertIsNone(entry)
        self.assertIn("维修截止日", message)
        entry, _ = self.service.run_action(
            self.find_id("TRUC-0001"), "登记维修", {"维修截止日": "2026-10-03"}
        )
        self.assertEqual(entry["status"], "维修中")
        entry, message = self.dispatch("TRUC-0001", task_id="T-X")
        self.assertIsNone(entry)
        self.assertIn("维修期内", message)
        # 放行后回到待命，油量 65% 高于一队下限，可以再派
        entry, _ = self.service.run_action(self.find_id("TRUC-0001"), "维修放行")
        self.assertEqual(entry["status"], "待命")
        entry, _ = self.dispatch("TRUC-0001", task_id="T-X")
        self.assertIsNotNone(entry)


if __name__ == "__main__":
    unittest.main()
