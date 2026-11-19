import time
from dataclasses import dataclass, field
from pathlib import Path
from code_agent.analyzer.import_analyzer import (
    ImportEdge,
    ModuleDependencyGraph,
    UnresolvedImport,
    analyze_imports,
)
from code_agent.analyzer.project_scanner import ProjectStructure, scan_project
from code_agent.analyzer.symbol_extractor import (
    SymbolInfo,
    SymbolTable,
    UnresolvedItem,
    extract_symbols,
)

@dataclass(frozen=True)
class SymbolLookupResult:
    """
    符号查询结果封装，显式区分匹配精度等级。
    Attributes:
        query: 用户输入的原始查询字符串（短名、全限定名或子串）。
        match_mode: 命中方式：
            - "exact_fqn": 按全限定名（FQN）精确命中
            - "exact_name": 按短名称精确命中（可能包含多个同名符号）
            - "substring": 未精确命中，降级为不区分大小写的子串匹配
            - "none": 未找到任何匹配符号
        matches: 命中的符号列表，按文件路径、起始行号、FQN 确定性排序。
    """
    query: str
    match_mode: str
    matches: tuple[SymbolInfo, ...]

@dataclass(frozen=True)
class ModuleDependenciesSummary:
    """
    单个模块的依赖关系透视结果。
    Attributes:
        module_name: 目标模块的点分全名（如 "minisite.cli"）。
        exists: 该模块是否存在于工程内部已知模块清单中。
        internal_dependencies: 该模块导入的工程内部模块列表。
        external_dependencies: 该模块导入的标准库或第三方外部模块列表。
        dependents: 工程内所有直接导入了该模块的内部调用方模块列表。
        outgoing_edges: 该模块发起的所有原始导入边明细（含行号与别名）。
        unresolved_in_module: 发生在该模块内的未解析导入记录。
    """
    module_name: str
    exists: bool
    internal_dependencies: tuple[str, ...]
    external_dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    outgoing_edges: tuple[ImportEdge, ...]
    unresolved_in_module: tuple[UnresolvedImport, ...]

@dataclass(frozen=True)
class SymbolIndex:
    """
    工程级静态分析内存统一索引（聚合目录拓扑、符号表与模块依赖图）。
    Attributes:
        structure: S1 产出的工程目录结构快照。
        symbol_table: S2 产出的符号实体表与 AST 未解析项。
        dependency_graph: S3 产出的模块有向依赖图与导入未解析项。
        build_duration_ms: 构建整套索引（扫描 + 符号 + 导入）的耗时（毫秒）。
    """
    structure: ProjectStructure
    symbol_table: SymbolTable
    dependency_graph: ModuleDependencyGraph
    build_duration_ms: float
    _by_fqn: dict[str, SymbolInfo] = field(default_factory=dict, repr=False, compare=False)
    _by_name: dict[str, tuple[SymbolInfo, ...]] = field(default_factory=dict, repr=False, compare=False)
    _by_module: dict[str, tuple[SymbolInfo, ...]] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_components(
            cls,
            structure: ProjectStructure,
            symbol_table: SymbolTable,
            dependency_graph: ModuleDependencyGraph,
            build_duration_ms: float,
    ) -> "SymbolIndex":
        """
        基于已产出的三阶段分析结果构建内存哈希检索字典。
        Args:
            structure: 工程目录拓扑快照。
            symbol_table: AST 符号抽取表。
            dependency_graph: 模块导入依赖图。
            build_duration_ms: 构建总耗时（毫秒）。
        Returns:
            填充好加速索引字典的 SymbolIndex 实例。
        """
        by_fqn: dict[str, SymbolInfo] = {}
        name_buckets: dict[str, list[SymbolInfo]] = {}
        module_buckets: dict[str, list[SymbolInfo]] = {}

        for sym in symbol_table.symbols:
            by_fqn[sym.fqn] = sym
            name_buckets.setdefault(sym.name, []).append(sym)
            module_buckets.setdefault(sym.module_name, []).append(sym)

        return cls(
            structure=structure,
            symbol_table=symbol_table,
            dependency_graph=dependency_graph,
            build_duration_ms=build_duration_ms,
            _by_fqn=by_fqn,
            _by_name={k: tuple(v) for k, v in name_buckets.items()},
            _by_module={k: tuple(v) for k, v in module_buckets.items()},
        )

    def lookup_symbol(self, query: str) -> SymbolLookupResult:
        """
        按“精确 FQN -> 精确短名 -> 不区分大小写子串”三级策略查询符号。
        Args:
            query: 待查询的符号名称、全限定名或关键字片段。
        Returns:
            SymbolLookupResult: 包含命中模式与确定性排序后的符号元组。
        """
        cleaned = query.strip()
        if not cleaned:
            return SymbolLookupResult(query=query, match_mode="none", matches=())
        exact_fqn = self._by_fqn.get(cleaned)
        if exact_fqn is not None:
            return SymbolLookupResult(
                query=cleaned,
                match_mode="exact_fqn",
                matches=(exact_fqn,),
            )
        exact_name = self._by_name.get(cleaned)
        if exact_name:
            return SymbolLookupResult(
                query=cleaned,
                match_mode="exact_name",
                matches=exact_name,
            )
        needle = cleaned.lower()
        partial_matches = tuple(
            sym
            for sym in self.symbol_table.symbols
            if (needle in sym.fqn.lower() if "." in needle else needle in sym.name.lower())
        )
        if partial_matches:
            return SymbolLookupResult(
                query=cleaned,
                match_mode="substring",
                matches=partial_matches,
            )
        return SymbolLookupResult(query=cleaned, match_mode="none", matches=())

    def symbols_in_module(self, module_name: str) -> tuple[SymbolInfo, ...]:
        """
        返回指定模块内定义的所有符号（含模块自身、类、方法、函数与常量）。
        Args:
            module_name: 点分模块全名（如 "minisite.engine"）。
        Returns:
            该模块下的符号元组。
        """
        return self._by_module.get(module_name.strip(), ())

    def inspect_module_deps(self, module_name: str) -> ModuleDependenciesSummary:
        """
        透视单个模块的出入向依赖边与模块内未解析导入项。
        Args:
            module_name: 待查询的内部模块点分全名。
        Returns:
            ModuleDependenciesSummary: 结构化模块依赖透视结果。
        """
        target = module_name.strip()
        exists = target in self.dependency_graph.modules
        internal_deps = self.dependency_graph.dependencies_of(target, internal_only=True)
        all_deps = self.dependency_graph.dependencies_of(target, internal_only=False)
        internal_set = set(internal_deps)
        external_deps = tuple(dep for dep in all_deps if dep not in internal_set)
        dependents = self.dependency_graph.dependents_of(target)
        outgoing_edges = tuple(
            edge for edge in self.dependency_graph.edges if edge.source_module == target
        )
        unresolved_in_module = tuple(
            item for item in self.dependency_graph.unresolved if item.source_module == target
        )
        return ModuleDependenciesSummary(
            module_name=target,
            exists=exists,
            internal_dependencies=internal_deps,
            external_dependencies=external_deps,
            dependents=dependents,
            outgoing_edges=outgoing_edges,
            unresolved_in_module=unresolved_in_module,
        )

    def all_unresolved(self) -> tuple[tuple[UnresolvedItem, ...], tuple[UnresolvedImport, ...]]:
        """返回工程内全部未解析项：(符号未解析清单, 导入未解析清单)。"""
        return self.symbol_table.unresolved, self.dependency_graph.unresolved

def build_symbol_index(project_root: str | Path) -> SymbolIndex:
    """
    一站式扫描工程并构建统一静态符号索引，同时测量构建耗时。
    Args:
        project_root: 目标工程根目录路径。
    Returns:
        SymbolIndex: 聚合目录结构、符号表、依赖图与耗时基线的内存索引对象。
    """
    start = time.perf_counter()
    structure = scan_project(project_root)
    symbol_table = extract_symbols(structure)
    dependency_graph = analyze_imports(structure)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return SymbolIndex.from_components(
        structure=structure,
        symbol_table=symbol_table,
        dependency_graph=dependency_graph,
        build_duration_ms=elapsed_ms,
    )

