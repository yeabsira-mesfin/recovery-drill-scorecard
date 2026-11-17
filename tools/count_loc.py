"""Count Python code lines while excluding comments, docstrings and fixtures."""

import ast
import io
import json
from pathlib import Path
import tokenize


def count(path):
    source = path.read_text(encoding="utf-8")
    excluded = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                excluded.update(range(node.lineno, node.end_lineno + 1))
    code = set()
    ignored = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
               tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in ignored:
            code.update(range(token.start[0], token.end[0] + 1))
    return len(code - excluded)


def measure(root):
    totals = {"application": 0, "tests_and_acceptance": 0, "tools": 0}
    files = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        category = "application"
        if "tests" in relative.parts or path.name == "acceptance.py":
            category = "tests_and_acceptance"
        if "tools" in relative.parts:
            category = "tools"
        lines = count(path)
        totals[category] += lines
        files.append({"path": relative.as_posix(), "category": category, "code": lines})
    return {"method": "Python token lines excluding comments and standalone string expressions",
            "totals": totals, "files": files}


if __name__ == "__main__":
    print(json.dumps(measure(Path(__file__).resolve().parents[1]), indent=2))
