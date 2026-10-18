"""Tools：搜索资料，以及执行模型写出的求解程序。"""
import re
import requests
from evaluator import evaluate

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search", "description": "按需搜索算法论文，返回标题、DOI和可用摘要；不是全文阅读。",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "solve", "description": "保存并运行Python求解代码，再使用该任务的官方checker验收。允许Gurobi、COPT或普通Python。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "formulation": {"type": "string", "description": "变量、目标、约束和算法思路"},
        }, "required": ["code", "formulation"]},
    }},
    {"type": "function", "function": {
        "name": "finish", "description": "选择已经通过验收的候选，结束任务。",
        "parameters": {"type": "object", "properties": {
            "candidate_id": {"type": "integer"}, "summary": {"type": "string"},
        }, "required": ["candidate_id", "summary"]},
    }},
]


def search(query):
    try:
        response = requests.get("https://api.crossref.org/works", params={"query": query[:300], "rows": 3}, timeout=20)
        response.raise_for_status()
        return {"references": [{"title": item.get("title", []), "url": item.get("URL"),
                "abstract": re.sub("<[^>]+>", "", item.get("abstract", ""))[:2000]}
                for item in response.json()["message"]["items"]]}
    except (requests.RequestException, ValueError, KeyError) as error:
        return {"error": str(error)}


def solve(goal, folder, code, formulation):
    folder.mkdir(parents=True)
    (folder / "candidate.py").write_text(code, encoding="utf-8")
    (folder / "formulation.md").write_text(formulation, encoding="utf-8")
    return evaluate(goal, folder)
