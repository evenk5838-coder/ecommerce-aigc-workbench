"""Isolated integrity regressions. No server, worker, network or business DB is opened."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import canvas, config, db, director, director_chat, projects
from app.assistant.provider import FixedReplyProvider, DeepSeekChatProvider
from app.errors import AppError


class DirectorCanvasIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="director-integrity-")
        self.old_db = config.DB_PATH
        self.old_limits = config.DIRECTOR_MAX_COST_PER_CALL_CNY, config.DIRECTOR_MAX_SESSION_COST_CNY
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        config.DIRECTOR_MAX_COST_PER_CALL_CNY = config.DIRECTOR_MAX_SESSION_COST_CNY = 0
        db.init_db()
        self.pid = projects.create_project("isolated review", {"subject": "one bottle", "quantity": 1})["project_id"]

    def tearDown(self):
        config.DB_PATH = self.old_db
        config.DIRECTOR_MAX_COST_PER_CALL_CNY, config.DIRECTOR_MAX_SESSION_COST_CNY = self.old_limits
        self.tmp.cleanup()

    def failure(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def chat(self, message="hello", key="r1", provider=None, **kw):
        return director_chat.chat(self.pid, {"message": message, "client_request_id": key, **kw},
                                  provider=provider or FixedReplyProvider([{"reply": "hello"}]))

    def test_memory_contains_previous_turn_and_script_contents(self):
        script = projects.add_script(self.pid, {"summary": "bottle reveal", "shots": []})
        first = self.chat("first unique request", provider=FixedReplyProvider([{"reply": "option A or option B?"}]))
        p = FixedReplyProvider([{"reply": "yes"}])
        self.chat("choose second", "r2", p, session_id=first["session_id"])
        messages = p.calls[0]["messages"]
        self.assertTrue(any(m.get("content") == "first unique request" for m in messages))
        self.assertTrue(any(m.get("content") == "option A or option B?" for m in messages))
        self.assertIn("bottle reveal", json.dumps(messages))
        self.assertIn(script["script_id"], json.dumps(messages))

    def test_other_project_history_cannot_leak(self):
        other = projects.create_project("other", {"subject": "secret"})["project_id"]
        director_chat.chat(other, {"message": "PRIVATE", "client_request_id": "other"},
                           provider=FixedReplyProvider([{"reply": "SECRET ANSWER"}]))
        p = FixedReplyProvider([{"reply": "ok"}])
        self.chat(provider=p)
        self.assertNotIn("PRIVATE", json.dumps(p.calls))
        self.assertNotIn("SECRET ANSWER", json.dumps(p.calls))

    def test_usage_accumulates_both_provider_calls(self):
        p = FixedReplyProvider([
            {"input_tokens": 100, "output_tokens": 10, "tool_calls": [{"name": "get_project_state"}]},
            {"reply": "done", "input_tokens": 200, "output_tokens": 20}])
        out = self.chat(provider=p)
        self.assertEqual((out["usage"]["input_tokens"],out["usage"]["output_tokens"]),(300,30))
        self.assertAlmostEqual(out["usage"]["cost_cny"], director_chat._token_cost(300,30),places=6)
        self.assertEqual(len(db.query("SELECT * FROM assistant_usage")),2)
        self.assertTrue(all(call["max_tokens"] > 0 for call in p.calls))

    def test_second_call_budget_gate_keeps_first_usage_and_failed_receipt(self):
        config.DIRECTOR_MAX_SESSION_COST_CNY = 0.03
        p = FixedReplyProvider([
            {"input_tokens": 10000, "output_tokens": 0, "tool_calls": [{"name": "get_project_state"}]},
            {"reply": "must not execute"}])
        with patch.object(director_chat,"_estimate_call_cost",return_value=0.02):
            self.failure("ASSISTANT_BUDGET_EXCEEDED",lambda:self.chat(provider=p))
        self.assertEqual(len(p.calls),1)
        self.assertEqual(len(db.query("SELECT * FROM assistant_usage")),1)
        self.assertEqual(director_chat.request_receipt(self.pid,"r1")["status"],"failed")
        self.failure("DIRECTOR_REQUEST_FAILED",lambda:self.chat(provider=p))
        self.assertEqual(len(p.calls),1)

    def test_initial_budget_failure_is_not_pending(self):
        config.DIRECTOR_MAX_COST_PER_CALL_CNY = 0.000001
        p = FixedReplyProvider([{"reply":"never"}])
        self.failure("ASSISTANT_BUDGET_EXCEEDED",lambda:self.chat(provider=p))
        self.assertEqual(len(p.calls),0)
        receipt=director_chat.request_receipt(self.pid,"r1")
        self.assertEqual((receipt["status"],receipt["error_code"]),("failed","ASSISTANT_BUDGET_EXCEEDED"))

    def test_revision_tracks_edits_and_replay_precedes_revision_validation(self):
        baseline = director_chat._workflow_revision(self.pid)
        out = self.chat(expected_workflow_revision=baseline)
        self.assertEqual(out["workflow_revision"],director_chat._workflow_revision(self.pid))
        projects.update_project(self.pid,brief={"style":"new style"})
        self.assertNotEqual(out["workflow_revision"],director_chat._workflow_revision(self.pid))
        replay=self.chat(expected_workflow_revision=baseline)
        self.assertTrue(replay["idempotent_replay"])
        p=FixedReplyProvider([{"reply":"never"}])
        self.failure("WORKFLOW_REVISION_CONFLICT",lambda:self.chat(key="stale",provider=p,expected_workflow_revision=baseline))
        self.assertEqual(p.calls,[])
        self.assertLess(director_chat._workflow_revision(self.pid),2**53)

    def test_canvas_get_does_not_create_state(self):
        self.assertEqual(canvas.get_canvas(self.pid)["edges"],[])
        self.assertEqual(db.query("SELECT * FROM canvas_states"),[])

    def test_position_only_preserves_image_type_binding_config(self):
        script=projects.add_script(self.pid,{"summary":"script"})
        canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"n1","type":"image", "position":{"x":1,"y":2},
            "config":{"prompt":"keep"},"binding":{"script_id":script["script_id"]}}]})
        before=canvas.get_canvas(self.pid)
        out=canvas.patch_canvas(self.pid,{"expected_layout_revision":before["layout_revision"],
            "nodes":[{"node_id":"n1","position":{"x":5,"y":6}}]})
        node=out["canvas"]["nodes"][0]
        self.assertEqual(node["type"],"image")
        self.assertEqual(node["binding"],{"script_id":script["script_id"]})
        self.assertEqual(node["config"],{"prompt":"keep"})
        self.assertEqual(node["config_revision"],before["nodes"][0]["config_revision"])
        self.assertFalse(out["config_changed"])

    def test_edges_survive_refresh_and_edges_only_advances_layout(self):
        canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"a"},{"node_id":"b"}]})
        old=canvas.get_canvas(self.pid)["layout_revision"]
        edges=[{"from":"a","to":"b"}]
        out=canvas.patch_canvas(self.pid,{"expected_layout_revision":old,"edges":edges})
        self.assertEqual(out["layout_revision"],old+1)
        self.assertEqual(canvas.get_canvas(self.pid)["edges"],edges)
        self.failure("CANVAS_REVISION_CONFLICT",lambda:canvas.patch_canvas(self.pid,{"expected_layout_revision":old,"edges":[]}))

    def test_invalid_later_node_rolls_back_everything_and_confirmation(self):
        canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"a","config":{"prompt":"old"}}]})
        director.confirm_object(self.pid,"scope","scope-fixture",1)
        before=canvas.get_canvas(self.pid)
        self.failure("VALIDATION_FAILED",lambda:canvas.patch_canvas(self.pid,{"nodes":[
            {"node_id":"a","config":{"prompt":"new"}},{"node_id":"bad","type":"invalid"}]}))
        self.assertEqual(canvas.get_canvas(self.pid),before)
        self.assertTrue(director.current_confirmation("scope","scope-fixture",1)["valid"])

    def test_invalid_edges_roll_back_nodes_and_pruning(self):
        canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"a"},{"node_id":"b"}],"edges":[{"from":"a","to":"b"}]})
        before=canvas.get_canvas(self.pid)
        self.failure("CANVAS_INVALID_REFERENCE",lambda:canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"a"}],"prune":True}))
        self.assertEqual(canvas.get_canvas(self.pid),before)
        self.failure("CANVAS_CYCLE",lambda:canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"c"}],
            "edges":[{"from":"a","to":"b"},{"from":"b","to":"a"}]}))
        self.assertEqual(canvas.get_canvas(self.pid),before)

    def test_invalid_reference_image_and_image_size_rejected(self):
        image="doubao-seedream-5-0-flash-260915"
        self.failure("CANVAS_INVALID_REFERENCE",lambda:canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"x", "config":{
            "model_id":image,"generation_mode":"image_to_image","input_asset_id":"nonexistent"}}]}))
        self.failure("MODEL_CAPABILITY_MISMATCH",lambda:canvas.patch_canvas(self.pid,{"nodes":[{"node_id":"x", "config":{
            "model_id":image,"generation_mode":"text_to_image","params":{"size":"10x10"}}}]}))
        self.assertEqual(canvas.get_canvas(self.pid)["nodes"],[])

    def make_plan(self):
        script=projects.add_script(self.pid,{"summary":"script"})
        projects.confirm_script(script["script_id"])
        prop=director_chat._save_proposal(self.pid,"shot_plan",{"shots":[{"description":"shot 1","duration_s":5}]},
            target_id=script["script_id"],target_version=script["version"])
        return script,prop

    def test_apply_idempotent_returns_original_shot_ids(self):
        _,prop=self.make_plan()
        first=director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"a1")
        repeat=director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"a1")
        self.assertEqual(first["shot_ids"],repeat["shot_ids"])
        self.assertTrue(repeat["idempotent_replay"])
        self.assertEqual(len(db.query("SELECT * FROM shots")),1)

    def test_apply_failure_receipt_rolls_back_shots(self):
        _,prop=self.make_plan()
        with db.conn() as c:
            c.execute("CREATE TRIGGER test_fail_receipt BEFORE INSERT ON director_requests BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):
            director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"a1")
        self.assertEqual(db.query("SELECT * FROM shots"),[])
        self.assertEqual(db.get("director_proposals",prop["proposal_id"])["status"],"draft")

    def test_invalidated_script_does_not_offer_or_apply_old_plan(self):
        script,prop=self.make_plan()
        projects.update_project(self.pid,brief={"style":"changed"})
        self.assertFalse(director_chat._tool_state(self.pid)["script"]["confirmed"])
        actions=director_chat._actions_for(self.pid,director.readiness(self.pid))
        self.assertIn("confirm_script",[x["type"] for x in actions])
        self.assertNotIn("apply_shot_plan",[x["type"] for x in actions])
        self.failure("SCRIPT_NOT_CONFIRMED",lambda:director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"a1"))

    def test_message_restore_returns_current_actions_and_session(self):
        script,prop=self.make_plan()
        out=self.chat()
        before=director_chat.messages(self.pid)
        self.assertEqual(before["session_id"],out["session_id"])
        self.assertEqual(before["project_id"],self.pid)
        self.assertIn("apply_shot_plan",[x["type"] for x in before["actions"]])
        director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"apply")
        after=director_chat.messages(self.pid)
        self.assertNotIn("apply_shot_plan",[x["type"] for x in after["actions"]])
        self.assertEqual(after["workflow_revision"],director_chat._workflow_revision(self.pid))
        self.assertNotEqual(before["workflow_revision"],after["workflow_revision"])

    def test_reconfirm_after_invalidation_can_apply_plan(self):
        script,prop=self.make_plan()
        projects.update_project(self.pid,brief={"style":"changed"})
        projects.confirm_script(script["script_id"])
        result=director_chat.apply_proposal(self.pid,prop["proposal_id"],1,"apply")
        self.assertEqual(result["count"],1)

    def test_message_pagination_does_not_skip_equal_timestamps(self):
        self.chat(key="r1")
        self.chat(key="r2")
        with db.conn() as c:
            c.execute("UPDATE director_turns SET created_at=100")
        first=director_chat.messages(self.pid,limit=1)
        second=director_chat.messages(self.pid,cursor=first["next_cursor"],limit=1)
        self.assertEqual(len(second["items"]),1)
        self.assertNotEqual(first["items"][0]["turn_id"],second["items"][0]["turn_id"])

    def test_cross_project_bindings_rollback_complete_patch(self):
        other=projects.create_project("other",{"subject":"other"})["project_id"]
        script=projects.add_script(other,{"summary":"other script"})
        self.failure("CANVAS_INVALID_REFERENCE",lambda:canvas.patch_canvas(self.pid,{"nodes":[
            {"node_id":"first"},{"node_id":"second","binding":{"script_id":script["script_id"]}}]}))
        self.assertEqual(canvas.get_canvas(self.pid)["nodes"],[])

    def test_malformed_model_brief_cannot_poison_project_state(self):
        p=FixedReplyProvider([{"tool_calls":[{"name":"propose_brief","arguments":{"brief":{"subject":{"bad":"object"}}}}]},
                              {"reply":"Please clarify"}])
        out=self.chat(provider=p)
        self.assertEqual(out["artifacts"],[])
        self.assertEqual(out["tool_calls"][0]["error"]["code"],"VALIDATION_FAILED")
        self.assertEqual(director.readiness(self.pid)["facts"]["brief"]["subject"],"one bottle")

    def test_provider_honors_explicit_output_limit(self):
        p=DeepSeekChatProvider(model="fixture",api_key="test-only")
        with patch("app.assistant.provider._deepseek_post",return_value={"choices":[{"message":{"content":"ok"}}]}) as call:
            p.chat([{"role":"user","content":"hello"}],[],max_tokens=37)
        self.assertEqual(call.call_args.args[1]["max_tokens"],37)


if __name__ == "__main__":
    unittest.main()
