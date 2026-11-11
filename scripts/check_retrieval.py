"""检索层实机自检：在真实仓库语料上验证"定义块是否被正确召回"。

零成本（不调用大模型，只做本地 BM25 + 向量检索），用于回答一个具体问题：
W6 复盘里 014 / 017 / 019 的"检索盲区"是否真的修好了——
即「被查符号的物理定义块」能不能进入 Top-K。

用法：
    python scripts/check_retrieval.py
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

from code_agent.index.hybrid_retriever import HybridRetriever  # noqa: E402

# (用例ID, 查询, 必须出现在 Top-K 里的符号名)
PROBES: list[tuple[str, str, str]] = [
    ("014", "正则表达式 _CITATION_PATTERN 是如何同时兼容单行引用 [file:line] 与区间引用 [file:start-end] 的？", "_CITATION_PATTERN"),
    ("017", "BM25CodeIndex.build_from_directory 在递归扫描目录构建索引时，默认排除了哪些目录？", "DEFAULT_IGNORE_DIRS"),
    ("019", "has_no_evidence 判定函数是通过检查哪些特定关键词来识别模型是否显式输出了“无依据”的？", "_NO_EVIDENCE_PATTERN"),
    ("011", "项目中的严重度枚举 Severity 是在哪个文件定义的，包含哪几个级别？", "Severity"),
    ("006", "tokenize_code 分词函数是如何处理驼峰命名（如 OrderTimeout）和下划线命名的？", "tokenize_code"),
]


def main() -> int:
    retriever = HybridRetriever(alpha=0.5)
    total = retriever.build_from_sources(code_dir=_ROOT / "src", doc_dir=_ROOT / "docs")
    print(f"索引构建完成：{total} 个切块\n")

    # 统计多符号块，确认 symbol_names 真的生效
    multi_symbol = [c for c in retriever.chunks if len(c.all_symbols()) > 1]
    print(f"多符号聚合块数量：{len(multi_symbol)}")
    for chunk in multi_symbol[:5]:
        print(f"  - {chunk.file_path}:{chunk.start_line}-{chunk.end_line} -> {chunk.all_symbols()}")
    print()

    failures = 0
    for case_id, query, required_symbol in PROBES:
        hits = retriever.search(query, top_k=3)
        found = any(required_symbol in chunk.all_symbols() for hit in hits for chunk in [hit.chunk])
        mark = "✅" if found else "❌"
        print(f"{mark} Case {case_id} | 期望定义块符号: {required_symbol}")
        for rank, hit in enumerate(hits, 1):
            symbols = hit.chunk.all_symbols() or [""]
            label = ", ".join(s for s in symbols if s) or "(无符号)"
            rel = Path(hit.chunk.file_path).resolve()
            try:
                rel_str = rel.relative_to(_ROOT).as_posix()
            except ValueError:
                rel_str = str(rel)
            print(
                f"    Top{rank} [{hit.source:6s}] score={hit.score:.2f} "
                f"{rel_str}:{hit.chunk.start_line}-{hit.chunk.end_line} | {label}"
            )
        if not found:
            failures += 1
        print()

    print("=" * 60)
    if failures:
        print(f"⚠️ {failures}/{len(PROBES)} 个探针未在 Top-3 命中定义块")
    else:
        print(f"✅ {len(PROBES)}/{len(PROBES)} 个探针均在 Top-3 命中了定义块")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())