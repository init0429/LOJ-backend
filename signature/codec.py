"""
함수형 문제의 입출력 텍스트(일반 OJ 형식)와 값, LeetCode 표기 사이의 변환.

테스트는 지금처럼 표준 입력/출력 텍스트로 저장한다. 인자는 순서대로 이렇게 적는다.
    정수·실수·bool·문자   토큰 하나(bool은 true/false)
    문자열               한 줄 통째로(공백 포함). 문자열은 늘 새 줄에서 시작한다.
    1차원 배열·list       개수 n, 이어서 값 n개(줄바꿈 자유). 문자열 배열이면 n줄
    2차원 배열·list<list> 행 수 r, 각 행은 "k v1 … vk"(문자열이면 k 다음 k줄)
    ListNode             1차원 int 배열과 같음
    TreeNode             개수 n, 이어서 LeetCode 레벨 순서 토큰 n개(null 포함)
답(반환값)은 개수 없이 적는다.
    스칼라는 한 줄(double은 소수점 5자리), 1차원은 공백으로 이은 한 줄(문자열 배열은 값마다 한 줄),
    2차원은 행마다 한 줄, TreeNode는 끝의 null을 지운 레벨 순서.
각 언어의 드라이버(signature/lang_*.py)가 같은 규칙으로 읽고 쓴다.
"""
import json
import re

from .types import Signature, SignatureError, Type

# ── 읽기: 드라이버와 똑같은 규칙 ─────────────────────────────────────────────


class Reader:
    """숫자는 토큰 단위로, 문자열은 줄 단위로 읽는다. 언어별 드라이버의 읽기와 같은 규칙이다."""

    def __init__(self, text: str):
        self.lines = [line.rstrip("\r") for line in text.split("\n")]
        self.li = 0
        self.col = 0

    def token(self) -> str:
        while self.li < len(self.lines):
            line, c = self.lines[self.li], self.col
            while c < len(line) and line[c] in " \t":
                c += 1
            if c < len(line):
                start = c
                while c < len(line) and line[c] not in " \t":
                    c += 1
                self.col = c
                return line[start:c]
            self.li, self.col = self.li + 1, 0
        raise SignatureError("입력이 인자보다 일찍 끝났습니다.")

    def line(self) -> str:
        # 이미 토큰을 읽은 줄이면 그 줄의 나머지(공백)는 버리고 다음 줄을 읽는다.
        if self.col > 0:
            self.li, self.col = self.li + 1, 0
        if self.li >= len(self.lines):
            return ""
        line = self.lines[self.li]
        self.li += 1
        return line

    def rest_is_blank(self) -> bool:
        rest = self.lines[self.li][self.col:] if self.li < len(self.lines) else ""
        return not rest.strip() and all(not line.strip() for line in self.lines[self.li + 1:])


def _scalar_from_token(base: str, token: str):
    try:
        if base in ("int", "long"):
            return int(token)
        if base == "double":
            return float(token)
    except ValueError:
        raise SignatureError(f"{base} 값이 아닙니다: {token}") from None
    if base == "bool":
        if token in ("true", "1"):
            return True
        if token in ("false", "0"):
            return False
        raise SignatureError(f"bool 값은 true나 false로 적어 주세요: {token}")
    if base == "char":
        if len(token) != 1:
            raise SignatureError(f"char 값은 한 글자여야 합니다: {token}")
        return token
    raise AssertionError(base)


def _count(reader: Reader) -> int:
    token = reader.token()
    if not token.isdigit():
        raise SignatureError(f"개수 자리에 0 이상의 정수가 와야 합니다: {token}")
    return int(token)


def read_value(reader: Reader, t: Type):
    if t.kind == "scalar":
        return reader.line() if t.base == "string" else _scalar_from_token(t.base, reader.token())
    if t.kind in ("array", "listnode") and (t.kind == "listnode" or t.dims == 1):
        base = "int" if t.kind == "listnode" else t.base
        n = _count(reader)
        if base == "string":
            return [reader.line() for _ in range(n)]
        return [_scalar_from_token(base, reader.token()) for _ in range(n)]
    if t.kind == "array":
        rows = []
        for _ in range(_count(reader)):
            k = _count(reader)
            if t.base == "string":
                rows.append([reader.line() for _ in range(k)])
            else:
                rows.append([_scalar_from_token(t.base, reader.token()) for _ in range(k)])
        return rows
    if t.kind == "treenode":
        values = []
        for _ in range(_count(reader)):
            token = reader.token()
            values.append(None if token == "null" else _scalar_from_token("int", token))
        return values
    raise AssertionError(t)


def decode_input(sig: Signature, text: str) -> list:
    """표준 입력 텍스트를 인자 값 목록으로. 맞지 않으면 SignatureError"""
    reader = Reader(text)
    values = [read_value(reader, p.type) for p in sig.params]
    if not reader.rest_is_blank():
        raise SignatureError("인자를 다 읽고도 입력이 남았습니다.")
    return values


def decode_output(t: Type, text: str):
    """답 텍스트를 값으로(화면에 LeetCode 표기로 보여 주려고). 맞지 않으면 SignatureError"""
    text = text.replace("\r", "").strip("\n")
    if t.kind == "scalar":
        return text if t.base == "string" else _output_scalar(t.base, text.strip())
    lines = text.split("\n") if text else []
    if t.kind == "array" and t.dims == 1 and t.base == "string":
        return lines
    if t.kind == "array" and t.dims == 2:
        if t.base == "string":
            return [line.split() for line in lines]
        return [[_output_scalar(t.base, token) for token in line.split()] for line in lines]
    tokens = text.split()
    if t.kind == "treenode":
        return [None if token == "null" else _scalar_from_token("int", token) for token in tokens]
    base = "int" if t.kind == "listnode" else t.base
    return [_output_scalar(base, token) for token in tokens]


class _Raw(str):
    """이미 정해진 모양으로 적힌 숫자(double). 보여 줄 때 다시 고치지 않는다."""


def _output_scalar(base: str, token: str):
    if base == "double":
        _scalar_from_token("double", token)
        return _Raw(token)
    return _scalar_from_token(base, token)


# ── 쓰기 ──────────────────────────────────────────────────────────────────────


def _format_double(value) -> str:
    text = "%.5f" % float(value)
    return "0.00000" if text == "-0.00000" else text


def _token(base: str, value) -> str:
    if base == "bool":
        return "true" if value else "false"
    if base == "double":
        return value if isinstance(value, _Raw) else _format_double(value)
    return str(value)


def _input_token(base: str, value) -> str:
    # 입력의 실수는 적힌 그대로 둔다(1e-3 같은 표기도 그대로 읽힌다).
    if base == "double":
        return str(value)
    return _token(base, value)


def _encode_list(base: str, values: list) -> str:
    if base == "string":
        return "\n".join([str(len(values)), *values])
    if not values:
        return "0"
    return f"{len(values)}\n" + " ".join(_input_token(base, v) for v in values)


def encode_value(t: Type, value) -> str:
    if t.kind == "scalar":
        return value if t.base == "string" else _input_token(t.base, value)
    if t.kind == "listnode" or (t.kind == "array" and t.dims == 1):
        return _encode_list("int" if t.kind == "listnode" else t.base, value)
    if t.kind == "array":
        lines = [str(len(value))]
        for row in value:
            if t.base == "string":
                lines += [str(len(row)), *row]
            else:
                lines.append(" ".join([str(len(row)), *(_input_token(t.base, v) for v in row)]))
        return "\n".join(lines)
    if t.kind == "treenode":
        tokens = ["null" if v is None else str(v) for v in value]
        return f"{len(tokens)}\n" + " ".join(tokens) if tokens else "0"
    raise AssertionError(t)


def encode_input(sig: Signature, values: list) -> str:
    return "\n".join(encode_value(p.type, v) for p, v in zip(sig.params, values)) + "\n"


def encode_output(t: Type, value) -> str:
    """답으로 기대하는 출력. 드라이버가 반환값을 출력하는 모양과 같다."""
    if t.kind == "scalar":
        return value if t.base == "string" else _token(t.base, value)
    if t.kind == "array" and t.dims == 1 and t.base == "string":
        return "\n".join(value)
    if t.kind == "array" and t.dims == 2:
        return "\n".join(" ".join(_token(t.base, v) for v in row) for row in value)
    if t.kind == "treenode":
        tokens = ["null" if v is None else str(v) for v in value]
        while tokens and tokens[-1] == "null":
            tokens.pop()
        return " ".join(tokens)
    base = "int" if t.kind == "listnode" else t.base
    return " ".join(_token(base, v) for v in value)


# ── LeetCode 표기 ─────────────────────────────────────────────────────────────


def format_value(t: Type, value) -> str:
    """LeetCode가 예제에 쓰는 표기. [2,7,11,15], "abc", true, [3,9,20,null,null,15,7]"""
    if t.kind == "scalar":
        if t.base in ("string", "char"):
            return json.dumps(value, ensure_ascii=False)
        # 입력의 실수는 적힌 그대로, 답의 실수는 출력된 그대로(_Raw) 보여 준다.
        return str(value) if t.base == "double" else _token(t.base, value)
    if t.kind == "treenode":
        return "[" + ",".join("null" if v is None else str(v) for v in value) + "]"
    base = Type("scalar", "int" if t.kind == "listnode" else t.base)
    if t.kind == "array" and t.dims == 2:
        return "[" + ",".join("[" + ",".join(format_value(base, v) for v in row) + "]" for row in value) + "]"
    return "[" + ",".join(format_value(base, v) for v in value) + "]"


def format_args(sig: Signature, values: list) -> str:
    """nums = [2,7,11,15], target = 9"""
    return ", ".join(f"{p.name} = {format_value(p.type, v)}" for p, v in zip(sig.params, values))


class _Parser:
    """LeetCode 표기의 값(JSON과 거의 같다)을 읽는다."""

    _NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")

    def __init__(self, text: str):
        self.text, self.i = text, 0

    def skip(self):
        while self.i < len(self.text) and self.text[self.i] in " \t\r\n":
            self.i += 1

    def done(self) -> bool:
        self.skip()
        return self.i >= len(self.text)

    def peek(self) -> str:
        self.skip()
        return self.text[self.i] if self.i < len(self.text) else ""

    def expect(self, char: str):
        if self.peek() != char:
            raise SignatureError(f"'{char}'가 와야 할 자리입니다: {self.text[self.i:self.i + 20]!r}")
        self.i += 1

    def name(self):
        """'nums =' 꼴이면 이름을, 아니면 None을 돌려준다."""
        self.skip()
        match = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=").match(self.text, self.i)
        if not match:
            return None
        self.i = match.end()
        return match.group(1)

    def value(self):
        char = self.peek()
        if char == "[":
            self.i += 1
            items = []
            if self.peek() == "]":
                self.i += 1
                return items
            while True:
                items.append(self.value())
                if self.peek() == ",":
                    self.i += 1
                    continue
                self.expect("]")
                return items
        if char == '"':
            decoder = json.JSONDecoder()
            try:
                value, end = decoder.raw_decode(self.text, self.i)
            except ValueError:
                raise SignatureError("문자열의 따옴표나 이스케이프가 맞지 않습니다.") from None
            self.i = end
            return value
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.i):
                self.i += len(word)
                return value
        match = self._NUMBER.match(self.text, self.i)
        if match:
            self.i = match.end()
            token = match.group(0)
            return float(token) if any(c in token for c in ".eE") else int(token)
        raise SignatureError(f"값을 읽을 수 없습니다: {self.text[self.i:self.i + 20]!r}")


def _coerce(t: Type, value, where: str):
    """LeetCode 표기에서 읽은 값을 자료형에 맞는지 확인하고 맞춘다."""
    def scalar(base, v):
        if base in ("int", "long") and isinstance(v, int) and not isinstance(v, bool):
            if base == "int" and not -2**31 <= v < 2**31:
                raise SignatureError(f"{where}: int 범위를 넘습니다: {v}")
            return v
        if base == "double" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if base == "bool" and isinstance(v, bool):
            return v
        if base == "string" and isinstance(v, str):
            if "\n" in v or "\r" in v:
                raise SignatureError(f"{where}: 문자열에 줄바꿈은 넣을 수 없습니다.")
            return v
        if base == "char" and isinstance(v, str) and len(v) == 1 and v.strip():
            return v
        raise SignatureError(f"{where}: {base} 값이 아닙니다: {json.dumps(v, ensure_ascii=False)}")

    def array(base, v, dims):
        if not isinstance(v, list):
            raise SignatureError(f"{where}: 배열([...])이어야 합니다.")
        return [array(base, x, dims - 1) if dims > 1 else scalar(base, x) for x in v]

    if t.kind == "scalar":
        return scalar(t.base, value)
    if t.kind == "array":
        return array(t.base, value, t.dims)
    if t.kind == "listnode":
        return array("int", value, 1)
    if t.kind == "treenode":
        if not isinstance(value, list):
            raise SignatureError(f"{where}: 트리는 [3,9,20,null,null,15,7]처럼 적어 주세요.")
        if value and value[0] is None:
            raise SignatureError(f"{where}: 트리의 첫 값(루트)은 null일 수 없습니다. 빈 트리는 []로 적어 주세요.")
        return [None if x is None else scalar("int", x) for x in value]
    raise AssertionError(t)


def parse_leetcode_input(sig: Signature, text: str) -> list:
    """
    LeetCode 예제의 입력을 인자 값 목록으로. 두 가지 꼴을 받는다.
        nums = [2,7,11,15], target = 9
        [2,7,11,15]      (값마다 한 줄, LeetCode의 테스트 입력 칸 모양)
        9
    """
    parser = _Parser(text)
    first = parser.name()
    named = {}
    if first is not None:
        name = first
        while True:
            if name in named:
                raise SignatureError(f"{name}을(를) 두 번 적었습니다.")
            named[name] = parser.value()
            if parser.done():
                break
            parser.expect(",")
            name = parser.name()
            if name is None:
                raise SignatureError("'이름 = 값' 꼴로 이어서 적어 주세요.")
        expected = [p.name for p in sig.params]
        if set(named) != set(expected):
            raise SignatureError(f"인자 이름이 맞지 않습니다. 필요한 인자: {', '.join(expected) or '(없음)'}")
        raw = [named[p.name] for p in sig.params]
    else:
        raw = []
        while not parser.done():
            raw.append(parser.value())
            if parser.peek() == ",":
                parser.i += 1
        if len(raw) != len(sig.params):
            raise SignatureError(f"인자는 {len(sig.params)}개인데 값이 {len(raw)}개입니다.")
    return [_coerce(p.type, v, p.name) for p, v in zip(sig.params, raw)]


def parse_leetcode_output(sig: Signature, text: str):
    parser = _Parser(text)
    if parser.done():
        raise SignatureError("출력 값을 적어 주세요.")
    value = parser.value()
    if not parser.done():
        raise SignatureError("출력에는 값 하나만 적어 주세요.")
    return _coerce(sig.output_type, value, "출력")


def leetcode_to_oj(sig: Signature, input_text: str, output_text: str):
    """관리자 화면의 도우미: LeetCode 표기 예제를 저장할 입출력 텍스트로 바꾼다."""
    return encode_input(sig, parse_leetcode_input(sig, input_text)), encode_output(
        sig.output_type, parse_leetcode_output(sig, output_text)
    )


def oj_to_leetcode(sig: Signature, input_text: str, output_text: str):
    """함수 구현 화면에서 예제를 보여 줄 표기. 읽을 수 없는 쪽은 None"""
    try:
        shown_input = format_args(sig, decode_input(sig, input_text))
    except (SignatureError, ValueError):
        shown_input = None
    try:
        shown_output = format_value(sig.output_type, decode_output(sig.output_type, output_text))
    except (SignatureError, ValueError):
        shown_output = None
    return shown_input, shown_output
