#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

from multiconfig import multiconfig as mc

import argparse
import io
import itertools
import json
import pathlib
import pytest
import sys
import tempfile
import unittest.mock as utm

VALID_CONFIG_NAMES = ("c1", "c_", "C", "_c")
INVALID_CONFIG_NAMES = ("-c", "c-", "1c")
NUMBERS = {
    "normal": tuple(range(1, 11)),
    "invalid": tuple(range(11, 21)),
    "default": tuple(range(21, 31)),
    "global_default": tuple(range(31, 41)),
    "const": tuple(range(41, 51)),
}
ACTIONS = (
    "store",
    "append",
    "store_const",
    "store_true",
    "store_false",
    "count",
    "extend",
)
TYPES = (int, str, pathlib.Path)
TEST_FILE_PATH = pathlib.Path(__file__).resolve().parent / "testfile.txt"
with TEST_FILE_PATH.open() as f:
    TEST_FILE_CONTENT = f.read()

# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------


def get_scalar_values(category, type):
    if type is int:
        return NUMBERS[category]
    if type is str:
        return [f"str{i}" for i in NUMBERS[category]]
    if type is pathlib.Path:
        return [pathlib.Path(f"/path/{i}") for i in NUMBERS[category]]


def get_value(category, type, nargs, index=0):
    assert index < 5
    if nargs in ("*", "+"):
        return get_value(category, type, 2, index)
    if nargs == 0:
        return None
    svals = get_scalar_values(category, type)
    if nargs is None or nargs in ("?", 1):
        return svals[0 + index]
    assert isinstance(nargs, int)
    return list(svals[index * nargs : (index + 1) * nargs])


def get_dict_value(category, type, nargs, index=0):
    return get_value(category, type, nargs, index)


def get_argparse_value(category, type, nargs, index=0):
    if nargs in ("*", "+"):
        return get_argparse_value(category, type, 2, index)
    if nargs == 0:
        return "--c"
    value = get_value(category, type, nargs, index)
    if nargs is None or nargs in ("?", 1):
        return f"--c {str(value)}"
    assert isinstance(nargs, int)
    return f"--c {' '.join([str(v) for v in value[:nargs]])}"


def get_default_value(action, category, type, nargs, index=0):
    return get_parse_return_value(action, category, type, nargs, nargs, index)


def get_const_value(category, type, index=0):
    return get_value(category, type, None, index)


def get_parse_return_value(
    action, category, type, nargs, input_nargs=mc.NONE, index=0
):
    if input_nargs is mc.NONE:
        input_nargs = nargs
    value = get_value(category, type, input_nargs, index)

    if input_nargs in (None, 1, "?") and nargs in (1, 2, "+", "*"):
        value = [value]
    elif input_nargs == 0 and nargs == "*":
        value = []

    if action == "append":
        return [value]
    if action == "extend":
        if isinstance(value, list):
            return value
        return [value]
    return value


def get_choices(type):
    return get_scalar_values("normal", type)


def get_default_nargs_for_action(action):
    if action in ("store", "append"):
        return None
    if action in ("store_const", "store_true", "store_false", "count"):
        return 0
    assert action == "extend"
    return "*"


def split_str(s):
    return s.split()


# ------------------------------------------------------------------------------
# Helper classes
# ------------------------------------------------------------------------------


class ArgparseError(RuntimeError):
    pass


class RaisingArgumentParser(argparse.ArgumentParser):
    def exit(self, status=0, message=None):
        if status:
            raise ArgparseError(message)


class JsonEncoderWithPath(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, pathlib.Path):
            return str(o)
        return super().default(o)


class _OmitTestForSource:
    def __str__(self):
        return "OMIT_TEST_FOR_SOURCE"

    __repr__ = __str__


OMIT_TEST_FOR_SOURCE = _OmitTestForSource()


class Spec:
    def __init__(
        self,
        id,
        config_args,
        expected,
        dict_source=OMIT_TEST_FOR_SOURCE,
        argparse_source=OMIT_TEST_FOR_SOURCE,
        test_without_source=False,
        config_parser_args=None,
        test_against_argparse_xfail=None,
    ):
        self.id = id
        self.config_args = config_args
        self.expected = expected
        self.dict_source = dict_source
        self.argparse_source = argparse_source
        self.test_without_source = test_without_source
        self.config_parser_args = config_parser_args or {}
        self.test_against_argparse_xfail = test_against_argparse_xfail

    def __str__(self):
        return str(vars(self))

    __repr__ = __str__


# ------------------------------------------------------------------------------
# ConfigParser tests
# ------------------------------------------------------------------------------


def test_partially_parse_config():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2")
    mc_parser.add_source(mc.DictSource, {"c2": "v2"})
    values = mc_parser.partially_parse_config()
    expected_values = mc._namespace_from_dict({"c1": None, "c2": "v2"})
    assert values == expected_values


def test_types():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", type=str)
    mc_parser.add_config("c2", type=int)
    mc_parser.add_config("c3", type=pathlib.Path)
    mc_parser.add_config("c4", type=json.loads)
    mc_parser.add_config("c5", type=split_str)
    mc_parser.add_source(
        mc.DictSource,
        {
            "c1": "v1",
            "c2": "10",
            "c3": "/some/path",
            "c4": '{ "a": "va", "b": 10, "c": [ 1, 2, 3 ] }',
            "c5": "word1 word2 word3",
        },
    )
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict(
        {
            "c1": "v1",
            "c2": 10,
            "c3": pathlib.Path("/some/path"),
            "c4": {"a": "va", "b": 10, "c": [1, 2, 3]},
            "c5": ["word1", "word2", "word3"],
        }
    )
    assert values == expected_values
    assert isinstance(values.c1, str)
    assert isinstance(values.c2, int)
    assert isinstance(values.c3, pathlib.Path)
    assert isinstance(values.c4, dict)
    assert isinstance(values.c5, list)


def test_file_opening():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", type=mc.FileType("r"))
    mc_parser.add_config("c2", type=mc.FileType("w"))
    with tempfile.TemporaryDirectory() as tmpdir:
        mc_parser.add_source(
            mc.DictSource,
            {"c1": str(TEST_FILE_PATH), "c2": f"{tmpdir}/testfile2.txt"},
        )
        values = mc_parser.parse_config()
        assert values.c1.read() == TEST_FILE_CONTENT
        values.c2.write(TEST_FILE_CONTENT)


@pytest.mark.parametrize("name", VALID_CONFIG_NAMES)
def test_valid_name(name):
    mc_parser = mc.ConfigParser()
    mc_parser.add_config(name)


@pytest.mark.parametrize("name", INVALID_CONFIG_NAMES)
def test_invalid_name(name):
    mc_parser = mc.ConfigParser()
    with pytest.raises(ValueError):
        mc_parser.add_config(name)


def test_validate_type():
    mc_parser = mc.ConfigParser()
    with pytest.raises(TypeError):
        mc_parser.add_config("c1", type=1)


def test_count():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count")
    mc_parser.add_source(mc.DictSource, {"c1": None})
    mc_parser.add_source(mc.DictSource, {"c1": None})
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 2})
    assert values == expected_values


def test_count_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", default=10)
    mc_parser.add_source(mc.DictSource, {"c1": None})
    mc_parser.add_source(mc.DictSource, {"c1": None})
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 12})
    assert values == expected_values


def test_count_missing():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count")
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": None})
    assert values == expected_values


def test_count_required_missing():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", required=True)
    with pytest.raises(mc.RequiredConfigNotFoundError):
        mc_parser.parse_config()


def test_count_required_missing_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", required=True, default=10)
    with pytest.raises(mc.RequiredConfigNotFoundError):
        mc_parser.parse_config()


def test_count_missing_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", default=10)
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 10})
    assert values == expected_values


test_specs = []

nargs_test_specs = []

good_input_nargs = {
    None: (None,),
    1: (1,),
    2: (2,),
    "?": (None, 0),
    "*": (None, 0, 1, 2),
    "+": (None, 1, 2),
}
bad_input_nargs = {
    None: (0, 2),
    1: (0, 2),
    2: (None, 0, 1),
    "?": (1, 2),
    "*": (),
    "+": (0,),
}

for action, nargs, input_nargs, type in (
    (a, na, gna, t)
    for a in ("store", "append", "extend")
    for na in (None, 1, 2, "?", "*", "+")
    for gna in good_input_nargs[na]
    for t in TYPES
):

    dict_value = get_dict_value("normal", type, input_nargs)
    argparse_value = get_argparse_value("normal", type, input_nargs)
    expected = get_parse_return_value(
        action, "normal", type, nargs, input_nargs=input_nargs
    )
    test_against_argparse_xfail = None
    if action == "extend" and nargs in (None, "?"):
        test_against_argparse_xfail = (
            "argparse bug: https://bugs.python.org/issue40365"
        )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_valid_input; action={action}; nargs={nargs}; "
                f"args=yes_{input_nargs}; type={type.__name__}"
            ),
            config_args={"action": action, "nargs": nargs, "type": type},
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=expected,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )

for action, nargs, input_nargs, type in (
    (a, na, bna, t)
    for a in ("store", "append", "extend")
    for na in (None, 1, 2, "?", "*", "+")
    for bna in bad_input_nargs[na]
    for t in TYPES
):

    dict_value = get_dict_value("normal", type, input_nargs)
    argparse_value = get_argparse_value("normal", type, input_nargs)
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_invalid_input; action={action}; nargs={nargs}; "
                f"args=yes_{input_nargs}; type={type.__name__}"
            ),
            config_args={"action": action, "nargs": nargs, "type": type},
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=Exception,
        )
    )

for action, nargs in itertools.product(
    ("store", "append", "extend"), (None, 1, 2, "?", "*", "+"),
):
    type = str
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_invalid_const; action={action}; nargs={nargs}; "
                f"args=no; type={type.__name__}; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "const": "a",
            },
            dict_source=None,
            argparse_source="",
            expected=Exception,
        )
    )

    dict_value = get_dict_value("normal", type, nargs)
    argparse_value = get_argparse_value("normal", type, nargs)
    default_value = get_default_value(action, "default", type, nargs)
    global_default_value = get_default_value(
        action, "global_default", type, nargs
    )
    normal_expected = get_parse_return_value(action, "normal", type, nargs)
    normal_expected_with_default = normal_expected
    if action in ("append", "extend"):
        normal_expected_with_default = default_value + normal_expected
    normal_expected_with_global_default = normal_expected
    if action in ("append", "extend"):
        normal_expected_with_global_default = (
            global_default_value + normal_expected
        )
    default_expected = get_parse_return_value(action, "default", type, nargs)
    global_default_expected = get_parse_return_value(
        action, "global_default", type, nargs
    )
    test_against_argparse_xfail = None
    if action == "extend" and nargs in (None, "?"):
        test_against_argparse_xfail = (
            "argparse bug: https://bugs.python.org/issue40365"
        )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_default; action={action}; nargs={nargs}; "
                f"args=yes_{nargs}; type={type.__name__}; default=yes"
            ),
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=normal_expected_with_default,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_default; action={action}; nargs={nargs}; "
                f"args=no; type={type.__name__}; default=yes"
            ),
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_global_default; action={action}; nargs={nargs}; "
                f"args=yes_{nargs}; type={type.__name__}; global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={"action": action, "nargs": nargs, "type": type},
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=normal_expected_with_global_default,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_global_default; action={action}; nargs={nargs}; "
                f"args=no; type={type.__name__}; global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={"action": action, "nargs": nargs, "type": type},
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=global_default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_default_and_global_default; action={action}; "
                f"nargs={nargs}; args=yes_{nargs}; type={type.__name__};"
                f"default=yes; global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=normal_expected_with_default,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_default_and_global_default; action={action}; "
                f"nargs={nargs}; args=no; type={type.__name__}; "
                f"default=yes; global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=default_expected,
        )
    )
for action in ("store", "append", "extend"):
    type = str
    nargs = "?"
    dict_value = get_dict_value("normal", type, nargs)
    argparse_value = get_argparse_value("normal", type, nargs)
    default_value = get_default_value(action, "default", type, nargs)
    const_value = get_const_value("const", type)
    normal_expected = get_parse_return_value(action, "normal", type, nargs)
    normal_expected_with_default = normal_expected
    if action in ("append", "extend"):
        normal_expected_with_default = default_value + normal_expected
    default_expected = get_parse_return_value(action, "default", type, nargs)
    const_expected = get_parse_return_value(action, "const", type, nargs)
    const_expected_with_default = const_expected
    if action in ("append", "extend"):
        const_expected_with_default = default_value + const_expected
    test_against_argparse_xfail = None
    if action == "extend":
        test_against_argparse_xfail = (
            "argparse bug: https://bugs.python.org/issue40365"
        )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; args=no; "
                f"type={type.__name__}; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=None,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; "
                f"args=yes1; type={type.__name__}; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
            },
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=normal_expected,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; "
                f"args=yes0; type={type.__name__}; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
            },
            dict_source=None,
            argparse_source="--c",
            expected=const_expected,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; args=no; "
                f"type={type.__name__}; default=yes; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
                "default": default_value,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; "
                f"args=yes1; type={type.__name__}; default=yes; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
                "default": default_value,
            },
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=normal_expected_with_default,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const; action={action}; nargs=?; "
                f"args=yes0; type={type.__name__}; default=yes; const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
                "default": default_value,
            },
            dict_source=None,
            argparse_source="--c",
            expected=const_expected_with_default,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
test_specs.extend(nargs_test_specs)

store_const_test_specs = [
    Spec(
        id="store_const; args=yes; const=no (missing const)",
        config_args={"action": "store_const"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_const; args=yes",
        config_args={"action": "store_const", "const": "c"},
        dict_source=None,
        argparse_source="--c",
        expected="c",
    ),
    Spec(
        id="store_const; args=invalid1",
        config_args={"action": "store_const", "const": "c"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_const; args=no; default=yes",
        config_args={"action": "store_const", "const": "c", "default": "d"},
        dict_source=mc.NONE,
        argparse_source="",
        expected="d",
    ),
]
test_specs.extend(store_const_test_specs)

store_true_test_specs = [
    Spec(
        id="store_true; args=yes; const=yes (invalid const)",
        config_args={"action": "store_true", "const": "v"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_true; args=no",
        config_args={"action": "store_true"},
        dict_source=mc.NONE,
        argparse_source="",
        expected=False,
    ),
    Spec(
        id="store_true; args=yes",
        config_args={"action": "store_true"},
        dict_source=None,
        argparse_source="--c",
        expected=True,
    ),
    Spec(
        id="store_true; args=invalid1",
        config_args={"action": "store_true"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_true; args=no; default=yes",
        config_args={"action": "store_true", "default": "d"},
        dict_source=mc.NONE,
        argparse_source="",
        expected="d",
    ),
]
test_specs.extend(store_true_test_specs)

store_false_test_specs = [
    Spec(
        id="store_false; args=yes; const=yes (invalid const)",
        config_args={"action": "store_false", "const": "v"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_false; args=no",
        config_args={"action": "store_false"},
        dict_source=mc.NONE,
        argparse_source="",
        expected=True,
    ),
    Spec(
        id="store_false; args=yes",
        config_args={"action": "store_false"},
        dict_source=None,
        argparse_source="--c",
        expected=False,
    ),
    Spec(
        id="store_false; args=invalid1",
        config_args={"action": "store_false"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_false; args=no; default=yes",
        config_args={"action": "store_false", "default": "d"},
        dict_source=mc.NONE,
        argparse_source="",
        expected="d",
    ),
]
test_specs.extend(store_false_test_specs)

suppress_test_specs = []
nargs_not0_actions = ("store", "append")
nargs_none_actions = ("extend", "store_true", "count")
nargs_none_with_const_actions = ("store_const",)
for action, nargs, const in itertools.chain(
    itertools.product(
        nargs_not0_actions, (mc.NONE, 1, 2, "?", "*", "+"), (mc.NONE,),
    ),
    ((a, mc.NONE, mc.NONE) for a in nargs_none_actions),
    ((a, mc.NONE, "c") for a in nargs_none_with_const_actions),
):
    extra_config_args = {}
    if nargs is not mc.NONE:
        extra_config_args["nargs"] = nargs
    if const is not mc.NONE:
        extra_config_args["const"] = const

    suppress_test_specs.append(
        Spec(
            id=(
                f"suppress; action={action}; nargs={nargs}; args=no; "
                f"default=suppress"
            ),
            config_args={
                "action": action,
                "default": mc.SUPPRESS,
                **extra_config_args,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=mc.NONE,
        )
    )
    suppress_test_specs.append(
        Spec(
            id=(
                f"suppress; action={action}; nargs={nargs}; args=no; "
                f"default=none; config_default=suppress"
            ),
            config_args={"action": action, **extra_config_args},
            config_parser_args={"config_default": mc.SUPPRESS},
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=mc.NONE,
        )
    )
test_specs.extend(suppress_test_specs)

required_test_specs = []
for action, type in itertools.product(("store", "append", "extend"), TYPES):
    nargs = get_default_nargs_for_action(action)
    dict_value = get_dict_value("normal", type, nargs)
    argparse_value = get_argparse_value("normal", type, nargs)
    expected = get_parse_return_value(action, "normal", type, nargs)
    test_against_argparse_xfail = None
    if action == "extend":
        test_against_argparse_xfail = (
            "argparse bug: https://bugs.python.org/issue40365"
        )
    required_test_specs.append(
        Spec(
            id=(
                f"required; action={action}; nargs=no; args=yes; "
                f"type={type.__name__}; required=yes, default=no"
            ),
            config_args={"action": action, "required": True, "type": type},
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=expected,
            test_against_argparse_xfail=test_against_argparse_xfail,
        ),
    )
    required_test_specs.append(
        Spec(
            id=(
                f"required; action={action}; nargs=no; args=no; "
                f"type={type.__name__}; required=yes, default=yes"
            ),
            config_args={
                "action": action,
                "required": True,
                "default": expected,
                "type": type,
            },
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=Exception,
        ),
    )
    required_test_specs.append(
        Spec(
            id=f"required; action={action}; nargs=no; args=no; required=yes",
            config_args={"action": action, "required": True, "type": type},
            dict_source=mc.NONE,
            argparse_source="",
            test_without_source=True,
            expected=Exception,
        ),
    )
test_specs.extend(required_test_specs)


choices_test_specs = []
for action, type, index, category in itertools.product(
    (a for a in ACTIONS if get_default_nargs_for_action(a) != 0),
    TYPES,
    (0, 1),
    ("normal", "invalid"),
):
    nargs = get_default_nargs_for_action(action)
    choices = get_choices(type)
    dict_value = get_dict_value(category, type, nargs, index)
    argparse_value = get_argparse_value(category, type, nargs, index)
    if category == "invalid":
        expected = Exception
    else:
        expected = get_parse_return_value(
            action, category, type, nargs, index=index
        )
    test_against_argparse_xfail = None
    if action == "extend":
        test_against_argparse_xfail = (
            "argparse bug: https://bugs.python.org/issue40365"
        )
    choices_test_specs.append(
        Spec(
            id=(
                f"choices; action={action}; nargs=no; args=yes; "
                f"type={type.__name__}; choices={choices}; "
                f"choice={category}{index}"
            ),
            config_args={"action": action, "choices": choices, "type": type},
            dict_source=dict_value,
            argparse_source=argparse_value,
            expected=expected,
            test_against_argparse_xfail=test_against_argparse_xfail,
        )
    )
test_specs.extend(choices_test_specs)


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_dict(spec):
    if spec.dict_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip("DictSource does not support this test")
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_dict(spec)
    else:
        _test_spec_with_dict(spec)


def _test_spec_with_dict(spec):
    mc_parser = mc.ConfigParser(**spec.config_parser_args)
    mc_parser.add_config("c", **spec.config_args)
    dict_source = {}
    if spec.dict_source is not mc.NONE:
        dict_source["c"] = spec.dict_source
    mc_parser.add_source(mc.DictSource, dict_source)
    values = mc_parser.parse_config()
    if spec.expected is mc.NONE:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_json(spec):
    if spec.dict_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip("JsonSource does not support this test")
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_json(spec)
    else:
        _test_spec_with_json(spec)


def _test_spec_with_json(spec):
    mc_parser = mc.ConfigParser(**spec.config_parser_args)
    mc_parser.add_config("c", **spec.config_args)
    dict_source = {}
    if spec.dict_source is not mc.NONE:
        dict_source["c"] = spec.dict_source
    fileobj = io.StringIO(json.dumps(dict_source, cls=JsonEncoderWithPath))
    mc_parser.add_source(mc.JsonSource, fileobj=fileobj)
    values = mc_parser.parse_config()
    if spec.expected is mc.NONE:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_argparse(spec):
    if spec.argparse_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip("SimpleArgparseSource does not support this test")
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_argparse(spec)
    else:
        _test_spec_with_argparse(spec)


def _test_spec_with_argparse(spec):
    mc_parser = mc.ConfigParser(**spec.config_parser_args)
    mc_parser.add_config("c", **spec.config_args)
    mc_parser.add_source(
        mc.SimpleArgparseSource, argument_parser_class=RaisingArgumentParser
    )
    argv = ["prog", *spec.argparse_source.split()]
    with utm.patch.object(sys, "argv", argv):
        values = mc_parser.parse_config()
    if spec.expected is mc.NONE:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_against_argparse(spec):
    if spec.test_against_argparse_xfail is not None:
        pytest.xfail(spec.test_against_argparse_xfail)
    if spec.argparse_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip("Argparse does not support this test")
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_against_argparse(spec)
    else:
        _test_spec_against_argparse(spec)


def _test_spec_against_argparse(spec):
    # Check that we get the expected results by using argparse directly
    if (
        "action" in spec.config_args
        and spec.config_args["action"] == "extend"
        and sys.version_info < (3, 8)
    ):
        pytest.skip(
            'Argparse\'s "extend" action is not supported before Python 3.8'
        )
    rap_args = {}
    if "config_default" in spec.config_parser_args:
        config_default = spec.config_parser_args["config_default"]
        if config_default is mc.SUPPRESS:
            rap_args["argument_default"] = argparse.SUPPRESS
        elif config_default is not mc.NONE:
            rap_args["argument_default"] = config_default

    if (
        "default" in spec.config_args
        and spec.config_args["default"] == mc.SUPPRESS
    ):
        spec.config_args["default"] = argparse.SUPPRESS

    ap_parser = RaisingArgumentParser(**rap_args)
    ap_parser.add_argument("--c", **spec.config_args)
    ap_values = ap_parser.parse_args(spec.argparse_source.split())
    if spec.expected is mc.NONE:
        assert not hasattr(ap_values, "c")
    else:
        assert getattr(ap_values, "c") == spec.expected


# ------------------------------------------------------------------------------
# SimpleArgparseSource tests
# ------------------------------------------------------------------------------


def test_simple_argparse_source_with_config_added_after_source():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_source(mc.SimpleArgparseSource)
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    with utm.patch.object(sys, "argv", "prog --c1 v1 --c3 v3".split()):
        with pytest.raises(SystemExit):
            values = mc_parser.parse_config()
    with utm.patch.object(sys, "argv", "prog --c1 v1".split()):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict(
        {"c1": "v1", "c2": "v2", "c3": None, "c4": "v4"}
    )
    assert values == expected_values


def test_simple_argparse_source_with_prog():
    mc_parser = mc.ConfigParser()
    mc_parser.add_source(
        mc.SimpleArgparseSource,
        argument_parser_class=RaisingArgumentParser,
        prog="PROG_TEST",
    )
    with utm.patch.object(sys, "argv", "prog --c1 v1".split()):
        with pytest.raises(ArgparseError, match="PROG_TEST"):
            mc_parser.parse_config()


def test_simple_argparse_source_with_count():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count")
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog --c1 --c1".split()):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 2})
    assert values == expected_values


def test_simple_argparse_source_with_count_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", default=10)
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog --c1 --c1".split()):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 12})
    assert values == expected_values


def test_simple_argparse_source_with_count_missing():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count")
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog".split()):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": None})
    assert values == expected_values


def test_simple_argparse_source_with_count_required_missing():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", required=True)
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog".split()):
        with pytest.raises(mc.RequiredConfigNotFoundError):
            mc_parser.parse_config()


def test_simple_argparse_source_with_count_required_missing_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", required=True, default=10)
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog".split()):
        with pytest.raises(mc.RequiredConfigNotFoundError):
            mc_parser.parse_config()


def test_simple_argparse_source_with_count_missing_with_default():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", action="count", default=10)
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(sys, "argv", "prog".split()):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict({"c1": 10})
    assert values == expected_values


# ------------------------------------------------------------------------------
# JsonSource tests
# ------------------------------------------------------------------------------


def test_json_source_with_config_added_after_source():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    fileobj = io.StringIO(
        """{
        "c1": "v1",
        "c3": "v3"
    }"""
    )
    mc_parser.add_source(mc.JsonSource, fileobj=fileobj)
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict(
        {"c1": "v1", "c2": "v2", "c3": None, "c4": "v4"}
    )
    assert values == expected_values


# ------------------------------------------------------------------------------
# Multiple source tests
# ------------------------------------------------------------------------------


def test_multiple_sources():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", type=int, choices=[1, 2], required=True)
    mc_parser.add_config("c2", type=str, default="v2")
    mc_parser.add_config("c3", type=pathlib.Path)
    mc_parser.add_config("c4", type=split_str, default="word1 word2".split())
    mc_parser.add_config("c5", choices=["v5", "v5a"], required=True)
    mc_parser.add_config("c6", default="v6")
    mc_parser.add_config("c7", type=json.loads)
    mc_parser.add_config("c8", default="v8")
    mc_parser.add_config("c9", action="append", type=int, default=[0, 1])
    mc_parser.add_config("c10", action="count", default=10)
    fileobj = io.StringIO(
        """{
        "c1": 1,
        "c2": "v2a",
        "c9": 2,
        "c10": null
    }"""
    )
    mc_parser.add_source(mc.JsonSource, fileobj=fileobj)
    mc_parser.add_source(mc.DictSource, {"c9": 3, "c10": None})
    mc_parser.add_source(mc.SimpleArgparseSource)
    with utm.patch.object(
        sys, "argv", "prog --c5 v5 --c7 [1,2] --c9 4 --c10 --c10".split()
    ):
        values = mc_parser.parse_config()
    expected_values = mc._namespace_from_dict(
        {
            "c1": 1,
            "c2": "v2a",
            "c3": None,
            "c4": "word1 word2".split(),
            "c5": "v5",
            "c6": "v6",
            "c7": [1, 2],
            "c8": "v8",
            "c9": [0, 1, 2, 3, 4],
            "c10": 14,
        }
    )
    assert values == expected_values


# ------------------------------------------------------------------------------
# Free function tests
# ------------------------------------------------------------------------------


def test_getattr_or_none():
    obj = mc.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mc.NONE)
    assert mc._getattr_or_none(obj, "c1") == "v1"
    assert mc._getattr_or_none(obj, "c2") is None
    assert mc._getattr_or_none(obj, "c3") is mc.NONE
    assert mc._getattr_or_none(obj, "c4") is mc.NONE


def test_has_nonnone_attr():
    obj = mc.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mc.NONE)
    assert mc._has_nonnone_attr(obj, "c1")
    assert mc._has_nonnone_attr(obj, "c2")
    assert not mc._has_nonnone_attr(obj, "c3")
    assert not mc._has_nonnone_attr(obj, "c4")
