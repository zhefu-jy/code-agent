"""全链路 Trace 审计记账模块：记录每次问答的环境、输入快照、模型响应与三维判定。"""

import hashlib
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field
from code_agent.core.schemas import (
    AnswerVerdict,
    EvidenceValidity,
    SystemStatus,
)
from code_agent.index.bm25_index import CodeChunk

def compute_content_hash(content: str)-> str:
    """计算代码/文档片段内容的 SHA-256 哈希值。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_git_info(repo_path: Path | str)-> tuple[str,bool]:
    """
    探测目标工程的 Git revision 与工作区 dirty 状态。
    若非 Git 仓库或探测失败，返回 ('non-git', False)。
    """
    path = Path(repo_path).resolve()
    try:
        rev_proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if rev_proc.returncode != 0:
            return ("non-git", False)
        revision = rev_proc.stdout.strip()
        if not revision:
            return ("non-git", False)

        status_proc = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        is_dirty = bool(status_proc.stdout.strip())
        return (revision, is_dirty)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ("non-git", False)



class SnippetSnapshot(BaseModel):
    """实际提供给模型的切片片段快照及其内容哈希。"""
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str = ""
    title_path: str = ""
    source_type: str = "code"
    score: float = 0.0
    content: str
    content_hash: str

    @classmethod
    def from_chunk(cls, chunk: CodeChunk, score: float = 0.0) -> "SnippetSnapshot":
        """从底层 CodeChunk 转换为带哈希校验的快照对象。"""
        return cls(
            file_path=chunk.file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            symbol_name=chunk.symbol_name,
            title_path=chunk.title_path,
            source_type=chunk.source_type,
            score=score,
            content=chunk.content,
            content_hash=compute_content_hash(chunk.content),
        )

class TraceRecord(BaseModel):
    """问答全链路审计 Trace 实体。"""
    trace_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    agent_version: str = "0.1.0"
    repo_path: str
    git_revision: str  # commit hash 或 "non-git"
    is_git_dirty: bool
    query: str
    config: dict[str, Any] = Field(default_factory=dict)
    spec_id: Optional[str] = None
    snippets: list[SnippetSnapshot] = Field(default_factory=list)
    full_prompt: str
    raw_answer: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency: float = 0.0
    system_status: SystemStatus
    evidence_validity: EvidenceValidity
    answer_verdict: AnswerVerdict

def generate_trace_id() -> str:
    """生成唯一的 Trace 标识符，如 trace_20261117_214500_a1b2c3d4。"""
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = uuid.uuid4().hex[:8]
    return f"trace_{now_str}_{rand_suffix}"

def save_trace(record: TraceRecord, trace_dir: Path | str = "traces") -> Path:
    """将 Trace 实体持久化落盘至指定目录的 JSON 文件。"""
    out_dir = Path(trace_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{record.trace_id}.json"
    out_file.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return out_file

def load_trace(trace_path: Path | str) -> TraceRecord:
    """从磁盘 JSON 读取并反序列化 Trace 实体。"""
    path = Path(trace_path).resolve()
    content = path.read_text(encoding="utf-8")
    return TraceRecord.model_validate_json(content)