"""
함수형 문제의 시그니처: 함수 이름, 인자(이름과 자료형), 반환형.

문제의 problems.signature(jsonb)에 이 모양으로 저장한다.
    {"function": "twoSum",
     "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
     "returns": "int[]",
     "output_param": null}
returns가 void면 함수가 제자리에서 고친 인자(output_param)를 답으로 출력한다(LeetCode의 in-place 문제).
"""
import re
from dataclasses import dataclass
from typing import Optional

SCALARS = ("int", "long", "double", "bool", "char", "string")
MAX_PARAMS = 10
MAX_NAME = 30


class SignatureError(ValueError):
    """관리자에게 그대로 보여 줄 수 있는 한국어 설명을 담는다."""


@dataclass(frozen=True)
class Type:
    """
    kind: "scalar" | "array" | "listnode" | "treenode" | "void"
    base: scalar·array의 원소 자료형(SCALARS 중 하나)
    dims: array의 차원(1 또는 2)
    is_list: list<T>로 적었는지. 대부분의 언어에서는 배열과 같고 Java에서만 List<...>가 된다.
    """
    kind: str
    base: Optional[str] = None
    dims: int = 0
    is_list: bool = False

    @property
    def text(self) -> str:
        if self.kind == "scalar":
            return self.base
        if self.kind == "array":
            if self.is_list:
                return "list<" * self.dims + self.base + ">" * self.dims
            return self.base + "[]" * self.dims
        return {"listnode": "ListNode", "treenode": "TreeNode", "void": "void"}[self.kind]

    @property
    def is_node(self) -> bool:
        return self.kind in ("listnode", "treenode")


_LIST = re.compile(r"^list<(.+)>$")


def parse_type(text: str, allow_void: bool = False) -> Type:
    raw = text
    text = (text or "").replace(" ", "")
    if text == "void":
        if allow_void:
            return Type("void")
        raise SignatureError("void는 반환형에만 쓸 수 있습니다.")
    if text == "ListNode":
        return Type("listnode")
    if text == "TreeNode":
        return Type("treenode")
    if text in SCALARS:
        return Type("scalar", text)

    dims, is_list = 0, False
    if text.startswith("list<"):
        is_list = True
        while (match := _LIST.match(text)):
            text, dims = match.group(1), dims + 1
    else:
        while text.endswith("[]"):
            text, dims = text[:-2], dims + 1
    if text in SCALARS and 1 <= dims <= 2:
        return Type("array", text, dims, is_list)
    raise SignatureError(f"알 수 없는 자료형입니다: {raw}")


# 어느 언어에서든 이름으로 쓰면 문제가 되는 낱말. 드라이버가 쓰는 이름(loj로 시작)도 막는다.
RESERVED = set("""
abstract alias and as assert async auto await begin bool boolean break byte case catch char class
const constexpr continue debugger def default defined del delete die do double echo elif else
elsif empty end ensure enum eval except exit explicit export extends extern false final finally
float for foreach friend from function global goto if implements import in include inline instanceof
int interface is isset lambda last let list local long main module my namespace native new next
nil none nonlocal not null or our package pass print private protected public raise redo register
require rescue retry return self short signed sizeof static string struct sub super switch
synchronized template then this throw throws transient true try typedef typename typeof undef
union unless unset unsigned until use using var virtual void volatile when while with yield
solution listnode treenode string array object number integer float boolean character
returnsize returncolumnsizes
""".split())

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def snake_case(name: str) -> str:
    """Ruby에서 쓰는 이름. twoSum → two_sum, isValidBST → is_valid_bst"""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def _check_name(name, what: str) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise SignatureError(f"{what} 이름은 영문자로 시작하고 영문자, 숫자, _만 쓸 수 있습니다: {name!r}")
    if len(name) > MAX_NAME:
        raise SignatureError(f"{what} 이름은 {MAX_NAME}자까지 쓸 수 있습니다: {name}")
    if name.lower() in RESERVED or name.lower().startswith("loj"):
        raise SignatureError(f"{what} 이름으로 쓸 수 없는 낱말입니다: {name}")
    return name


@dataclass(frozen=True)
class Param:
    name: str
    type: Type


@dataclass(frozen=True)
class Signature:
    function: str
    params: tuple
    returns: Type
    output_param: Optional[str] = None

    @property
    def output_type(self) -> Type:
        """답으로 출력하는 값의 자료형. void면 제자리에서 고친 인자의 자료형이다."""
        if self.returns.kind != "void":
            return self.returns
        return next(p.type for p in self.params if p.name == self.output_param)

    def to_json(self) -> dict:
        return {
            "function": self.function,
            "params": [{"name": p.name, "type": p.type.text} for p in self.params],
            "returns": self.returns.text,
            "output_param": self.output_param,
        }


def parse_signature(data) -> Signature:
    if not isinstance(data, dict):
        raise SignatureError("시그니처는 객체여야 합니다.")
    function = _check_name(data.get("function"), "함수")
    params_raw = data.get("params")
    if not isinstance(params_raw, list) or len(params_raw) > MAX_PARAMS:
        raise SignatureError(f"인자는 {MAX_PARAMS}개까지 적을 수 있습니다.")

    params = []
    for item in params_raw:
        if not isinstance(item, dict):
            raise SignatureError("인자마다 이름과 자료형을 적어야 합니다.")
        params.append(Param(_check_name(item.get("name"), "인자"), parse_type(item.get("type"))))

    names = [p.name for p in params]
    lowered = [n.lower() for n in names] + [function.lower()]
    if len(set(lowered)) != len(lowered):
        raise SignatureError("함수와 인자의 이름이 서로 겹치면 안 됩니다(대소문자만 다른 것도).")
    snakes = [snake_case(n) for n in names] + [snake_case(function)]
    if len(set(snakes)) != len(snakes):
        raise SignatureError("Ruby에서 쓰는 이름(snake_case)이 서로 겹칩니다. 이름을 바꿔 주세요.")
    # C에서는 배열 인자마다 길이 인자(numsSize, gridColSize)가 붙는다. 그 이름과 겹치면 안 된다.
    generated = {f"{p.name}{suffix}".lower() for p in params for suffix in ("Size", "ColSize")}
    clash = [n for n in names if n.lower() in generated]
    if clash:
        raise SignatureError(f"C에서 배열 길이 인자로 쓰는 이름과 겹칩니다: {', '.join(clash)}")

    returns = parse_type(data.get("returns"), allow_void=True)
    output_param = data.get("output_param") or None
    if returns.kind == "void":
        target = next((p for p in params if p.name == output_param), None)
        if target is None:
            raise SignatureError("반환형이 void면 함수가 고친 뒤 출력할 인자를 골라야 합니다.")
        if not (target.type.kind == "array" and target.type.base != "string" or target.type.is_node):
            raise SignatureError("제자리에서 고칠 수 있는 인자는 문자열이 아닌 배열이나 ListNode, TreeNode입니다.")
    else:
        output_param = None
    return Signature(function, tuple(params), returns, output_param)
