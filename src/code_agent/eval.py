"""
Code-Agent 模块化评测与重放入口
支持通过 `python -m code_agent.eval` 启动
"""
import sys
from pathlib import Path

# 确保能平稳引入 eval 目录下的执行引擎
root_path = Path(__file__).resolve().parent.parent.parent
eval_dir = root_path / "eval"
if str(eval_dir) not in sys.path:
    sys.path.insert(0, str(eval_dir))

from eval.run_eval import main

if __name__ == "__main__":
    main()