"""Clarification display contract; private DB and fixed model replies only.

These checks cover backend prompt/repair wiring and durable visible replies.
They do not establish whether a live model follows the prompt in one round.
"""
from __future__ import annotations

import unittest

import test_director_workflow as fixtures


class DirectorClarificationContractTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing strictly isolated service fixture without inheriting
        # its test cases or creating another provider/database arrangement.
        self.case = fixtures.DirectorWorkflowTests(methodName="runTest")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def test_initial_clarification_has_supplied_context_and_can_end_in_one_tool_call(self):
        provider = self.case.provider("respond_to_user", {
            "kind": "clarify", "message": "可以从晨光中的杯身开始拍。这条短片主要用于横屏还是竖屏？",
            "missing_fields": ["ratio"]})
        result = self.case.conversation(provider, message="我想拍一条水杯短片，请先帮我明确怎么拍。")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["outcome"]["status"], "clarify")
        self.assertEqual([entry["tool"] for entry in result["tool_calls"]], ["respond_to_user"])
        messages = provider.calls[0]["messages"]
        system = messages[0]["content"]
        self.assertIn("已经提供真实 creation/project 上下文", system)
        self.assertIn("写脚本无需预先查询模型", system)
        self.assertIn("普通assistant文本和中间工具结果不展示给用户", system)
        context = next(item["content"] for item in messages if item["role"] == "context")
        self.assertEqual(context["creation"]["project_id"], self.case.pid)
        self.assertIn("project", context)
        self.case.no_tasks()

    def test_unshown_plain_plan_is_repaired_into_a_complete_visible_reply_and_replayed(self):
        plan = "建议先拍窗边杯身全景，再拍杯口特写，共两镜约10秒。希望采用横屏还是竖屏？"
        provider = fixtures.FixedReplyProvider([
            {"reply": plan},
            {"tool_calls": [self.case.call("respond_to_user", {
                "kind": "clarify", "message": plan, "missing_fields": ["ratio"]}, "complete-reply")]},
        ])
        result = self.case.conversation(provider, message="先聊聊两镜水杯短片怎么拍。")
        self.assertEqual(len(provider.calls), 2)
        repair = provider.calls[1]["messages"][-1]
        self.assertEqual(repair["role"], "system")
        self.assertIn("尚未展示给用户", repair["content"])
        self.assertIn("完整、自洽地写入message", repair["content"])
        self.assertIn("未经实际工具结果证实的完成声明", repair["content"])
        self.assertEqual(provider.calls[1]["messages"][-2]["content"], plan)
        self.assertEqual(result["reply"], plan)
        self.assertEqual(result["outcome"]["status"], "clarify")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["actions"], [])
        self.assertEqual(fixtures.projects.list_scripts(self.case.pid), [])
        receipt = fixtures.service.request_receipt(self.case.pid, "request-1")
        self.assertEqual(receipt["response"]["reply"], plan)
        history = fixtures.db.query("SELECT reply_text FROM director_turns WHERE project_id=?", (self.case.pid,))
        self.assertEqual([turn["reply_text"] for turn in history], [plan])
        replay = self.case.conversation(provider, message="先聊聊两镜水杯短片怎么拍。")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["reply"], plan)
        self.assertEqual(len(provider.calls), 2)
        self.case.no_tasks()

    def test_plain_false_success_still_gets_only_one_repair_and_never_creates_artifacts(self):
        provider = fixtures.FixedReplyProvider([
            {"reply": "稍等，我这就保存脚本。"}, {"reply": "已经全部保存成功。"},
            {"tool_calls": [self.case.call("write_script", self.case.script_args(), "must-not-run")]},
        ])
        result = self.case.conversation(provider)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result["outcome"]["status"], "failed")
        self.assertEqual(result["outcome"]["error"]["code"], "DIRECTOR_NO_ACTION")
        self.assertNotIn("全部保存成功", result["reply"])
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["actions"], [])
        self.assertEqual(fixtures.projects.list_scripts(self.case.pid), [])
        self.case.no_tasks()

    def test_repaired_actual_script_write_keeps_saved_outcome_without_an_extra_summary_call(self):
        provider = fixtures.FixedReplyProvider([
            {"reply": "稍等，我这就保存。"},
            {"tool_calls": [self.case.call("write_script", self.case.script_args(), "real-write")]},
            {"reply": "This third model call must not run"},
        ])
        result = self.case.conversation(provider)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result["outcome"], {"status": "saved", "node_ids": ["script"]})
        self.assertEqual(len(fixtures.projects.list_scripts(self.case.pid)), 1)
        self.case.action(result, "confirm_script")
        self.case.no_tasks()

    def test_discussion_repair_cannot_hide_an_unrepaired_write_failure(self):
        provider = fixtures.FixedReplyProvider([
            {"tool_calls": [self.case.call("write_script", {"summary": ""}, "failed-write")]},
            {"reply": "已经全部保存成功。"},
            {"tool_calls": [self.case.call("respond_to_user", {
                "kind": "discuss", "message": "我们可以继续讨论画面风格。", "missing_fields": []}, "discuss")]},
            {"tool_calls": [self.case.call("write_script", {"summary": ""}, "still-invalid")]},
        ])
        result = self.case.conversation(provider)
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(result["outcome"]["status"], "failed")
        self.assertNotEqual(result["reply"], "我们可以继续讨论画面风格。")
        self.assertNotIn("全部保存成功", result["reply"])
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(fixtures.projects.list_scripts(self.case.pid), [])
        self.case.no_tasks()


if __name__ == "__main__":
    unittest.main()
