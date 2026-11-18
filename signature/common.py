"""언어별 드라이버가 함께 쓰는 것."""
import json

from .types import Signature, Type


def spec(t: Type) -> list:
    """드라이버 런타임에 넘기는 자료형 표시: [kind, base, dims]"""
    return [t.kind, t.base or "", t.dims]


def specs_json(sig: Signature) -> str:
    return json.dumps([spec(p.type) for p in sig.params])


def output_spec_json(sig: Signature) -> str:
    """답으로 출력하는 값의 자료형 표시(JSON)"""
    return json.dumps(spec(sig.output_type))


def output_index(sig: Signature) -> int:
    """void면 답으로 출력할 인자의 번호, 아니면 -1"""
    if sig.returns.kind != "void":
        return -1
    return [p.name for p in sig.params].index(sig.output_param)


def uses(sig: Signature, kind: str) -> bool:
    return any(t.kind == kind for t in [p.type for p in sig.params] + [sig.returns])


def comment_block(lines: list, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}".rstrip() for line in lines)
