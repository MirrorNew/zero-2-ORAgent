"""离线验证：不调用DeepSeek；用预设模型动作检查真实执行与拒绝路径。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import Goal, ROOT, run
from tools import solve


def action(name, **arguments):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": "test", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


class AgentTest(unittest.TestCase):
    def test_feedback_and_finish(self):
        goal = Goal("bergman2019", "tiny_instance.json", ROOT / "data/FrontierOR/tasks/bergman2019")
        actions = [
            action("finish", candidate_id=1, summary="不能提前提交"),
            action("solve", code="def solve(i, t): return {'objective_value': 0}", formulation="故意缺解"),
            action("solve", code="def solve(i, t): return {'objective_value': 0, 'assignment': []}", formulation="不选物品的可行基线"),
            action("finish", candidate_id=3, summary="提交通过验收的基线"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("agent.model", side_effect=actions):
                state = run(goal, Path(directory), None, max_steps=4)
            self.assertEqual(state.status, "completed")
            self.assertFalse(state.results[2]["valid"])
            self.assertTrue(state.results[3]["valid"])
            trace = json.loads((Path(directory) / "trace.json").read_text(encoding="utf-8"))
            observations = [json.loads(m["content"]) for m in trace if m["role"] == "tool"]
            self.assertIn("error", observations[0])
            self.assertTrue((Path(directory) / "best.py").exists())

    def test_step_limit(self):
        goal = Goal("bergman2019", "tiny_instance.json", ROOT / "data/FrontierOR/tasks/bergman2019")
        with tempfile.TemporaryDirectory() as directory:
            with patch("agent.model", return_value={"role": "assistant", "content": "还在考虑"}):
                state = run(goal, Path(directory), None, max_steps=1)
            self.assertEqual(state.status, "step_limit")
            self.assertFalse((Path(directory) / "best.py").exists())

    def test_optional_output_field(self):
        # 官方解省略可选的sparse_defaults，通用层不应因此拒绝它。
        task = ROOT / "data/FrontierOR/tasks/damcikurt2016"
        reference = json.loads((task / "gurobi_solution/tiny_solution.json").read_text(encoding="utf-8"))
        self.assertNotIn("sparse_defaults", reference)
        goal = Goal("damcikurt2016", "tiny_instance.json", task)
        with tempfile.TemporaryDirectory() as directory:
            result = solve(goal, Path(directory) / "candidate",
                           code=f"def solve(instance, time_limit): return {reference!r}",
                           formulation="官方已知可行解，仅测试验收接口，不代表模型能力")
            self.assertTrue(result["valid"], result)


if __name__ == "__main__":
    unittest.main()
