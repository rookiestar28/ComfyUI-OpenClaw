"""Verify selected broad-exception boundaries and repository-wide ratchets.

The selected-module contract classifies broad catches at high-value boundaries.
Repository-wide pass-only and runtime-stdout contracts separately freeze two
mechanically detectable legacy patterns without pretending every historical
broad catch can already satisfy a global BLE001-style rule.
"""

from __future__ import annotations

import argparse
import ast
import json
import symtable
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

VALID_CLASSIFICATIONS = {
    "allowed_boundary_guard",
    "needs_narrowing",
    "needs_follow_up_test_coverage",
}
VALID_COVERAGE_MODES = {"all_broad_catches", "selected_scopes"}
_AMBIGUOUS_ORIGIN = "<ambiguous>"
_ControlPath = tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class BroadCatch:
    path: str
    line: int
    scope: str
    catch_type: str


@dataclass(frozen=True)
class ScopedCall:
    path: str
    line: int
    scope: str


@dataclass(frozen=True)
class _ScopeImports:
    builtin_modules: frozenset[str]
    builtin_symbols: dict[str, str]


@dataclass(frozen=True)
class _BindingEvent:
    position: tuple[int, int]
    origin: str | None
    control_path: _ControlPath


@dataclass(frozen=True)
class _ScopeFrame:
    kind: str
    table: symtable.SymbolTable | None
    imports: _ScopeImports
    binding_events: dict[str, tuple[_BindingEvent, ...]]
    virtual_locals: frozenset[str] = frozenset()
    deferred_anchor: tuple[int, int] | None = None
    deferred_outer_paths: tuple[_ControlPath, ...] = ()


class _ImportOriginCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.origins: dict[str, set[str]] = {}

    def _record(self, local_name: str, origin: str) -> None:
        self.origins.setdefault(local_name, set()).add(origin)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            origin = "module:builtins" if alias.name == "builtins" else "other"
            self._record(local_name, origin)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name
            if node.module == "builtins" and alias.name in {
                "print",
                "Exception",
                "BaseException",
            }:
                origin = f"symbol:{alias.name}"
            else:
                origin = "other"
            self._record(local_name, origin)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _scope_imports(body: Iterable[ast.stmt]) -> _ScopeImports:
    collector = _ImportOriginCollector()
    for statement in body:
        collector.visit(statement)
    builtin_modules = {
        name
        for name, origins in collector.origins.items()
        if origins == {"module:builtins"}
    }
    builtin_symbols = {
        name: next(iter(origins)).removeprefix("symbol:")
        for name, origins in collector.origins.items()
        if len(origins) == 1 and next(iter(origins)).startswith("symbol:")
    }
    return _ScopeImports(frozenset(builtin_modules), builtin_symbols)


def _end_position(node: ast.AST) -> tuple[int, int]:
    return (
        int(getattr(node, "end_lineno", getattr(node, "lineno", 0))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))),
    )


def _bound_target_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_bound_target_names(item))
        return names
    if isinstance(node, ast.Starred):
        return _bound_target_names(node.value)
    return set()


def _bound_pattern_names(node: ast.pattern) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.MatchAs):
        if node.name is not None:
            names.add(node.name)
        if node.pattern is not None:
            names.update(_bound_pattern_names(node.pattern))
    elif isinstance(node, ast.MatchStar):
        if node.name is not None:
            names.add(node.name)
    elif isinstance(node, ast.MatchMapping):
        if node.rest is not None:
            names.add(node.rest)
        for pattern in node.patterns:
            names.update(_bound_pattern_names(pattern))
    elif isinstance(node, ast.MatchSequence):
        for pattern in node.patterns:
            names.update(_bound_pattern_names(pattern))
    elif isinstance(node, ast.MatchClass):
        for pattern in (*node.patterns, *node.kwd_patterns):
            names.update(_bound_pattern_names(pattern))
    elif isinstance(node, ast.MatchOr):
        for pattern in node.patterns:
            names.update(_bound_pattern_names(pattern))
    return names


class _BindingEventCollector(ast.NodeVisitor):
    """Collect binding activation points without crossing lexical-scope boundaries."""

    def __init__(self) -> None:
        self.events: dict[str, list[_BindingEvent]] = {}
        self._control_path: list[tuple[int, str]] = []

    def _visit_region(
        self, owner: ast.AST, label: str, nodes: Iterable[ast.AST]
    ) -> None:
        self._control_path.append((id(owner), label))
        for node in nodes:
            self.visit(node)
        self._control_path.pop()

    def _record(
        self,
        names: Iterable[str],
        activation_node: ast.AST,
        origin: str | None = "other",
    ) -> None:
        event = _BindingEvent(
            _end_position(activation_node), origin, tuple(self._control_path)
        )
        for name in names:
            self.events.setdefault(name, []).append(event)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._record(_bound_target_names(target), node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._record(_bound_target_names(node.target), node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._record(_bound_target_names(node.target), node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._record(_bound_target_names(node.target), node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            origin = "module:builtins" if alias.name == "builtins" else "other"
            self._record((local_name,), node, origin)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name
            if node.module == "builtins" and alias.name in {
                "print",
                "Exception",
                "BaseException",
            }:
                origin = f"symbol:{alias.name}"
            else:
                origin = "other"
            self._record((local_name,), node, origin)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record((node.name,), node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record((node.name,), node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record((node.name,), node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._control_path.append((id(node), "body"))
        self._record(_bound_target_names(node.target), node.iter)
        for statement in node.body:
            self.visit(statement)
        self._control_path.pop()
        self._visit_region(node, "else", node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
        self._control_path.append((id(node), "body"))
        for item in node.items:
            self._record(_bound_target_names(item.optional_vars), item.context_expr)
        for statement in node.body:
            self.visit(statement)
        self._control_path.pop()

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._record((node.name,), node.type or node)
        for statement in node.body:
            self.visit(statement)
        if node.name:
            # CPython clears the exception target when the handler exits.
            self._record((node.name,), node, None)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", node.body)
        self._visit_region(node, "else", node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", node.body)
        self._visit_region(node, "else", node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_region(node, "try-body", node.body)
        for index, handler in enumerate(node.handlers):
            self._visit_region(node, f"try-handler:{index}", (handler,))
        self._visit_region(node, "try-else", node.orelse)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_TryStar(self, node: Any) -> None:
        self.visit_Try(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", (node.body,))
        self._visit_region(node, "else", (node.orelse,))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not node.values:
            return
        self.visit(node.values[0])
        for index, value in enumerate(node.values[1:], start=1):
            self._visit_region(node, f"value:{index}", (value,))

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for index, case in enumerate(node.cases):
            self._control_path.append((id(node), f"case:{index}"))
            self._record(_bound_pattern_names(case.pattern), case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            self._control_path.pop()

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._record(_bound_target_names(target), node, None)

    def _visit_single_value_comp(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp
    ) -> None:
        if not node.generators:
            self.visit(node.elt)
            return
        self.visit(node.generators[0].iter)
        self._control_path.append((id(node), "iteration"))
        for generator in node.generators:
            if generator is not node.generators[0]:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.elt)
        self._control_path.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_single_value_comp(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_single_value_comp(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        if not node.generators:
            self.visit(node.key)
            self.visit(node.value)
            return
        self.visit(node.generators[0].iter)
        self._control_path.append((id(node), "iteration"))
        for generator in node.generators:
            if generator is not node.generators[0]:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.key)
        self.visit(node.value)
        self._control_path.pop()

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_single_value_comp(node)


def _scope_binding_events(
    body: Iterable[ast.stmt],
) -> dict[str, tuple[_BindingEvent, ...]]:
    collector = _BindingEventCollector()
    for statement in body:
        collector.visit(statement)
    return {
        name: tuple(sorted(events, key=lambda event: event.position))
        for name, events in collector.events.items()
    }


def _argument_names(arguments: ast.arguments) -> set[str]:
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *(() if arguments.vararg is None else (arguments.vararg,)),
            *(() if arguments.kwarg is None else (arguments.kwarg,)),
        )
    }


def _function_lexical_locals(
    table: symtable.SymbolTable,
    arguments: ast.arguments,
    binding_events: dict[str, tuple[_BindingEvent, ...]],
) -> frozenset[str]:
    candidates = (
        _argument_names(arguments) | set(binding_events) | set(table.get_identifiers())
    )
    return frozenset(
        name
        for name in candidates
        if name not in table.get_identifiers()
        or (not table.lookup(name).is_global() and not table.lookup(name).is_nonlocal())
    )


class _BroadCatchVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, tree: ast.Module):
        self.path = path.as_posix()
        self.scope_stack: list[str] = []
        self.catches: list[BroadCatch] = []
        self.pass_only_catches: list[BroadCatch] = []
        self.print_calls: list[ScopedCall] = []
        module_table = symtable.symtable(source, str(path), "exec")
        self._frames = [
            _ScopeFrame(
                kind="module",
                table=module_table,
                imports=_scope_imports(tree.body),
                binding_events=_scope_binding_events(tree.body),
            )
        ]
        self._frame_control_paths: list[list[tuple[int, str]]] = [[]]
        self._child_tables: dict[symtable.SymbolTable, list[symtable.SymbolTable]] = {}

    def _take_child_table(
        self, names: str | tuple[str, ...], line: int, *, required: bool = True
    ) -> symtable.SymbolTable | None:
        expected_names = (names,) if isinstance(names, str) else names
        parent = next(
            frame.table for frame in reversed(self._frames) if frame.table is not None
        )
        children = self._child_tables.setdefault(parent, list(parent.get_children()))
        for index, child in enumerate(children):
            if child.get_name() in expected_names and child.get_lineno() == line:
                return children.pop(index)
        if required:
            joined = "/".join(expected_names)
            raise RuntimeError(f"missing symbol table for {joined} at line {line}")
        return None

    @staticmethod
    def _has_binding(table: symtable.SymbolTable, name: str) -> bool:
        if name not in table.get_identifiers():
            return False
        symbol = table.lookup(name)
        return bool(
            symbol.is_local()
            and (
                symbol.is_assigned()
                or symbol.is_imported()
                or symbol.is_parameter()
                or symbol.is_namespace()
            )
        )

    @staticmethod
    def _node_position(node: ast.AST) -> tuple[int, int]:
        return (
            int(getattr(node, "lineno", 0)),
            int(getattr(node, "col_offset", 0)),
        )

    def _uses_direct_execution_order(self, frame_index: int) -> bool:
        return self._frames[frame_index].kind in {"module", "class"} and all(
            frame.kind in {"class", "comprehension"}
            for frame in self._frames[frame_index + 1 :]
        )

    @staticmethod
    def _control_region_labels_compatible(left: str, right: str) -> bool:
        if left == right:
            return True
        if left == "try-body":
            return right == "try-else" or right.startswith("try-handler:")
        if right == "try-body":
            return left == "try-else" or left.startswith("try-handler:")
        return False

    @classmethod
    def _control_paths_compatible(cls, left: _ControlPath, right: _ControlPath) -> bool:
        left_regions = dict(left)
        right_regions = dict(right)
        return all(
            owner not in right_regions
            or cls._control_region_labels_compatible(label, right_regions[owner])
            for owner, label in left_regions.items()
        )

    @classmethod
    def _control_path_dominates(cls, event: _ControlPath, use: _ControlPath) -> bool:
        if len(event) > len(use):
            return False
        for index, (event_owner, event_label) in enumerate(event):
            use_owner, use_label = use[index]
            if event_owner != use_owner:
                return False
            if event_label == use_label:
                continue
            # CRITICAL: entering a try-else proves its top-level try body completed,
            # but a body prefix may only have run before a handler. Keep body->else
            # dominant and body->handler ambiguous or builtin uses can be hidden.
            if (
                index == len(event) - 1
                and event_label == "try-body"
                and use_label == "try-else"
            ):
                continue
            return False
        return True

    def _binding_origin_at(
        self,
        frame_index: int,
        name: str,
        position: tuple[int, int],
        control_path: _ControlPath,
    ) -> str | None:
        # CRITICAL: source-earlier conditional bindings may never execute. Only a
        # dominating region may replace state; compatible alternatives stay ambiguous
        # so dead branches and zero-iteration loops cannot hide a runtime builtin.
        states: set[str | None] = {None}
        for event in self._frames[frame_index].binding_events.get(name, ()):
            if event.position > position:
                break
            if not self._control_paths_compatible(event.control_path, control_path):
                continue
            if self._control_path_dominates(event.control_path, control_path):
                states = {event.origin}
            else:
                states.add(event.origin)
        if len(states) == 1:
            return next(iter(states))
        return _AMBIGUOUS_ORIGIN

    def _deferred_context_after(
        self, frame_index: int
    ) -> tuple[tuple[int, int], _ControlPath] | None:
        for frame in self._frames[frame_index + 1 :]:
            if frame.kind != "generator_expression":
                continue
            if frame.deferred_anchor is None or frame_index >= len(
                frame.deferred_outer_paths
            ):
                continue
            return frame.deferred_anchor, frame.deferred_outer_paths[frame_index]
        return None

    def _deferred_binding_origin(
        self,
        frame_index: int,
        name: str,
        anchor: tuple[int, int],
        control_path: _ControlPath,
    ) -> str | None:
        # CRITICAL: generator bodies resolve enclosing names when consumed, not when
        # created. Any compatible later mutation is ambiguous and must fail closed.
        states = {self._binding_origin_at(frame_index, name, anchor, control_path)}
        for event in self._frames[frame_index].binding_events.get(name, ()):
            if event.position <= anchor:
                continue
            if self._control_paths_compatible(event.control_path, control_path):
                states.add(event.origin)
        if len(states) == 1:
            return next(iter(states))
        return _AMBIGUOUS_ORIGIN

    def _active_binding_origin(
        self, frame_index: int, name: str, node: ast.AST
    ) -> str | None:
        return self._binding_origin_at(
            frame_index,
            name,
            self._node_position(node),
            tuple(self._frame_control_paths[frame_index]),
        )

    def _resolved_binding_origin(
        self, frame_index: int, name: str, node: ast.AST
    ) -> str | None:
        deferred_context = self._deferred_context_after(frame_index)
        if deferred_context is not None:
            return self._deferred_binding_origin(frame_index, name, *deferred_context)
        if self._uses_direct_execution_order(frame_index):
            return self._active_binding_origin(frame_index, name, node)
        origins = {
            event.origin
            for event in self._frames[frame_index].binding_events.get(name, ())
        }
        if len(origins) == 1:
            return next(iter(origins))
        return None

    def _frame_has_binding(self, frame_index: int, name: str, node: ast.AST) -> bool:
        frame = self._frames[frame_index]
        if name in frame.virtual_locals:
            return True
        if frame.kind in {
            "function",
            "lambda",
            "comprehension",
            "generator_expression",
        }:
            return False
        if frame.table is None:
            return False
        if frame.kind in {"module", "class"} and self._uses_direct_execution_order(
            frame_index
        ):
            return self._active_binding_origin(frame_index, name, node) is not None
        return self._has_binding(frame.table, name)

    def _find_enclosing_binding(
        self, name: str, node: ast.AST, start_index: int
    ) -> int | None:
        for index in range(start_index, -1, -1):
            frame = self._frames[index]
            # Python closure/free-name lookup intentionally skips class namespaces.
            if frame.kind == "class":
                continue
            if self._frame_has_binding(index, name, node):
                return index
        return None

    def _binding_index(self, name: str, node: ast.AST) -> int | None:
        current_index = len(self._frames) - 1
        current_frame = self._frames[current_index]

        if (
            current_frame.kind
            in {
                "comprehension",
                "generator_expression",
            }
            and current_frame.table is None
        ):
            if name in current_frame.virtual_locals:
                return current_index
            return self._find_enclosing_binding(name, node, current_index - 1)

        current = current_frame.table
        if current is None or name not in current.get_identifiers():
            return self._find_enclosing_binding(name, node, current_index - 1)
        symbol = current.lookup(name)
        if self._frame_has_binding(current_index, name, node):
            return current_index
        if current_frame.kind == "class":
            return self._find_enclosing_binding(name, node, current_index - 1)
        if symbol.is_free() or symbol.is_nonlocal():
            return self._find_enclosing_binding(name, node, current_index - 1)
        if symbol.is_global() and self._frame_has_binding(0, name, node):
            return 0
        return None

    def _resolved_import_origin(
        self, binding_index: int, name: str, node: ast.AST
    ) -> str | None:
        deferred_context = self._deferred_context_after(binding_index)
        if deferred_context is not None:
            return self._deferred_binding_origin(binding_index, name, *deferred_context)
        if any(
            frame.kind in {"function", "lambda"}
            for frame in self._frames[binding_index + 1 :]
        ):
            origins = {
                event.origin
                for event in self._frames[binding_index].binding_events.get(name, ())
            }
            if len(origins) == 1:
                return next(iter(origins))
            return _AMBIGUOUS_ORIGIN if origins else None
        # CRITICAL: symbol.is_assigned() is whole-scope metadata. Canonical import
        # aliases need use-site provenance or a dead branch/zero loop can hide a
        # real builtins.print or builtins.Exception policy boundary.
        return self._active_binding_origin(binding_index, name, node)

    def _canonical_import_symbol(self, name: str, node: ast.AST) -> str | None:
        binding_index = self._binding_index(name, node)
        if binding_index is None:
            return None
        frame = self._frames[binding_index]
        table = frame.table
        if table is None:
            return None
        symbol = table.lookup(name)
        canonical = frame.imports.builtin_symbols.get(name)
        if not symbol.is_imported() or canonical is None:
            return None
        if self._resolved_import_origin(binding_index, name, node) not in {
            f"symbol:{canonical}",
            _AMBIGUOUS_ORIGIN,
        }:
            return None
        return canonical

    def _resolves_to_builtin_symbol(
        self, name: str, expected: str, node: ast.AST
    ) -> bool:
        binding_index = self._binding_index(name, node)
        if binding_index is None:
            return name == expected
        if (
            self._resolved_binding_origin(binding_index, name, node)
            == _AMBIGUOUS_ORIGIN
            and name == expected
        ):
            return True
        return self._canonical_import_symbol(name, node) == expected

    def _resolves_to_builtin_module(self, name: str, node: ast.AST) -> bool:
        binding_index = self._binding_index(name, node)
        if binding_index is None:
            return False
        frame = self._frames[binding_index]
        table = frame.table
        if table is None:
            return False
        symbol = table.lookup(name)
        resolved_origin = self._resolved_import_origin(binding_index, name, node)
        return bool(
            symbol.is_imported()
            and name in frame.imports.builtin_modules
            and resolved_origin in {"module:builtins", _AMBIGUOUS_ORIGIN}
        )

    def _catch_type_name(self, node: ast.expr | None) -> str:
        if node is None:
            return "bare"
        if isinstance(node, ast.Name):
            for expected in ("BaseException", "Exception"):
                if self._resolves_to_builtin_symbol(node.id, expected, node):
                    return expected
            if node.id in {"BaseException", "Exception"}:
                return ""
            return node.id
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in {"Exception", "BaseException"}
            and self._resolves_to_builtin_module(node.value.id, node)
        ):
            return node.attr
        if isinstance(node, ast.Tuple):
            names = {self._catch_type_name(item) for item in node.elts}
            if "BaseException" in names:
                return "BaseException"
            if "Exception" in names:
                return "Exception"
        return ""

    def _visit_control_region(
        self, owner: ast.AST, label: str, nodes: Iterable[ast.AST]
    ) -> None:
        self._frame_control_paths[-1].append((id(owner), label))
        for node in nodes:
            self.visit(node)
        self._frame_control_paths[-1].pop()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_control_region(node, "body", node.body)
        self._visit_control_region(node, "else", node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_control_region(node, "body", node.body)
        self._visit_control_region(node, "else", node.orelse)

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._frame_control_paths[-1].append((id(node), "body"))
        self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        self._frame_control_paths[-1].pop()
        self._visit_control_region(node, "else", node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
        self._frame_control_paths[-1].append((id(node), "body"))
        for item in node.items:
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        for statement in node.body:
            self.visit(statement)
        self._frame_control_paths[-1].pop()

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_control_region(node, "try-body", node.body)
        for index, handler in enumerate(node.handlers):
            self._visit_control_region(node, f"try-handler:{index}", (handler,))
        self._visit_control_region(node, "try-else", node.orelse)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_TryStar(self, node: Any) -> None:
        self.visit_Try(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._visit_control_region(node, "body", (node.body,))
        self._visit_control_region(node, "else", (node.orelse,))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not node.values:
            return
        self.visit(node.values[0])
        for index, value in enumerate(node.values[1:], start=1):
            self._visit_control_region(node, f"value:{index}", (value,))

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for index, case in enumerate(node.cases):
            self._frame_control_paths[-1].append((id(node), f"case:{index}"))
            self.visit(case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            self._frame_control_paths[-1].pop()

    def _visit_outer_function_expressions(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    ) -> None:
        if not isinstance(node, ast.Lambda):
            for decorator in node.decorator_list:
                self.visit(decorator)
            if node.returns is not None:
                self.visit(node.returns)
            for type_parameter in getattr(node, "type_params", ()):
                self.visit(type_parameter)
        arguments = node.args
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for optional_argument in (arguments.vararg, arguments.kwarg):
            if (
                optional_argument is not None
                and optional_argument.annotation is not None
            ):
                self.visit(optional_argument.annotation)
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._visit_outer_function_expressions(node)
        child = self._take_child_table(node.name, node.lineno)
        if child is None:
            raise RuntimeError(
                f"missing symbol table for {node.name} at line {node.lineno}"
            )
        binding_events = _scope_binding_events(node.body)
        self.scope_stack.append(node.name)
        self._frames.append(
            _ScopeFrame(
                kind="function",
                table=child,
                imports=_scope_imports(node.body),
                binding_events=binding_events,
                virtual_locals=_function_lexical_locals(
                    child, node.args, binding_events
                ),
            )
        )
        self._frame_control_paths.append([])
        for statement in node.body:
            self.visit(statement)
        self._frame_control_paths.pop()
        self._frames.pop()
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        child = self._take_child_table(node.name, node.lineno)
        if child is None:
            raise RuntimeError(
                f"missing symbol table for {node.name} at line {node.lineno}"
            )
        self.scope_stack.append(node.name)
        self._frames.append(
            _ScopeFrame(
                kind="class",
                table=child,
                imports=_scope_imports(node.body),
                binding_events=_scope_binding_events(node.body),
            )
        )
        self._frame_control_paths.append([])
        for statement in node.body:
            self.visit(statement)
        self._frame_control_paths.pop()
        self._frames.pop()
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_outer_function_expressions(node)
        child = self._take_child_table("lambda", node.lineno)
        if child is None:
            raise RuntimeError(f"missing symbol table for lambda at line {node.lineno}")
        collector = _BindingEventCollector()
        collector.visit(node.body)
        binding_events = {
            name: tuple(sorted(events, key=lambda event: event.position))
            for name, events in collector.events.items()
        }
        self._frames.append(
            _ScopeFrame(
                kind="lambda",
                table=child,
                imports=_ScopeImports(frozenset(), {}),
                binding_events=binding_events,
                virtual_locals=_function_lexical_locals(
                    child, node.args, binding_events
                ),
            )
        )
        self._frame_control_paths.append([])
        self.visit(node.body)
        self._frame_control_paths.pop()
        self._frames.pop()

    def _visit_comprehension_scope(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        values: tuple[ast.expr, ...],
        table_names: tuple[str, ...],
    ) -> None:
        generators = node.generators
        if not generators:
            for value in values:
                self.visit(value)
            return

        # CRITICAL: the first iterable executes in the enclosing scope, while
        # targets/body use an isolated scope that skips class locals. Keeping an
        # explicit frame makes this invariant independent of PEP 709 symtable layout.
        self.visit(generators[0].iter)
        target_names: set[str] = set()
        for generator in generators:
            target_names.update(_bound_target_names(generator.target))
        child = self._take_child_table(table_names, node.lineno, required=False)
        frame_kind = (
            "generator_expression"
            if isinstance(node, ast.GeneratorExp)
            else "comprehension"
        )
        self._frames.append(
            _ScopeFrame(
                kind=frame_kind,
                table=child,
                imports=_ScopeImports(frozenset(), {}),
                binding_events={},
                virtual_locals=frozenset(target_names),
                deferred_anchor=(
                    self._node_position(node)
                    if frame_kind == "generator_expression"
                    else None
                ),
                deferred_outer_paths=(
                    tuple(tuple(path) for path in self._frame_control_paths)
                    if frame_kind == "generator_expression"
                    else ()
                ),
            )
        )
        self._frame_control_paths.append([])
        self.visit(generators[0].target)
        for condition in generators[0].ifs:
            self.visit(condition)
        for generator in generators[1:]:
            self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self._frame_control_paths.pop()
        self._frames.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_scope(node, (node.elt,), ("listcomp", "<listcomp>"))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_scope(node, (node.elt,), ("setcomp", "<setcomp>"))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_scope(
            node, (node.key, node.value), ("dictcomp", "<dictcomp>")
        )

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_scope(node, (node.elt,), ("genexpr", "<genexpr>"))

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        catch_type = self._catch_type_name(node.type)
        if catch_type in {"bare", "Exception", "BaseException"}:
            catch = BroadCatch(
                path=self.path,
                line=node.lineno,
                scope=".".join(self.scope_stack) or "<module>",
                catch_type=catch_type,
            )
            self.catches.append(catch)
            if (
                catch_type == "Exception"
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                self.pass_only_catches.append(catch)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # CRITICAL: keep lexical/import resolution here; syntax-only matching lets
        # builtins.print bypass the ratchet and mistakes shadowed helpers for stdout.
        is_runtime_print = (
            isinstance(node.func, ast.Name)
            and self._resolves_to_builtin_symbol(node.func.id, "print", node.func)
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.attr == "print"
            and self._resolves_to_builtin_module(node.func.value.id, node.func)
        )
        if is_runtime_print:
            self.print_calls.append(
                ScopedCall(
                    path=self.path,
                    line=node.lineno,
                    scope=".".join(self.scope_stack) or "<module>",
                )
            )
        self.generic_visit(node)


def _visitor_for(path: Path) -> _BroadCatchVisitor:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    visitor = _BroadCatchVisitor(path, source, tree)
    visitor.visit(tree)
    return visitor


def iter_broad_catches(path: Path) -> Iterable[BroadCatch]:
    visitor = _visitor_for(path)
    return tuple(visitor.catches)


def iter_pass_only_broad_catches(path: Path) -> Iterable[BroadCatch]:
    visitor = _visitor_for(path)
    return tuple(visitor.pass_only_catches)


def iter_runtime_prints(path: Path) -> Iterable[ScopedCall]:
    visitor = _visitor_for(path)
    return tuple(visitor.print_calls)


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_ratchet_contract(
    repo_root: Path,
    contract: Any,
    *,
    entries_key: str,
    label: str,
    iterator: Callable[[Path], Iterable[BroadCatch | ScopedCall]],
) -> list[str]:
    failures: list[str] = []
    if not isinstance(contract, dict) or set(contract) != {"roots", entries_key}:
        return [f"{label} must contain roots and {entries_key}"]

    roots = contract.get("roots")
    if (
        not isinstance(roots, list)
        or not roots
        or any(
            not isinstance(root, str)
            or not root
            or Path(root).is_absolute()
            or ".." in Path(root).parts
            for root in roots
        )
        or len(roots) != len(set(roots))
    ):
        return [f"{label} roots must be unique safe repository-relative paths"]

    normalized_roots = tuple(Path(root).as_posix().rstrip("/") for root in roots)
    for root in normalized_roots:
        if not (repo_root / root).is_dir():
            failures.append(f"{label} root does not exist: {root}")

    entries = contract.get(entries_key)
    if not isinstance(entries, list):
        failures.append(f"{label} {entries_key} must be a list")
        return failures

    expected: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "scope",
            "expected_count",
            "reason",
            "review_after",
        }:
            failures.append(f"{label} {entries_key}[{index}] keys must match schema")
            continue
        rel_path = entry.get("path")
        scope = entry.get("scope")
        expected_count = entry.get("expected_count")
        reason = entry.get("reason")
        review_after = entry.get("review_after")
        if not isinstance(rel_path, str):
            failures.append(f"{label} {entries_key}[{index}] path must be a string")
            continue
        path_obj = Path(rel_path)
        normalized_path = path_obj.as_posix()
        if (
            path_obj.is_absolute()
            or ".." in path_obj.parts
            or path_obj.suffix != ".py"
            or not any(
                normalized_path == root or normalized_path.startswith(root + "/")
                for root in normalized_roots
            )
        ):
            failures.append(f"{label} unsafe owned path: {rel_path}")
            continue
        if not (repo_root / path_obj).is_file():
            failures.append(f"{label} owned path does not exist: {rel_path}")
        if not isinstance(scope, str) or not scope:
            failures.append(f"{label} {rel_path}: scope must be non-empty")
            continue
        if not isinstance(expected_count, int) or expected_count < 1:
            failures.append(f"{label} {rel_path}:{scope}: expected_count must be >= 1")
            continue
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"{label} {rel_path}:{scope}: missing reason")
        try:
            if not isinstance(review_after, str):
                raise TypeError
            review_date = date.fromisoformat(review_after)
        except (TypeError, ValueError):
            failures.append(f"{label} {rel_path}:{scope}: invalid review_after")
        else:
            if review_date < date.today():
                failures.append(f"{label} {rel_path}:{scope}: review_after is expired")
        key = (normalized_path, scope)
        if key in expected:
            failures.append(f"{label} duplicate owned scope: {rel_path}:{scope}")
        expected[key] = expected_count

    actual: Counter[tuple[str, str]] = Counter()
    locations: dict[tuple[str, str], list[int]] = {}
    for root in normalized_roots:
        for path in sorted((repo_root / root).rglob("*.py")):
            rel_path = path.relative_to(repo_root).as_posix()
            try:
                calls = tuple(iterator(path))
            except (OSError, SyntaxError, UnicodeError):
                failures.append(f"{label} could not parse: {rel_path}")
                continue
            for call in calls:
                key = (rel_path, call.scope)
                actual[key] += 1
                locations.setdefault(key, []).append(call.line)

    for (rel_path, scope), count in sorted(actual.items()):
        if (rel_path, scope) not in expected:
            line = min(locations[(rel_path, scope)])
            failures.append(f"unowned {label} at {rel_path}:{line} in {scope}")
        elif expected[(rel_path, scope)] != count:
            failures.append(
                f"{label} count drift at {rel_path}:{scope}: "
                f"expected {expected[(rel_path, scope)]}, found {count}"
            )
    for (rel_path, scope), count in sorted(expected.items()):
        if (rel_path, scope) not in actual:
            failures.append(
                f"stale {label} entry at {rel_path}:{scope}: expected {count}, found 0"
            )
    return failures


def validate_exception_boundary_policy(
    repo_root: Path,
    policy: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if set(policy) != {
        "version",
        "selected_modules",
        "pass_only_contract",
        "stdout_contract",
    }:
        failures.append("policy root keys must match the version 3 schema")
    if policy.get("version") != 3:
        failures.append("policy version must equal 3")
    modules = policy.get("selected_modules")
    if not isinstance(modules, dict) or not modules:
        return ["policy selected_modules must be a non-empty object"]

    for rel_path, module_policy in sorted(modules.items()):
        rel_path_obj = Path(rel_path)
        if (
            rel_path_obj.is_absolute()
            or ".." in rel_path_obj.parts
            or rel_path_obj.suffix != ".py"
        ):
            failures.append(f"{rel_path}: unsafe selected module path")
            continue
        if not isinstance(module_policy, dict):
            failures.append(f"{rel_path}: module policy must be an object")
            continue
        path = repo_root / rel_path
        if not path.is_file():
            failures.append(f"{rel_path}: selected module does not exist")
            continue

        allowed = module_policy.get("broad_catches")
        if not isinstance(allowed, list):
            failures.append(f"{rel_path}: broad_catches must be a list")
            continue

        coverage = module_policy.get("coverage")
        if coverage not in VALID_COVERAGE_MODES:
            failures.append(f"{rel_path}: invalid coverage mode {coverage!r}")
            continue
        expected_module_keys = {"coverage", "broad_catches"}
        if coverage == "selected_scopes":
            expected_module_keys.add("selected_scopes")
        if set(module_policy) != expected_module_keys:
            failures.append(f"{rel_path}: module keys must match coverage schema")
        selected_scopes_raw = module_policy.get("selected_scopes", [])
        if coverage == "selected_scopes":
            if (
                not isinstance(selected_scopes_raw, list)
                or not selected_scopes_raw
                or any(
                    not isinstance(scope, str) or not scope
                    for scope in selected_scopes_raw
                )
                or len(selected_scopes_raw) != len(set(selected_scopes_raw))
            ):
                failures.append(
                    f"{rel_path}: selected_scopes must be a unique non-empty string list"
                )
                selected_scopes: set[str] = set()
            else:
                selected_scopes = set(selected_scopes_raw)
        else:
            if selected_scopes_raw:
                failures.append(
                    f"{rel_path}: all_broad_catches must not declare selected_scopes"
                )
            selected_scopes = set()

        entries_by_scope: dict[str, dict[str, Any]] = {}
        for index, entry in enumerate(allowed):
            if not isinstance(entry, dict):
                failures.append(f"{rel_path}: broad_catches[{index}] must be an object")
                continue
            if set(entry) != {
                "scope",
                "expected_count",
                "classification",
                "reason",
                "regression_owner",
                "review_after",
            }:
                failures.append(
                    f"{rel_path}: broad_catches[{index}] entry keys must match schema"
                )
            scope = entry.get("scope")
            classification = entry.get("classification")
            reason = entry.get("reason")
            regression_owner = entry.get("regression_owner")
            review_after = entry.get("review_after")
            if not isinstance(scope, str) or not scope:
                failures.append(f"{rel_path}: broad_catches[{index}] missing scope")
                continue
            if scope in entries_by_scope:
                failures.append(f"{rel_path}: duplicate broad-catch scope {scope}")
            entries_by_scope[scope] = entry
            if classification not in VALID_CLASSIFICATIONS:
                failures.append(
                    f"{rel_path}:{scope}: invalid classification {classification!r}"
                )
            if not isinstance(reason, str) or not reason.strip():
                failures.append(f"{rel_path}:{scope}: missing reason")
            if not isinstance(regression_owner, str) or not regression_owner.strip():
                failures.append(f"{rel_path}:{scope}: missing regression_owner")
            else:
                owner_path = Path(regression_owner)
                if (
                    owner_path.is_absolute()
                    or ".." in owner_path.parts
                    or not owner_path.parts
                    or owner_path.parts[0] != "tests"
                    or owner_path.suffix != ".py"
                ):
                    failures.append(
                        f"{rel_path}:{scope}: regression_owner must be a safe tests/*.py path"
                    )
                elif not (repo_root / owner_path).is_file():
                    failures.append(
                        f"{rel_path}:{scope}: regression_owner does not exist"
                    )
            try:
                if not isinstance(review_after, str):
                    raise TypeError
                review_date = date.fromisoformat(review_after)
            except (TypeError, ValueError):
                failures.append(f"{rel_path}:{scope}: invalid review_after")
            else:
                if review_date < date.today():
                    failures.append(f"{rel_path}:{scope}: review_after is expired")
            if coverage == "selected_scopes" and scope not in selected_scopes:
                failures.append(f"{rel_path}:{scope}: entry is outside selected_scopes")

        catches = tuple(iter_broad_catches(path))
        governed_catches = (
            catches
            if coverage == "all_broad_catches"
            else tuple(catch for catch in catches if catch.scope in selected_scopes)
        )
        counts = Counter(catch.scope for catch in governed_catches)
        if coverage == "selected_scopes":
            for stale_scope in sorted(selected_scopes - set(counts)):
                failures.append(
                    f"{rel_path}:{stale_scope}: selected scope has no broad catch"
                )
        for catch in governed_catches:
            if catch.scope not in entries_by_scope:
                failures.append(
                    f"{rel_path}:{catch.line}: undocumented broad catch in {catch.scope}"
                )

        for scope, entry in entries_by_scope.items():
            expected_count = entry.get("expected_count", 1)
            if not isinstance(expected_count, int) or expected_count < 1:
                failures.append(f"{rel_path}:{scope}: expected_count must be >= 1")
                continue
            actual_count = counts.get(scope, 0)
            if actual_count != expected_count:
                failures.append(
                    f"{rel_path}:{scope}: expected {expected_count} broad catch(es), found {actual_count}"
                )

    failures.extend(
        _validate_ratchet_contract(
            repo_root,
            policy.get("pass_only_contract"),
            entries_key="grandfathered",
            label="pass-only broad catch",
            iterator=iter_pass_only_broad_catches,
        )
    )
    failures.extend(
        _validate_ratchet_contract(
            repo_root,
            policy.get("stdout_contract"),
            entries_key="allowed",
            label="runtime print",
            iterator=iter_runtime_prints,
        )
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--policy",
        default="tests/exception_boundary_policy.json",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    policy_path = repo_root / args.policy
    failures = validate_exception_boundary_policy(repo_root, load_policy(policy_path))
    if failures:
        for failure in failures:
            print(f"EXCEPTION-BOUNDARY-FAIL: {failure}")
        return 1
    print("EXCEPTION-BOUNDARY-PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
