from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import ConfigDict, create_model

ToolFunc = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class QueryTool:
    name: str
    description: str
    func: ToolFunc
    args_model: type


_REGISTRY: dict[str, QueryTool] = {}


def query_tool(name: str, description: str) -> Callable[[ToolFunc], ToolFunc]:
    def _decorator(func: ToolFunc) -> ToolFunc:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"query tool {name!r} must be async")
        if name in _REGISTRY:
            raise ValueError(f"query tool already registered: {name}")

        args_model = _build_args_model(name, func)
        _REGISTRY[name] = QueryTool(
            name=name,
            description=description,
            func=func,
            args_model=args_model,
        )
        return func

    return _decorator


def list_tools_for_anthropic() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.args_model.model_json_schema(),
        }
        for tool in _REGISTRY.values()
    ]


def is_tool_registered(name: str) -> bool:
    return name in _REGISTRY


async def execute_tool(
    name: str,
    args: dict[str, Any],
    user_id: uuid.UUID,
) -> dict[str, Any]:
    tool = _REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"unknown query tool: {name}")

    validated = tool.args_model.model_validate(args or {})
    kwargs = validated.model_dump()
    return await tool.func(**kwargs, user_id=user_id)


def _build_args_model(name: str, func: ToolFunc) -> type:
    signature = inspect.signature(func)
    if "user_id" not in signature.parameters:
        raise TypeError(f"query tool {name!r} must accept injected user_id")
    type_hints = get_type_hints(func, include_extras=True)
    fields: dict[str, tuple[Any, Any]] = {}

    for param_name, param in signature.parameters.items():
        if param_name == "user_id":
            continue
        annotation = type_hints.get(param_name)
        if annotation is None:
            raise TypeError(
                f"query tool {name!r} parameter {param_name!r} needs a type hint"
            )
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param_name] = (annotation, default)

    model_name = f"{''.join(part.title() for part in name.split('_'))}Args"
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _reset_registry_for_tests() -> None:
    _REGISTRY.clear()
