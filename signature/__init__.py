"""
함수형 문제(LeetCode식 class Solution) 지원.

문제에 시그니처가 있으면 푸는 사람이 두 가지 중 하나로 낸다.
- program: 지금처럼 전체 프로그램. 표준 입력을 읽고 표준 출력에 답을 쓴다.
- function: 함수(메서드)만 쓴다. compose()가 기본 라이브러리, 사용자 코드, 드라이버를 합쳐
  완성된 프로그램을 만든다. 드라이버는 표준 입력을 인자로 바꿔 함수를 부르고 반환값을 출력한다.
테스트 데이터와 채점기는 두 방식이 똑같이 쓴다(입출력 규칙은 codec.py).
"""
from . import lang_c, lang_cpp, lang_java, lang_js, lang_perl, lang_php, lang_python, lang_ruby
from .codec import (
    decode_input,
    decode_output,
    encode_input,
    encode_output,
    format_args,
    format_value,
    leetcode_to_oj,
    normalize_output,
    oj_to_leetcode,
    parse_leetcode_input,
    parse_leetcode_output,
)
from .types import Signature, SignatureError, Type, parse_signature, parse_type

# 함수 구현으로 낼 수 있는 언어. SQL은 함수가 아니므로 뺀다.
LANGUAGES = {
    "c": (lang_c.starter, lang_c.compose),
    "cpp": (lang_cpp.starter, lang_cpp.compose),
    "java": (lang_java.starter, lang_java.compose),
    "python": (lang_python.starter, lang_python.compose),
    "javascript": (lang_js.starter_js, lang_js.compose_js),
    "typescript": (lang_js.starter_ts, lang_js.compose_ts),
    "ruby": (lang_ruby.starter, lang_ruby.compose),
    "php": (lang_php.starter, lang_php.compose),
    "perl": (lang_perl.starter, lang_perl.compose),
}


def starter(sig: Signature, language: str) -> str:
    """편집기에 처음 보여 줄 코드"""
    return LANGUAGES[language][0](sig)


def starters(sig: Signature) -> dict:
    return {language: make(sig) for language, (make, _) in LANGUAGES.items()}


def compose(sig: Signature, language: str, code: str) -> str:
    """함수 구현으로 낸 코드를 채점기가 그대로 컴파일·실행할 수 있는 프로그램으로 합친다."""
    if language not in LANGUAGES:
        raise SignatureError("이 언어는 함수 구현으로 낼 수 없습니다. 전체 프로그램으로 내 주세요.")
    return LANGUAGES[language][1](sig, code)


__all__ = [
    "LANGUAGES", "Signature", "SignatureError", "Type", "compose", "decode_input", "decode_output",
    "encode_input", "encode_output", "format_args", "format_value", "leetcode_to_oj", "normalize_output", "oj_to_leetcode",
    "parse_leetcode_input", "parse_leetcode_output", "parse_signature", "parse_type", "starter", "starters",
]
