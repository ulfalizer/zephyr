#!/usr/bin/env python3

# Copyright (c) 2019, Nordic Semiconductor ASA and Ulf Magnusson
# SPDX-License-Identifier: ISC

import os
import shutil
import sys

import dtlib


def fail(msg):
    sys.exit("test failed: {}".format(msg))


def parse(dts, include_path=()):
    open(".tmp.dts", "w").write(dts)
    return dtlib.DT(".tmp.dts", include_path)


def verify_parse(dts, expected, include_path=()):
    # The [1:] is so that the first line can be put on a separate line
    # after """
    dt = parse(dts[1:], include_path)

    actual = str(dt)
    expected = expected[1:-1]
    if actual != expected:
        fail("expected '{}' to parse as '{}', parsed as '{}'"
             .format(dts, expected, actual))

    return dt


def verify_error(dts, msg):
    prefix = "expected '{}' to generate the error '{}', generated" \
             .format(dts, msg)
    try:
        parse(dts[1:])
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


# These might already exist from failed earlier runs

try:
    os.mkdir(".tmp")
except OSError:
    pass

try:
    os.mkdir(".tmp2")
except OSError:
    pass

#
# Test cell parsing
#

verify_parse("""
/dts-v1/;

/ {
	a = < >;
	b = < 10 20 >;
	c = < 0U 1L 2UL 3LL 4ULL >;
	d = < 0x10 0x20 >;
	e = < 010 020 >;
	f = /bits/ 8 < 0x10 0x20 (-1) >;
	g = /bits/ 16 < 0x10 0x20 (-1) >;
	h = /bits/ 32 < 0x10 0x20 (-1) >;
	i = /bits/ 64 < 0x10 0x20 (-1) >;
};
""",
"""
/dts-v1/;

/ {
	a;
	b = [ 00 00 00 0A 00 00 00 14 ];
	c = [ 00 00 00 00 00 00 00 01 00 00 00 02 00 00 00 03 00 00 00 04 ];
	d = [ 00 00 00 10 00 00 00 20 ];
	e = [ 00 00 00 08 00 00 00 10 ];
	f = [ 10 20 FF ];
	g = [ 00 10 00 20 FF FF ];
	h = [ 00 00 00 10 00 00 00 20 FF FF FF FF ];
	i = [ 00 00 00 00 00 00 00 10 00 00 00 00 00 00 00 20 FF FF FF FF FF FF FF FF ];
};
""")

verify_error("""
/dts-v1/;

/ {
	a = /bits/ 16 < 0x10000 >;
};
""",
".tmp.dts:4 (column 18): parse error: 65536 does not fit in 16 bits")

verify_error("""
/dts-v1/;

/ {
	a = < 0x100000000 >;
};
""",
".tmp.dts:4 (column 8): parse error: 4294967296 does not fit in 32 bits")

verify_error("""
/dts-v1/;

/ {
	a = /bits/ 128 < 0 >;
};
""",
".tmp.dts:4 (column 13): parse error: expected 8, 16, 32, or 64")

#
# Test bytes parsing
#

verify_parse("""
/dts-v1/;

/ {
	a = [ ];
	b = [ 12 34 ];
	c = [ 1234 ];
};
""",
"""
/dts-v1/;

/ {
	a;
	b = [ 12 34 ];
	c = [ 12 34 ];
};
""")

verify_error("""
/dts-v1/;

/ {
	a = [ 123 ];
};
""",
".tmp.dts:4 (column 10): parse error: expected two-digit byte or ']'")

#
# Test string parsing
#

verify_parse(r"""
/dts-v1/;

/ {
	a = "";
	b = "ABC";
	c = "\\\"\xAB\377\a\b\t\n\v\f\r";
};
""",
"""
/dts-v1/;

/ {
	a = [ 00 ];
	b = [ 41 42 43 00 ];
	c = [ 5C 22 AB FF 07 08 09 0A 0B 0C 0D 00 ];
};
""")

verify_error(r"""
/dts-v1/;

/ {
	a = "\400";
};
""",
".tmp.dts:4 (column 6): parse error: octal escape out of range (> 255)")

#
# Test /incbin/
#

open(".tmp.bin", "wb").write(b"\00\01\02\03")

verify_parse("""
/dts-v1/;

/ {
	a = /incbin/ (".tmp.bin");
	b = /incbin/ (".tmp.bin", 1, 1);
	c = /incbin/ (".tmp.bin", 1, 2);
};
""",
"""
/dts-v1/;

/ {
	a = [ 00 01 02 03 ];
	b = [ 01 ];
	c = [ 01 02 ];
};
""")

open(".tmp/in_subdir", "wb").write(b"\00\01\02")

verify_parse("""
/dts-v1/;

/ {
	a = /incbin/ ("in_subdir");
};
""",
"""
/dts-v1/;

/ {
	a = [ 00 01 02 ];
};
""",
include_path=(".tmp",))

verify_error(r"""
/dts-v1/;

/ {
	a = /incbin/ ("missing");
};
""",
".tmp.dts:4 (column 25): parse error: 'missing' could not be found")

#
# Test node merging
#

verify_parse("""
/dts-v1/;

/ {
	l1: l2: foo {
		foo1 = [ 01 ];
		l4: l5: bar {
			bar1 = [ 01 ];
		};
	};
};

l3: &l1 {
	foo2 = [ 02 ];
	l6: l7: bar {
		bar2 = [ 02 ];
	};
};

&l3 {
	foo3 = [ 03 ];
};

&{/foo} {
	foo4 = [ 04 ];
};

&{/foo/bar} {
	bar3 = [ 03 ];
	l8: baz {};
};

/ {
};

/ {
	top = [ 01 ];
};
""",
"""
/dts-v1/;

/ {
	top = [ 01 ];
	l1: l2: l3: foo {
		foo1 = [ 01 ];
		foo2 = [ 02 ];
		foo3 = [ 03 ];
		foo4 = [ 04 ];
		l4: l5: l6: l7: bar {
			bar1 = [ 01 ];
			bar2 = [ 02 ];
			bar3 = [ 03 ];
			l8: baz {
			};
		};
	};
};
""")

verify_error("""
/dts-v1/;

/ {
};

&missing {
};
""",
".tmp.dts:6 (column 1): parse error: undefined node label 'missing'")

verify_error("""
/dts-v1/;

/ {
};

&{foo} {
};
""",
".tmp.dts:6 (column 1): parse error: node path does not start with '/'")

verify_error("""
/dts-v1/;

/ {
};

&{/foo} {
};
""",
".tmp.dts:6 (column 1): parse error: component 1 ('foo') in path '/foo' does not exist")

#
# Test property labels
#


def verify_label_to_prop(label, expected):
    actual = dt.label_to_prop[label].name
    if actual != expected:
        fail("expected label '{}' to map to prop '{}', mapped to prop '{}'"
             .format(label, expected, actual))


dt = verify_parse("""
/dts-v1/;

/ {
	a;
	b;
	l2: c;
	l4: l5: l5: d = < 0 >;
};

/ {
	l1: b;
	l3: c;
	l6: d;
};
""",
"""
/dts-v1/;

/ {
	a;
	l1: b;
	l2: l3: c;
	l4: l5: l6: d = [ 00 00 00 00 ];
};
""")

verify_label_to_prop("l1", "b")
verify_label_to_prop("l2", "c")
verify_label_to_prop("l3", "c")
verify_label_to_prop("l4", "d")
verify_label_to_prop("l5", "d")
verify_label_to_prop("l6", "d")

#
# Test offset labels
#


def verify_label_to_offset(label, expected_prop, expected_offset):
    actual_prop, actual_offset = dt.label_to_prop_offset[label]
    actual_prop = actual_prop.name
    if (actual_prop, actual_offset) != (expected_prop, expected_offset):
        fail("expected label '{}' to map to offset {} on prop '{}', mapped "
             "to offset {} on prop '{}'"
             .format(label, expected_offset, expected_prop, actual_offset,
                     actual_prop))


dt = verify_parse("""
/dts-v1/;

/ {
    a = l01: l02: < l03: l04: &node l05: l06: 2 l07: l08: > l09: l10:,
        l11: l12: [ l13: l14: 03 l15: l16: 04 l17: l18: ] l19: l20:,
        l21: l22: "A";

    b = < 0 > l23: l24:;

    node: node {
    };
};
""",
"""
/dts-v1/;

/ {
	a = [ l01: l02: l03: l04: 00 00 00 01 l05: l06: 00 00 00 02 l07: l08: l09: l10: l11: l12: l13: l14: 03 l15: l16: 04 l17: l18: l19: l20: l21: l22: 41 00 ];
	b = [ 00 00 00 00 l23: l24: ];
	node: node {
		phandle = [ 00 00 00 01 ];
	};
};
""")

verify_label_to_offset("l01", "a", 0)
verify_label_to_offset("l04", "a", 0)
verify_label_to_offset("l05", "a", 4)
verify_label_to_offset("l14", "a", 8)
verify_label_to_offset("l15", "a", 9)
verify_label_to_offset("l22", "a", 10)

verify_label_to_offset("l23", "b", 4)
verify_label_to_offset("l24", "b", 4)

#
# Test node path references
#

verify_parse("""
/dts-v1/;

/ {
	a = &label;
	b = &{/abc};
	label: abc {
		c = &label;
		d = &{/abc};
	};
};
""",
"""
/dts-v1/;

/ {
	a = [ 2F 61 62 63 00 ];
	b = [ 2F 61 62 63 00 ];
	label: abc {
		c = [ 2F 61 62 63 00 ];
		d = [ 2F 61 62 63 00 ];
	};
};
""")

verify_error("""
/dts-v1/;

/ {
	sub {
		x = &missing;
	};
};
""",
"/sub: undefined node label 'missing'")

verify_error("""
/dts-v1/;

/ {
	sub {
		x = &{/sub/missing};
	};
};
""",
"/sub: component 2 ('missing') in path '/sub/missing' does not exist")

#
# Test phandles
#

# Check that existing phandles are used (and not reused)
verify_parse("""
/dts-v1/;

/ {
	x = < &a &{/b} &c >;

	dummy1 {
		phandle = < 1 >;
	};

	dummy2 {
		phandle = < 3 >;
	};

	a: a {
	};

	b {
	};

	c: c {
		phandle = < 0xFF >;
	};
};
""",
"""
/dts-v1/;

/ {
	x = [ 00 00 00 02 00 00 00 04 00 00 00 FF ];
	dummy1 {
		phandle = [ 00 00 00 01 ];
	};
	dummy2 {
		phandle = [ 00 00 00 03 ];
	};
	a: a {
		phandle = [ 00 00 00 02 ];
	};
	b {
		phandle = [ 00 00 00 04 ];
	};
	c: c {
		phandle = [ 00 00 00 FF ];
	};
};
""")

# Check that a node can be assigned a phandle to itself. This just forces a
# phandle to be allocated on it. The C tools support this too.
verify_parse("""
/dts-v1/;

/ {
	dummy {
		phandle = < 1 >;
	};

	a {
                foo: phandle = < &{/a} >;
	};

	label: b {
                bar: phandle = < &label >;
	};
};
""",
"""
/dts-v1/;

/ {
	dummy {
		phandle = [ 00 00 00 01 ];
	};
	a {
		foo: phandle = [ 00 00 00 02 ];
	};
	label: b {
		bar: phandle = [ 00 00 00 03 ];
	};
};
""")

verify_error("""
/dts-v1/;

/ {
	sub {
		x = < &missing >;
	};
};
""",
"/sub: undefined node label 'missing'")

verify_error("""
/dts-v1/;

/ {
	a: sub {
		x = /bits/ 16 < &a >;
	};
};
""",
".tmp.dts:5 (column 19): parse error: phandle references are only allowed in arrays with 32-bit elements")

verify_error("""
/dts-v1/;

/ {
	foo {
		phandle = [ 00 ];
	};
};
""",
"/foo: bad phandle length (1), expected 4 bytes")

verify_error("""
/dts-v1/;

/ {
	foo {
		phandle = < 0 >;
	};
};
""",
"/foo: bad value 0x00000000 for phandle")

verify_error("""
/dts-v1/;

/ {
	foo {
		phandle = < (-1) >;
	};
};
""",
"/foo: bad value 0xffffffff for phandle")

verify_error("""
/dts-v1/;

/ {
	foo {
		phandle = < 17 >;
	};

	bar {
		phandle = < 17 >;
	};
};
""",
"/bar: duplicated phandle 0x11 (seen before at /foo)")

verify_error("""
/dts-v1/;

/ {
	foo {
		phandle = < &{/bar} >;
	};

	bar {
	};
};
""",
"/foo: phandle refers to another node")

# Test phandle_to_node


def verify_phandle_to_node(prop, offset, expected_name):
    phandle = dtlib.to_num(dt.root.props[prop].value[offset:offset + 4])
    actual_name = dt.phandle_to_node[phandle].name

    if actual_name != expected_name:
        fail("expected {} to be a phandle for {}, was a phandle for {}"
             .format(prop, expected_name, actual_name))


dt = parse("""
/dts-v1/;

/ {
	phandle_ = < &{/node1} 0 1 >;
	phandles = < 0 &{/node2} 1 &{/node3} >;

	node1 {
		phandle = < 123 >;
	};

	node2 {
	};

	node3 {
	};
};
""")

verify_phandle_to_node("phandle_", 0, "node1")
verify_phandle_to_node("phandles", 4, "node2")
verify_phandle_to_node("phandles", 12, "node3")


#
# Test mixed value type assignments
#

verify_parse("""
/dts-v1/;

/ {
	x = /bits/ 8 < 0xFF 0xFF >,
	    &abc,
	    < 0xFF &abc 0xFF &abc >,
	    &abc,
	    [ FF FF ],
	    "abc";

	abc: abc {
	};
};
""",
"""
/dts-v1/;

/ {
	x = [ FF FF 2F 61 62 63 00 00 00 00 FF 00 00 00 01 00 00 00 FF 00 00 00 01 2F 61 62 63 00 FF FF 61 62 63 00 ];
	abc: abc {
		phandle = [ 00 00 00 01 ];
	};
};
""")

#
# Test property deletion
#

verify_parse("""
/dts-v1/;

/ {
	keep = < 1 >;
	delete = < &sub >, &sub;
	/delete-property/ missing;
	/delete-property/ delete;
	sub: sub {
		y = < &sub >, &sub;
	};
};

&sub {
	/delete-property/ y;
};
""",
"""
/dts-v1/;

/ {
	keep = [ 00 00 00 01 ];
	sub: sub {
	};
};
""")

#
# Test node deletion
#

verify_parse("""
/dts-v1/;

/ {
	sub1 {
		x = < 1 >;
		sub2 {
			x = < &sub >, &sub;
		};
		/delete-node/ sub2;
	};

	sub3: sub3 {
		x = < &sub >, &sub;
	};

	sub4 {
		x = < &sub >, &sub;
	};
};

/delete-node/ &sub3;
/delete-node/ &{/sub4};
""",
"""
/dts-v1/;

/ {
	sub1 {
		x = [ 00 00 00 01 ];
	};
};
""")

verify_error("""
/dts-v1/;

/ {
};

/delete-node/ &missing;
""",
".tmp.dts:6 (column 15): parse error: undefined node label 'missing'")

#
# Test /include/ (which is handled in the lexer)
#

# Verify that /include/ searches the current directory

open(".tmp/same-dir-1", "w").write("""
	x = [ 00 ];
	/include/ "same-dir-2"
""")
open(".tmp/same-dir-2", "w").write("""
	y = [ 01 ];
	/include/ "same-dir-3"
""")
open(".tmp/same-dir-3", "w").write("""
	z = [ 02 ];
""")

verify_parse("""
/dts-v1/;

/ {
	/include/ ".tmp/same-dir-1"
};
""",
"""
/dts-v1/;

/ {
	x = [ 00 ];
	y = [ 01 ];
	z = [ 02 ];
};
""")

# Test tricky includes and include paths

open(".tmp2.dts", "w").write("""
/dts-v1/;
/ {
""")
open(".tmp3.dts", "w").write("""
    x = <1>;
""")
open(".tmp/via-include-path-1", "w").write("""
      = /include/ "via-include-path-2"
""")
open(".tmp2/via-include-path-2", "w").write("""
        <2>;
};
""")

verify_parse("""
/include/ ".tmp2.dts"
/include/ ".tmp3.dts"
y /include/ "via-include-path-1"
""",
"""
/dts-v1/;

/ {
	x = [ 00 00 00 01 ];
	y = [ 00 00 00 02 ];
};
""",
include_path=(".tmp", ".tmp2",))

verify_error("""
/include/ "missing"
""",
".tmp.dts:1 (column 1): parse error: 'missing' could not be found")

# Verify that an error in an included file points to the right location

open(".tmp2.dts", "w").write("""\


  x
""")

verify_error("""


/include/ ".tmp2.dts"
""",
".tmp2.dts:3 (column 3): parse error: expected /dts-v1/ -- other versions are not supported")

# Test recursive /include/ detection

open(".tmp2.dts", "w").write("""\
/include/ ".tmp3.dts"
""")
open(".tmp3.dts", "w").write("""\
/include/ ".tmp.dts"
""")

verify_error("""
/include/ ".tmp2.dts"
""",
"""\
.tmp3.dts:1 (column 1): parse error: recursive /include/:
.tmp.dts:1 ->
.tmp2.dts:1 ->
.tmp3.dts:1 ->
.tmp.dts\
""")

verify_error("""
/include/ ".tmp.dts"
""",
"""\
.tmp.dts:1 (column 1): parse error: recursive /include/:
.tmp.dts:1 ->
.tmp.dts\
""")

#
# Test /omit-if-no-ref/
#

verify_parse("""
/dts-v1/;

/ {
	x = < &{/referenced} >, &referenced2;

	/omit-if-no-ref/ referenced {
	};

	referenced2: referenced2 {
	};

	/omit-if-no-ref/ unreferenced {
	};

	l1: /omit-if-no-ref/ unreferenced2 {
	};

	/omit-if-no-ref/ l2: unreferenced3 {
	};

	unreferenced4: unreferenced4 {
	};

	unreferenced5 {
	};
};

/omit-if-no-ref/ &referenced2;
/omit-if-no-ref/ &unreferenced4;
/omit-if-no-ref/ &{/unreferenced5};
""",
"""
/dts-v1/;

/ {
	x = [ 00 00 00 01 2F 72 65 66 65 72 65 6E 63 65 64 32 00 ];
	referenced {
		phandle = [ 00 00 00 01 ];
	};
	referenced2: referenced2 {
	};
};
""")

verify_error("""
/dts-v1/;

/ {
	/omit-if-no-ref/ x = "";
};
""",
".tmp.dts:4 (column 21): parse error: /omit-if-no-ref/ can only be used on nodes")

verify_error("""
/dts-v1/;

/ {
	/omit-if-no-ref/ x;
};
""",
".tmp.dts:4 (column 20): parse error: /omit-if-no-ref/ can only be used on nodes")

verify_error("""
/dts-v1/;

/ {
	/omit-if-no-ref/ {
	};
};
""",
".tmp.dts:4 (column 19): parse error: expected node or property name")

verify_error("""
/dts-v1/;

/ {
	/omit-if-no-ref/ = < 0 >;
};
""",
".tmp.dts:4 (column 19): parse error: expected node or property name")

verify_error("""
/dts-v1/;

/ {
};

/omit-if-no-ref/ &missing;
""",
".tmp.dts:6 (column 18): parse error: undefined node label 'missing'")

#
# Test expressions
#

verify_parse("""
/dts-v1/;

/ {
	ter1        = < (0 ? 1 : 0 ? 2 : 3) >;
	ter2        = < (0 ? 1 : 1 ? 2 : 3) >;
	ter3        = < (1 ? 1 : 0 ? 2 : 3) >;
	ter4        = < (1 ? 1 : 1 ? 2 : 3) >;
	or1         = < (0 || 0) >;
	or2         = < (0 || 1) >;
	or3         = < (1 || 0) >;
	or4         = < (1 || 1) >;
	and1        = < (0 && 0) >;
	and2        = < (0 && 1) >;
	and3        = < (1 && 0) >;
	and4        = < (1 && 1) >;
	bitor       = < (1 | 2) >;
	bitxor      = < (7 ^ 2) >;
	bitand      = < (3 & 6) >;
	eq1         = < (1 == 0) >;
	eq2         = < (1 == 1) >;
	neq1        = < (1 != 0) >;
	neq2        = < (1 != 1) >;
	lt1         = < (1 < 2) >;
	lt2         = < (2 < 2) >;
	lt3         = < (3 < 2) >;
	lteq1       = < (1 <= 2) >;
	lteq2       = < (2 <= 2) >;
	lteq3       = < (3 <= 2) >;
	gt1         = < (1 > 2) >;
	gt2         = < (2 > 2) >;
	gt3         = < (3 > 2) >;
	gteq1       = < (1 >= 2) >;
	gteq2       = < (2 >= 2) >;
	gteq3       = < (3 >= 2) >;
	lshift      = < (2 << 3) >;
	rshift      = < (16 >> 3) >;
	add         = < (3 + 4) >;
	sub         = < (7 - 4) >;
	mul         = < (3 * 4) >;
	div         = < (11 / 3) >;
	mod         = < (11 % 3) >;
	unary_minus = < (-3) >;
	bitnot      = < (~1) >;
	not0        = < (!-1) >;
	not1        = < (!0) >;
	not2        = < (!1) >;
	not3        = < (!2) >;
	nest        = < (((--3) + (-2)) * (--(-2))) >;
};
""",
"""
/dts-v1/;

/ {
	ter1 = [ 00 00 00 03 ];
	ter2 = [ 00 00 00 02 ];
	ter3 = [ 00 00 00 01 ];
	ter4 = [ 00 00 00 01 ];
	or1 = [ 00 00 00 00 ];
	or2 = [ 00 00 00 01 ];
	or3 = [ 00 00 00 01 ];
	or4 = [ 00 00 00 01 ];
	and1 = [ 00 00 00 00 ];
	and2 = [ 00 00 00 00 ];
	and3 = [ 00 00 00 00 ];
	and4 = [ 00 00 00 01 ];
	bitor = [ 00 00 00 03 ];
	bitxor = [ 00 00 00 05 ];
	bitand = [ 00 00 00 02 ];
	eq1 = [ 00 00 00 00 ];
	eq2 = [ 00 00 00 01 ];
	neq1 = [ 00 00 00 01 ];
	neq2 = [ 00 00 00 00 ];
	lt1 = [ 00 00 00 01 ];
	lt2 = [ 00 00 00 00 ];
	lt3 = [ 00 00 00 00 ];
	lteq1 = [ 00 00 00 01 ];
	lteq2 = [ 00 00 00 01 ];
	lteq3 = [ 00 00 00 00 ];
	gt1 = [ 00 00 00 00 ];
	gt2 = [ 00 00 00 00 ];
	gt3 = [ 00 00 00 01 ];
	gteq1 = [ 00 00 00 00 ];
	gteq2 = [ 00 00 00 01 ];
	gteq3 = [ 00 00 00 01 ];
	lshift = [ 00 00 00 10 ];
	rshift = [ 00 00 00 02 ];
	add = [ 00 00 00 07 ];
	sub = [ 00 00 00 03 ];
	mul = [ 00 00 00 0C ];
	div = [ 00 00 00 03 ];
	mod = [ 00 00 00 02 ];
	unary_minus = [ FF FF FF FD ];
	bitnot = [ FF FF FF FE ];
	not0 = [ 00 00 00 00 ];
	not1 = [ 00 00 00 01 ];
	not2 = [ 00 00 00 00 ];
	not3 = [ 00 00 00 00 ];
	nest = [ FF FF FF FE ];
};
""")

verify_error("""
/dts-v1/;

/ {
	a = < (1/(-1 + 1)) >;
};

/omit-if-no-ref/ &missing;
""",
".tmp.dts:4 (column 18): parse error: division by zero")

verify_error("""
/dts-v1/;

/ {
	a = < (1%0) >;
};
""",
".tmp.dts:4 (column 11): parse error: division by zero")

#
# Test comment removal
#

verify_parse("""
/**//dts-v1//**/;//
//
// foo
/ /**/{// foo
x/**/=/*
foo
*/</**/1/***/>/****/;/**/}/*/**/;
""",
"""
/dts-v1/;

/ {
	x = [ 00 00 00 01 ];
};
""")

#
# Test get_node()
#


def verify_path_is(path, node_name):
    try:
        node = dt.get_node(path)
        if node.name != node_name:
            fail("expected {} to lead to {}, lead to {}"
                 .format(path, node_name, node.name))
    except dtlib.DTError:
        fail("no node found for path " + alias)


def verify_path_error(path, msg):
    prefix = "expected looking up '{}' to generate the error '{}', " \
             "generated ".format(path, msg)
    try:
        dt.get_node(path)
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_path_exists(path):
    if not dt.has_node(path):
        fail("expected path '{}' to exist, didn't".format(path))


def verify_path_missing(path):
    if dt.has_node(path):
        fail("expected path '{}' to not exist, did".format(path))


dt = parse("""
/dts-v1/;

/ {
	foo {
		bar {
		};
	};

	baz {
	};
};
""")

verify_path_is("/", "/")
verify_path_is("//", "/")
verify_path_is("///", "/")
verify_path_is("/foo", "foo")
verify_path_is("//foo", "foo")
verify_path_is("///foo", "foo")
verify_path_is("/foo/bar", "bar")
verify_path_is("//foo//bar", "bar")
verify_path_is("///foo///bar", "bar")
verify_path_is("/baz", "baz")

verify_path_error("",
                  "no alias '' found -- did you forget the leading '/' in the node path?")
verify_path_error("missing",
                  "no alias 'missing' found -- did you forget the leading '/' in the node path?")
verify_path_error("/missing",
                  "component 1 ('missing') in path '/missing' does not exist")
verify_path_error("/foo/missing",
                  "component 2 ('missing') in path '/foo/missing' does not exist")

verify_path_exists("/")
verify_path_exists("/foo")
verify_path_exists("/foo/bar")

verify_path_missing("/missing")
verify_path_missing("/foo/missing")

#
# Test /aliases
#

dt = parse("""
/dts-v1/;

/ {
	aliases {
		alias1 = &l1;
		alias2 = &l2;
		alias3 = &{/sub/node3};
		alias4 = [2F 6E 6F 64 65 34 00]; // "/node4";
	};

	l1: node1 {
	};

	l2: node2 {
	};

	sub {
		node3 {
		};
	};

	node4 {
		node5 {
		};
	};
};
""")

verify_path_is("alias1", "node1")
verify_path_is("alias2", "node2")
verify_path_is("alias3", "node3")
verify_path_is("alias4", "node4")
verify_path_is("alias4/node5", "node5")

verify_path_error("alias4/node5/node6",
                  "component 3 ('node6') in path 'alias4/node5/node6' does not exist")

verify_error("""
/dts-v1/;

/ {
	aliases {
		A = "/aliases";
	};
};
""",
"/aliases: alias property name 'A' should include only characters from [0-9a-z-]")

verify_error(r"""
/dts-v1/;

/ {
	aliases {
		a = "\xFF";
	};
};
""",
r"b'\xff\x00' is not valid UTF-8 (for property 'a' on /aliases)")

verify_error(r"""
/dts-v1/;

/ {
	aliases {
		a = [ 41 ]; // "A"
	};
};
""",
"b'A' is not null-terminated (for property 'a' on /aliases)")

verify_error(r"""
/dts-v1/;

/ {
	aliases {
		a = "/missing";
	};
};
""",
"/aliases: bad path for 'a': component 1 ('missing') in path '/missing' does not exist")

#
# Test to_{num,nums,string,strings}()
#

def verify_to_num(prop, size, signed, expected):
    try:
        actual = dt.root.props[prop].to_num(size, signed)
    except dtlib.DTError as e:
        fail("failed to convert {} to {} number with {} bytes: {}"
             .format(prop, "a signed" if signed else "an unsigned", size, e))

    if actual != expected:
        fail("expected {} to have the {} numeric value {:#x}, had the value "
             "{:#x}".format(prop, "signed" if signed else "unsigned", expected,
                            actual))


def verify_to_num_error(prop, size, msg):
    prefix = "expected {} converted from {} bytes to generate the error " \
             "'{}', generated".format(prop, size, msg)
    try:
        dt.root.props[prop].to_num(size)
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_to_nums(prop, size, signed, expected):
    try:
        actual = dt.root.props[prop].to_nums(size, signed)
    except dtlib.DTError as e:
        fail("failed to convert {} to {} numbers with {} bytes each: {}"
             .format(prop, "signed" if signed else "unsigned", size, e))

    if actual != expected:
        fail("expected {} to give the {} numbers {} for size {}, gave {}"
             .format(prop, "signed" if signed else "unsigned", expected,
                     size, actual))


def verify_to_nums_error(prop, size, msg):
    prefix = "expected {} converted to numbers with {} bytes each to " \
             "generate the error '{}', generated".format(prop, size, msg)
    try:
        dt.root.props[prop].to_nums(size)
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_raw_to_num_error(fn, data, size, msg):
    prefix = "expected {}() called with data='{}', size='{}' to generate " \
             "the error '{}', generated".format(fn.__name__, data, size, msg)
    try:
        dtlib.to_num(data, size)
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_to_string(prop, expected):
    try:
        actual = dt.root.props[prop].to_string()
    except dtlib.DTError as e:
        fail("failed to convert {} to string: {}".format(prop, e))

    if actual != expected:
        fail("expected {} to have the value '{}', had the value '{}'"
             .format(prop, expected, actual))


def verify_to_string_error(prop, msg):
    prefix = "expected converting {} to string to generate the error '{}', " \
             "generated".format(prop, msg)
    try:
        dt.root.props[prop].to_string()
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_raw_to_string_error(data, msg):
    prefix = "expected to_string() called with data='{}' to generate the " \
             "error '{}', generated".format(data, msg)
    try:
        dtlib.to_string(data)
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


def verify_to_strings(prop, expected):
    try:
        actual = dt.root.props[prop].to_strings()
    except dtlib.DTError as e:
        fail("failed to convert {} to strings: {}".format(prop, e))

    if actual != expected:
        fail("expected {} to have the value '{}', had the value '{}'"
             .format(prop, expected, actual))


def verify_to_strings_error(prop, msg):
    prefix = "expected converting {} to strings to generate the error '{}', " \
             "generated".format(prop, msg)
    try:
        dt.root.props[prop].to_string()
        fail(prefix + " no error")
    except dtlib.DTError as e:
        if str(e) != msg:
            fail("{} the error '{}'".format(prefix, e))
    except Exception as e:
        fail("{} the non-DTError '{}'".format(prefix, e))


dt = parse(r"""
/dts-v1/;

/ {
	empty;
	u1 = /bits/ 8 < 0x01 >;
	u2 = /bits/ 8 < 0x01 0x02 >;
	u3 = /bits/ 8 < 0x01 0x02 0x03 >;
	u4 = /bits/ 8 < 0x01 0x02 0x03 0x04 >;
	s1 = /bits/ 8 < 0xFF >;
	s2 = /bits/ 8 < 0xFF 0xFE >;
	s3 = /bits/ 8 < 0xFF 0xFF 0xFD >;
	s4 = /bits/ 8 < 0xFF 0xFF 0xFF 0xFC >;
	empty_string = "";
	string = "foo\tbar baz";
	invalid_string = "\xff";
	non_null_terminated_string = [ 41 ]; // A
	strings = "foo", "bar", "baz";
	invalid_strings = "foo", "\xff", "bar";
	non_null_terminated_strings = "foo", "bar", [ 01 ];
};
""")

# Test to_num()

verify_to_num("u1", 1, False, 0x01)
verify_to_num("u2", 2, False, 0x0102)
verify_to_num("u3", 3, False, 0x010203)
verify_to_num("u4", 4, False, 0x01020304)
verify_to_num("s1", 1, False, 0xFF)
verify_to_num("s2", 2, False, 0xFFFE)
verify_to_num("s3", 3, False, 0xFFFFFD)
verify_to_num("s4", 4, False, 0xFFFFFFFC)

verify_to_num("u1", 1, True, 0x01)
verify_to_num("u2", 2, True, 0x0102)
verify_to_num("u3", 3, True, 0x010203)
verify_to_num("u4", 4, True, 0x01020304)
verify_to_num("s1", 1, True, -1)
verify_to_num("s2", 2, True, -2)
verify_to_num("s3", 3, True, -3)
verify_to_num("s4", 4, True, -4)

verify_to_num("u1", None, False, 0x01)
verify_to_num("u2", None, False, 0x0102)
verify_to_num("u3", None, False, 0x010203)
verify_to_num("u4", None, False, 0x01020304)
verify_to_num("s1", None, False, 0xFF)
verify_to_num("s2", None, False, 0xFFFE)
verify_to_num("s3", None, False, 0xFFFFFD)
verify_to_num("s4", None, False, 0xFFFFFFFC)

verify_to_num("u1", None, True, 0x01)
verify_to_num("u2", None, True, 0x0102)
verify_to_num("u3", None, True, 0x010203)
verify_to_num("u4", None, True, 0x01020304)
verify_to_num("s1", None, True, -1)
verify_to_num("s2", None, True, -2)
verify_to_num("s3", None, True, -3)
verify_to_num("s4", None, True, -4)

verify_to_num_error("u1", 0, "'size' must be greater than zero, was 0 (for property 'u1' on /)")
verify_to_num_error("u1", -1, "'size' must be greater than zero, was -1 (for property 'u1' on /)")
verify_to_num_error("u1", 2, r"b'\x01' is 1 bytes long, expected 2 (for property 'u1' on /)")
verify_to_num_error("u2", 1, r"b'\x01\x02' is 2 bytes long, expected 1 (for property 'u2' on /)")

verify_raw_to_num_error(dtlib.to_num, 0, 0, "'0' has type 'int', expected 'bytes'")
verify_raw_to_num_error(dtlib.to_num, b"", 0, "'size' must be greater than zero, was 0")

# Test to_nums()

verify_to_nums("empty", 1, False, [])
verify_to_nums("u1", 1, False, [1])
verify_to_nums("u2", 1, False, [1, 2])
verify_to_nums("u3", 1, False, [1, 2, 3])
verify_to_nums("u4", 1, False, [1, 2, 3, 4])
verify_to_nums("s1", 1, False, [0xFF])
verify_to_nums("s2", 1, False, [0xFF, 0xFE])
verify_to_nums("s3", 1, False, [0xFF, 0xFF, 0xFD])
verify_to_nums("s4", 1, False, [0xFF, 0xFF, 0xFF, 0xFC])

verify_to_nums("u2", 2, False, [0x0102])
verify_to_nums("u4", 2, False, [0x0102, 0x0304])

verify_to_nums("u1", 1, True, [1])
verify_to_nums("u2", 1, True, [1, 2])
verify_to_nums("u3", 1, True, [1, 2, 3])
verify_to_nums("u4", 1, True, [1, 2, 3, 4])
verify_to_nums("s1", 1, True, [-1])
verify_to_nums("s2", 1, True, [-1, -2])
verify_to_nums("s3", 1, True, [-1, -1, -3])
verify_to_nums("s4", 1, True, [-1, -1, -1, -4])

verify_to_nums("s2", 2, True, [-2])
verify_to_nums("s4", 2, True, [-1, -4])

verify_to_nums_error("u1", 0, "'size' must be greater than zero, was 0 (for property 'u1' on /)")
verify_to_nums_error("u1", 2, r"b'\x01' is 1 bytes long, expected a length that's a multiple of 2 (for property 'u1' on /)")
verify_to_nums_error("u2", 3, r"b'\x01\x02' is 2 bytes long, expected a length that's a multiple of 3 (for property 'u2' on /)")

verify_raw_to_num_error(dtlib.to_nums, 0, 0, "'0' has type 'int', expected 'bytes'")
verify_raw_to_num_error(dtlib.to_nums, b"", 0, "'size' must be greater than zero, was 0")

# Test to_string()

verify_to_string("empty_string", "")
verify_to_string("string", "foo\tbar baz")

verify_to_string_error("invalid_string", r"b'\xff\x00' is not valid UTF-8 (for property 'invalid_string' on /)")
verify_to_string_error("non_null_terminated_string", "b'A' is not null-terminated (for property 'non_null_terminated_string' on /)")

verify_raw_to_string_error(0, "'0' has type 'int', expected 'bytes'")

# Test to_strings()

verify_to_strings("empty_string", [""])
verify_to_strings("string", ["foo\tbar baz"])
verify_to_strings("strings", ["foo", "bar", "baz"])

verify_to_strings_error("invalid_strings", r"b'foo\x00\xff\x00bar\x00' is not valid UTF-8 (for property 'invalid_strings' on /)")
verify_to_strings_error("non_null_terminated_strings", r"b'foo\x00bar\x00\x01' is not null-terminated (for property 'non_null_terminated_strings' on /)")

#
# Test duplicate label error
#

verify_error("""
/dts-v1/;

/ {
	sub1 {
		label: foo {
		};
	};

	sub2 {
		label: bar {
		};
	};
};
""",
"Label 'label' appears on /sub1/foo and on /sub2/bar")

verify_error("""
/dts-v1/;

/ {
	sub {
		label: foo {
		};
	};
};
/ {
	sub {
		label: bar {
		};
	};
};
""",
"Label 'label' appears on /sub/bar and on /sub/foo")

verify_error("""
/dts-v1/;

/ {
	foo: a = < 0 >;
	foo: node {
	};
};
""",
"Label 'foo' appears on /node and on property 'a' of node /")

verify_error("""
/dts-v1/;

/ {
	foo: a = < 0 >;
	node {
		foo: b = < 0 >;
	};
};
""",
"Label 'foo' appears on property 'a' of node / and on property 'b' of node /node")

verify_error("""
/dts-v1/;

/ {
	foo: a = foo: < 0 >;
};
""",
"Label 'foo' appears in the value of property 'a' of node / and on property 'a' of node /")

# Giving the same label twice for the same node is fine
verify_parse("""
/dts-v1/;

/ {
	sub {
		label: foo {
		};
	};
};
/ {

	sub {
		label: foo {
		};
	};
};
""",
"""
/dts-v1/;

/ {
	sub {
		label: foo {
		};
	};
};
""")

# Duplicate labels are fine if one of the nodes is deleted
verify_parse("""
/dts-v1/;

/ {
	label: foo {
	};
	label: bar {
	};
};

/delete-node/ &{/bar};
""",
"""
/dts-v1/;

/ {
	label: foo {
	};
};
""")

#
# Test overriding/deleting a property with references
#

verify_parse("""
/dts-v1/;

/ {
	x = &foo, < &foo >;
	y = &foo, < &foo >;
	foo: foo {
	};
};

/ {
	x = < 1 >;
	/delete-property/ y;
};
""",
"""
/dts-v1/;

/ {
	x = [ 00 00 00 01 ];
	foo: foo {
	};
};
""")

#
# Test self-referential node
#

verify_parse("""
/dts-v1/;

/ {
	label: foo {
		x = &{/foo}, &label, < &label >;
	};
};
""",
"""
/dts-v1/;

/ {
	label: foo {
		x = [ 2F 66 6F 6F 00 2F 66 6F 6F 00 00 00 00 01 ];
		phandle = [ 00 00 00 01 ];
	};
};
""")

#
# Test /memreserve/
#

dt = verify_parse("""
/dts-v1/;

l1: l2: /memreserve/ (1 + 1) (2 * 2);
/memreserve/ 0x100 0x200;

/ {
};
""",
"""
/dts-v1/;

l1: l2: /memreserve/ 0x0000000000000002 0x0000000000000004;
/memreserve/ 0x0000000000000100 0x0000000000000200;

/ {
};
""")

expected = [({"l1", "l2"}, 2, 4), (set(), 0x100, 0x200)]
if dt.memreserves != expected:
    fail("expected {} for dt.memreserve, got {}"
         .format(expected, dt.memreserves))

#
# Test __repr__() functions
#


def verify_repr(obj, expected):
    if repr(obj) != expected:
        fail("expected repr() to be '{}', was '{}'"
             .format(expected, repr(obj)))


dt = parse("""
/dts-v1/;

/ {
	x = < 0 >;
	sub {
		y = < 1 >;
	};
};
""",
include_path=("foo", "bar"))

verify_repr(dt, "DT(filename='.tmp.dts', include_path=('foo', 'bar'))")
verify_repr(dt.root.props["x"], "<Property object for 'x' at '/' in '.tmp.dts'>")
verify_repr(dt.root.nodes["sub"], "<Node object for /sub in '.tmp.dts'>")

#
# Test names
#

# The C tools disallow '@' in property names, but otherwise accept the same
# characters in node and property names. Emulate that instead of the DT spec
# (v0.2), which gives different characters for nodes and properties.
verify_parse(r"""
/dts-v1/;

/ {
	// A leading \ is accepted but ignored in node/propert names
	\aA0,._+*#?- = &_, &{/aA0,._+*#?@-};

	// Names that overlap with operators
	+ = [ 00 ];
	* = [ 02 ];
	- = [ 01 ];
	? = [ 03 ];

	_: \aA0,._+*#?@- {
	};
};
""",
"""
/dts-v1/;

/ {
	aA0,._+*#?- = [ 2F 61 41 30 2C 2E 5F 2B 2A 23 3F 40 2D 00 2F 61 41 30 2C 2E 5F 2B 2A 23 3F 40 2D 00 ];
	+ = [ 00 ];
	* = [ 02 ];
	- = [ 01 ];
	? = [ 03 ];
	_: aA0,._+*#?@- {
	};
};
""")

verify_error(r"""
/dts-v1/;

/ {
	foo@3;
};
""",
".tmp.dts:4 (column 7): parse error: '@' is only allowed in node names")

verify_error(r"""
/dts-v1/;

/ {
	foo@3 = < 0 >;
};
""",
".tmp.dts:4 (column 8): parse error: '@' is only allowed in node names")

verify_error(r"""
/dts-v1/;

/ {
	foo@2@3 {
	};
};
""",
".tmp.dts:4 (column 10): parse error: multiple '@' in node name")

#
# Test bad formatting
#

verify_parse("""
/dts-v1/;/{l1:l2:foo{l3:l4:bar{l5:x=l6:/bits/8<l7:1 l8:2>l9:,[03],"a";};};};
""",
"""
/dts-v1/;

/ {
	l1: l2: foo {
		l3: l4: bar {
			l5: x = [ l6: l7: 01 l8: 02 l9: 03 61 00 ];
		};
	};
};
""")

#
# Test misc. errors
#

verify_error("",
             ".tmp.dts:1 (column 1): parse error: expected /dts-v1/ -- other versions are not supported")

verify_error("""
/dts-v1/;
""",
".tmp.dts:2 (column 1): parse error: no root node defined")

verify_error("""
/dts-v1/; /plugin/;
""",
".tmp.dts:1 (column 11): parse error: /plugin/ is not supported")


print("all tests passed")

# Only remove these if tests succeed. They're handy to have around otherwise.
shutil.rmtree(".tmp")
shutil.rmtree(".tmp2")
os.remove(".tmp.dts")
os.remove(".tmp2.dts")
os.remove(".tmp3.dts")
os.remove(".tmp.bin")
