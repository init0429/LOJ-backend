"""Perl: 함수(sub) 하나. 배열은 배열 참조로 주고받는다. (LeetCode에는 Perl이 없어 Ruby와 같은 꼴로 맞췄다.)"""
from .common import comment_block, output_index, output_spec_json, specs_json, uses
from .types import Signature, Type

PRELUDE = "use List::Util qw(max min sum sum0 first reduce any all none uniq shuffle maxstr minstr); use POSIX qw(floor ceil);"

LIST_NODE = [
    "package ListNode;",
    "sub new {",
    "    my ($class, $val, $next) = @_;",
    "    return bless { val => $val // 0, next => $next }, $class;",
    "}",
    "package main;",
]
TREE_NODE = [
    "package TreeNode;",
    "sub new {",
    "    my ($class, $val, $left, $right) = @_;",
    "    return bless { val => $val // 0, left => $left, right => $right }, $class;",
    "}",
    "package main;",
]

_SCALAR = {"int": "Integer", "long": "Integer", "double": "Float", "bool": "Boolean", "char": "Character", "string": "String"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _SCALAR[t.base]
    if t.kind == "array":
        return _SCALAR[t.base] + "[]" * t.dims + " (array reference)"
    return {"listnode": "ListNode", "treenode": "TreeNode", "void": "Void"}[t.kind]


def starter(sig: Signature) -> str:
    lines = []
    if uses(sig, "listnode"):
        lines += ["# Definition for singly-linked list. Fields: $node->{val}, $node->{next}",
                  comment_block(LIST_NODE[:-1], "# ")]
    if uses(sig, "treenode"):
        lines += ["# Definition for a binary tree node. Fields: $node->{val}, $node->{left}, $node->{right}",
                  comment_block(TREE_NODE[:-1], "# ")]
    lines += [f"# @param {{{type_name(p.type)}}} {p.name}" for p in sig.params]
    if sig.returns.kind == "void":
        lines.append(f"# @return {{Void}} Do not return anything, modify {sig.output_param} in-place instead.")
    else:
        lines.append(f"# @return {{{type_name(sig.returns)}}}")
    lines.append(f"sub {sig.function} {{")
    if sig.params:
        lines.append(f"    my ({', '.join('$' + p.name for p in sig.params)}) = @_;")
    lines += ["    ", "}"]
    return "\n".join(lines) + "\n"


RUNTIME = r'''
{
    package LojJudgeReader;
    sub new {
        my ($class, $text) = @_;
        my @lines = map { s/\r\z//r } split /\n/, $text, -1;
        return bless { lines => \@lines, li => 0, col => 0 }, $class;
    }
    sub token {
        my ($self) = @_;
        my $lines = $self->{lines};
        while ($self->{li} < @$lines) {
            my $line = $lines->[$self->{li}];
            pos($line) = $self->{col};
            if ($line =~ /\G[ \t]*([^ \t]+)/gc) {
                $self->{col} = pos($line);
                return $1;
            }
            $self->{li}++;
            $self->{col} = 0;
        }
        die "input ended early\n";
    }
    sub line {
        my ($self) = @_;
        if ($self->{col} > 0) { $self->{li}++; $self->{col} = 0; }
        return "" if $self->{li} >= @{$self->{lines}};
        return $self->{lines}[$self->{li}++];
    }
}

sub loj_judge_scalar {
    my ($r, $base) = @_;
    return $r->line if $base eq "string";
    my $t = $r->token;
    if ($base eq "bool") { my $truthy = $t eq "true" || $t eq "1"; return $truthy ? 1 : 0; }
    return $t if $base eq "char";
    return 0 + $t;
}

sub loj_judge_read {
    my ($r, $spec) = @_;
    my ($kind, $base, $dims) = @$spec;
    return loj_judge_scalar($r, $base) if $kind eq "scalar";
    if ($kind eq "array") {
        my $n = 0 + $r->token;
        return [map { $dims == 1 ? loj_judge_scalar($r, $base) : loj_judge_read($r, ["array", $base, 1]) } 1 .. $n];
    }
    if ($kind eq "listnode") {
        my $n = 0 + $r->token;
        my @values = map { 0 + $r->token } 1 .. $n;
        my $head;
        $head = ListNode->new($_, $head) for reverse @values;
        return $head;
    }
    my $n = 0 + $r->token;
    my @tokens = map { $r->token } 1 .. $n;
    return undef if !@tokens || $tokens[0] eq "null";
    my $root = TreeNode->new(0 + $tokens[0]);
    my @queue = ($root);
    my $i = 1;
    for (my $q = 0; $q < @queue && $i < @tokens; $q++) {
        for my $side ("left", "right") {
            if ($i < @tokens && $tokens[$i] ne "null") {
                $queue[$q]{$side} = TreeNode->new(0 + $tokens[$i]);
                push @queue, $queue[$q]{$side};
            }
            $i++;
        }
    }
    return $root;
}

sub loj_judge_token {
    my ($base, $value) = @_;
    return $value ? "true" : "false" if $base eq "bool";
    if ($base eq "double") {
        my $text = sprintf("%.5f", $value);
        return $text eq "-0.00000" ? "0.00000" : $text;
    }
    return "$value";
}

sub loj_judge_write {
    my ($spec, $value) = @_;
    my ($kind, $base, $dims) = @$spec;
    return $base eq "string" ? "$value" : loj_judge_token($base, $value) if $kind eq "scalar";
    if ($kind eq "array") {
        return join("\n", @$value) if $dims == 1 && $base eq "string";
        return join(" ", map { loj_judge_token($base, $_) } @$value) if $dims == 1;
        return join("\n", map { my $row = $_; join(" ", map { loj_judge_token($base, $_) } @$row) } @$value);
    }
    if ($kind eq "listnode") {
        my @values;
        for (my $node = $value; defined $node; $node = $node->{next}) { push @values, $node->{val}; }
        return join(" ", @values);
    }
    my @tokens;
    my @queue = ($value);
    for (my $q = 0; $q < @queue; $q++) {
        my $node = $queue[$q];
        if (!defined $node) { push @tokens, "null"; next; }
        push @tokens, $node->{val};
        push @queue, $node->{left}, $node->{right};
    }
    pop @tokens while @tokens && $tokens[-1] eq "null";
    return join(" ", @tokens);
}

sub loj_judge_main {
    my ($function, $params, $output, $output_index) = @_;
    my $reader = LojJudgeReader->new(do { local $/; <STDIN> });
    my @args = map { loj_judge_read($reader, $_) } @$params;
    no strict "refs";
    my @result = &{"main::$function"}(@args);
    # 배열은 참조가 아닌 목록으로 돌려줘도 받아 준다. 나머지는 마지막 값(빈 리스트·트리면 undef).
    my $result = $output->[0] ne "array" ? $result[-1]
        : (@result == 1 && ref $result[0] eq "ARRAY") ? $result[0] : \@result;
    print loj_judge_write($output, $output_index >= 0 ? $args[$output_index] : $result), "\n";
}
'''


def compose(sig: Signature, code: str) -> str:
    nodes = ""
    if uses(sig, "listnode"):
        nodes += "\n".join(LIST_NODE) + "\n"
    if uses(sig, "treenode"):
        nodes += "\n".join(TREE_NODE) + "\n"
    # 줄 번호 지시문으로 오류의 파일 이름과 줄 번호를 사용자 코드 기준으로 맞춘다.
    return (
        PRELUDE + "\n# line 1 \"solution.pl\"\n" + code
        + "\n# line 1 \"driver.pl\"\n" + nodes + RUNTIME
        + f"\nloj_judge_main(\"{sig.function}\", {specs_json(sig)}, {output_spec_json(sig)}, {output_index(sig)});\n"
    )
