"""路由注册表与匹配器（架构重构第 1 期）。

目标
----
把「34 个 if/elif 分支」换成**一张可读的路由表 + 一个通用匹配器**，使得：

* 新增接口 = 在 `routes.py` 加一行（或在新模块里加一个资源组），
  不需要打开 `adapter.py`；
* 匹配规则与顺序由「静态段优先」决定，而不是靠代码书写顺序，
  避免以后插一个路由把另一个路由挡掉。

匹配规则
--------
1. 路径按 `/` 切段，去掉 `api/v1` 前缀。
2. 段数必须相等；以 `*` 结尾的路由表示「前缀匹配」，可吃 0 段或多段。
3. `{name}` 为参数段，捕获后放进 `ctx["params_path"]`。
4. 候选路由按「静态段多者优先、段数多者优先、注册顺序」排序。
   静态段优先保证 `/strategies/registered` 不会被 `/strategies/{id}` 抢走。

行为约定
--------
* 匹配不到 → 抛 `LookupError("接口不存在")`，与旧实现完全一致
  （`adapter.Handler` 会把它转成 404）。
* 本模块**不碰任何业务逻辑**，只做「路径 → 处理函数」的解析。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import unquote

_PARAM_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class Route:
    """一条路由。

    pattern  : `/api/v1/...` 形式，可含 `{name}` 与结尾 `*`
    handler  : `(ctx) -> (body, status)`
    group    : `light` / `heavy`，供后续限流分组使用（第 4 期启用）
    cache    : 建议的响应缓存秒数；0 表示不缓存（第 4 期启用）
    note     : 一句话说明，便于表本身当文档看
    """

    pattern: str
    handler: Callable[[dict[str, Any]], tuple[Any, int]]
    group: str = "heavy"
    cache: float = 0.0
    note: str = ""

    # 由 __post_init__ 计算
    segments: tuple[str, ...] = field(init=False, repr=False, compare=False)
    prefix: bool = field(init=False, repr=False, compare=False)
    static_count: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        raw = [s for s in self.pattern.split("/") if s]
        if raw[:2] != ["api", "v1"]:
            raise ValueError(f"路由必须以 /api/v1 开头：{self.pattern}")
        body = tuple(raw[2:])
        is_prefix = bool(body) and body[-1] == "*"
        if is_prefix:
            body = body[:-1]
        object.__setattr__(self, "segments", body)
        object.__setattr__(self, "prefix", is_prefix)
        object.__setattr__(self, "static_count", sum(1 for s in body if not _PARAM_RE.match(s)))

    def match(self, parts: list[str]) -> dict[str, str] | None:
        if self.prefix:
            if len(parts) < len(self.segments):
                return None
        elif len(parts) != len(self.segments):
            return None
        captured: dict[str, str] = {}
        for idx, seg in enumerate(self.segments):
            m = _PARAM_RE.match(seg)
            if m:
                captured[m.group(1)] = unquote(parts[idx])
            elif seg != unquote(parts[idx]):
                return None
        return captured

    def rest(self, parts: list[str]) -> list[str]:
        return [unquote(p) for p in parts[len(self.segments):]]


class Router:
    """路由表。注册顺序只在完全同分时作为兜底。"""

    def __init__(self, routes: Iterable[Route]):
        self._routes = list(routes)
        # 排序：静态段多者优先 → 段数多者优先 → 非前缀优先 → 注册顺序
        self._ordered = sorted(
            enumerate(self._routes),
            key=lambda pair: (-pair[1].static_count, -len(pair[1].segments),
                              0 if not pair[1].prefix else 1, pair[0]),
        )

    def __len__(self) -> int:
        return len(self._routes)

    def routes(self) -> list[Route]:
        return list(self._routes)

    def resolve(self, path: str, params: dict[str, list[str]]) -> tuple[Any, int]:
        parts = [unquote(p) for p in path.split("/") if p][2:]
        for _, route in self._ordered:
            captured = route.match(parts)
            if captured is None:
                continue
            ctx = {
                "path": path,
                "segments": parts,
                "rest": route.rest(parts),
                "path_params": captured,
                "params": params,
            }
            ctx.update(captured)
            return route.handler(ctx)
        raise LookupError("接口不存在")
