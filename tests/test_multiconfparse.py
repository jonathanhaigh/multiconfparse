#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import argparse
import io
import itertools
import json
import os
import pathlib
import pytest
import shlex
import sys
import tempfile
import unittest.mock as utm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import multiconfparse as mcp  # noqa: E402


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
    action, category, type, nargs, input_nargs=mcp.NOT_GIVEN, index=0
):
    if input_nargs is mcp.NOT_GIVEN:
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


def shlex_join(words):
    if sys.version_info < (3, 8):
        # shlex.join() was new in 3.8.
        return " ".join((shlex.quote(w) for w in words))
    return shlex.join(words)


# ------------------------------------------------------------------------------
# Helper classes
# ------------------------------------------------------------------------------


class ArgparseError(RuntimeError):
    pass


class UcStoreAction(mcp.Action):
    name = "uc_store"

    def __init__(self, const=None, **kwargs):
        super().__init__(type=str, **kwargs)

        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action} action"
            )

        if const is not None and self.nargs != "?":
            raise ValueError(
                f"const cannot be supplied to the {self.action} action "
                'unless nargs is "?"'
            )
        self.const = const

    def __call__(self, namespace, args):
        if self.nargs == "?" and not args:
            setattr(namespace, self.name, self.const)
        elif self.nargs is None or self.nargs == "?":
            assert len(args) == 1
            setattr(namespace, self.name, args[0].upper())
        else:
            setattr(namespace, self.name, [arg.upper() for arg in args])


class RaisingArgumentParser(argparse.ArgumentParser):
    def exit(self, status=0, message=None):
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
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", required=True)
    mcp_parser.add_config("c2")
    mcp_parser.add_source("dict", {"c2": "v2"})
    values = mcp_parser.partially_parse_config()
    expected_values = mcp._namespace_from_dict({"c1": None, "c2": "v2"})
    assert values == expected_values


def test_types():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", type=str)
    mcp_parser.add_config("c2", type=int)
    mcp_parser.add_config("c3", type=pathlib.Path)
    mcp_parser.add_config("c4", type=json.loads)
    mcp_parser.add_config("c5", type=split_str)
    mcp_parser.add_source(
        "dict",
        {
            "c1": "v1",
            "c2": "10",
            "c3": "/some/path",
            "c4": '{ "a": "va", "b": 10, "c": [ 1, 2, 3 ] }',
            "c5": "word1 word2 word3",
        },
    )
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
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
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", type=mcp.FileType("r"))
    mcp_parser.add_config("c2", type=mcp.FileType("w"))
    with tempfile.TemporaryDirectory() as tmpdir:
        mcp_parser.add_source(
            "dict",
            {"c1": str(TEST_FILE_PATH), "c2": f"{tmpdir}/testfile2.txt"},
        )
        values = mcp_parser.parse_config()
        assert values.c1.read() == TEST_FILE_CONTENT
        values.c2.write(TEST_FILE_CONTENT)


@pytest.mark.parametrize("name", VALID_CONFIG_NAMES)
def test_valid_name(name):
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config(name)


@pytest.mark.parametrize("name", INVALID_CONFIG_NAMES)
def test_invalid_name(name):
    mcp_parser = mcp.ConfigParser()
    with pytest.raises(ValueError):
        mcp_parser.add_config(name)


def test_validate_type():
    mcp_parser = mcp.ConfigParser()
    with pytest.raises(TypeError):
        mcp_parser.add_config("c1", type=1)


def test_custom_action():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action=UcStoreAction)
    mcp_parser.add_config("c2", action=UcStoreAction, nargs=2)
    mcp_parser.add_source("dict", {"c1": "abc", "c2": ["def", "ghi"]})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
        {"c1": "ABC", "c2": ["DEF", "GHI"]}
    )
    assert values == expected_values

    with pytest.raises(Exception):
        mcp_parser = mcp.ConfigParser()
        mcp_parser.add_config("c1", action=UcStoreAction, type=int)


def test_source_as_class():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1")
    mcp_parser.add_source(mcp.DictSource, {"c1": "v1"})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": "v1"})
    assert values == expected_values

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", exclude_sources=["dict"])
    mcp_parser.add_source(mcp.DictSource, {"c1": "v1"})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": None})
    assert values == expected_values

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", include_sources=["dict"])
    mcp_parser.add_source(mcp.DictSource, {"c1": "v1"})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": "v1"})
    assert values == expected_values

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", include_sources=["json"])
    mcp_parser.add_source(mcp.DictSource, {"c1": "v1"})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": None})
    assert values == expected_values


def test_count():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count")
    mcp_parser.add_source("dict", {"c1": None})
    mcp_parser.add_source("dict", {"c1": None})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 2})
    assert values == expected_values


def test_count_with_default():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count", default=10)
    mcp_parser.add_source("dict", {"c1": None})
    mcp_parser.add_source("dict", {"c1": None})
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 12})
    assert values == expected_values


def test_count_missing():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count")
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": None})
    assert values == expected_values


def test_count_with_required():
    mcp_parser = mcp.ConfigParser()
    with pytest.raises(Exception):
        mcp_parser.add_config("c1", action="count", required=True)


def test_count_missing_with_default():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count", default=10)
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 10})
    assert values == expected_values


def test_priorities():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=1)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=3)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": "v3"})

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": "v1"})

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="append")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=1)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=3)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": ["v1", "v2", "v3"]})

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="append")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": ["v3", "v2", "v1"]})


def test_priorities_with_default():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="append", default=["d"])
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": ["d", "v3", "v2", "v1"]})

    mcp_parser = mcp.ConfigParser(config_default=["cd"])
    mcp_parser.add_config("c", action="append")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": ["cd", "v3", "v2", "v1"]})

    mcp_parser = mcp.ConfigParser(config_default=["cd"])
    mcp_parser.add_config("c", action="append", default=["d"])
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("dict", {"c": "v2"}, priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": ["d", "v3", "v2", "v1"]})


def test_priorities_with_multiple_values_from_source():
    # Multiple values from a single source should retain their ordering
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="extend")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=3)
    mcp_parser.add_source("simple_argparse", priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=1)
    with utm.patch.object(sys, "argv", "prog --c v2a v2b".split()):
        values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict(
        {"c": ["v3", "v2a", "v2b", "v1"]}
    )

    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="extend")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=1)
    mcp_parser.add_source("simple_argparse", priority=2)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=3)
    with utm.patch.object(sys, "argv", "prog --c v2a v2b".split()):
        values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict(
        {"c": ["v1", "v2a", "v2b", "v3"]}
    )


def test_priorities_stable_sort():
    # values from multiple sources with the same priority should be in the
    # order the sources were added
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="extend")
    mcp_parser.add_source("dict", {"c": "v1"}, priority=0)
    mcp_parser.add_source("simple_argparse", priority=0)
    mcp_parser.add_source("dict", {"c": "v3"}, priority=0)
    with utm.patch.object(sys, "argv", "prog --c v2a v2b".split()):
        values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict(
        {"c": ["v1", "v2a", "v2b", "v3"]}
    )


def test_dict_source_none_values():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="store", nargs="?", const="cv")
    mcp_parser.add_source(
        "dict", {"c": "none_value"}, none_values=["none_value"]
    )
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": "cv"})


def test_env_source_none_values():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="store", nargs="?", const="cv")
    mcp_parser.add_source(
        "environment", env_var_prefix="TEST_", none_values=["none_value"],
    )
    with utm.patch.object(os, "environ", {"TEST_C": "none_value"}):
        values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": "cv"})


def test_json_source_none_values():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c", action="store", nargs="?", const="cv")
    fileobj = io.StringIO('{"c": "none_value"}')
    mcp_parser.add_source(
        "json", fileobj=fileobj, none_values=["none_value"],
    )
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"c": "cv"})


def test_config_name_clash():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c")
    with pytest.raises(ValueError):
        mcp_parser.add_config("c")


def test_dest_with_store():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", dest="d")
    mcp_parser.add_config("c2", dest="d")
    mcp_parser.add_config("c3", dest="d")
    mcp_parser.add_source("dict", {"c1": "v1"})
    mcp_parser.add_source("dict", {"c2": "v2"})
    mcp_parser.add_source("dict", {"c3": "v3"})
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"d": "v3"})


def test_dest_with_store_const():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="store_const", const="v1", dest="d")
    mcp_parser.add_config("c2", action="store_const", const="v2", dest="d")
    mcp_parser.add_config("c3", action="store_const", const="v3", dest="d")
    mcp_parser.add_source("dict", {"c1": None})
    mcp_parser.add_source("dict", {"c2": None})
    mcp_parser.add_source("dict", {"c3": None})
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"d": "v3"})


def test_dest_with_count():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count", dest="d")
    mcp_parser.add_config("c2", action="count", dest="d")
    mcp_parser.add_config("c3", action="count", dest="d")
    mcp_parser.add_source("dict", {"c1": None})
    mcp_parser.add_source("dict", {"c2": None})
    mcp_parser.add_source("dict", {"c3": None})
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"d": 3})


def test_dest_with_append():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="append", dest="d")
    mcp_parser.add_config("c2", action="append", dest="d")
    mcp_parser.add_config("c3", action="append", dest="d")
    mcp_parser.add_source("dict", {"c1": "v1"})
    mcp_parser.add_source("dict", {"c2": "v2"})
    mcp_parser.add_source("dict", {"c3": "v3"})
    values = mcp_parser.parse_config()
    assert values == mcp._namespace_from_dict({"d": ["v1", "v2", "v3"]})


def test_dest_with_extend():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="extend", dest="d")
    mcp_parser.add_config("c2", action="extend", dest="d")
    mcp_parser.add_config("c3", action="extend", dest="d")
    mcp_parser.add_source("dict", {"c1": ["v1", "v2"]})
    mcp_parser.add_source("dict", {"c2": "v3"})
    mcp_parser.add_source("dict", {"c3": ["v4", "v5"]})
    values = mcp_parser.parse_config()
    expected = mcp._namespace_from_dict({"d": ["v1", "v2", "v3", "v4", "v5"]})
    assert values == expected


def test_dest_with_simple_argparse():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="extend", dest="d")
    mcp_parser.add_config("c2", action="extend", dest="d")
    mcp_parser.add_config("c3", action="extend", dest="d")
    mcp_parser.add_source("simple_argparse")
    argv = "prog --c1 v1 v2 --c2 v3 --c3 v4 v5".split()
    with utm.patch.object(sys, "argv", argv):
        values = mcp_parser.parse_config()
    expected = mcp._namespace_from_dict({"d": ["v1", "v2", "v3", "v4", "v5"]})
    assert values == expected


def test_suppress_help(capfd):
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("config_item1")
    mcp_parser.add_config("config_item2", help=mcp.SUPPRESS)
    mcp_parser.add_config("config_item3")
    mcp_parser.add_source(
        "simple_argparse", argument_parser_class=RaisingArgumentParser,
    )
    argv = "prog --help".split()
    with utm.patch.object(sys, "argv", argv):
        with pytest.raises(ArgparseError):
            mcp_parser.parse_config()
        out, err = capfd.readouterr()
        assert "--config-item1" in out
        assert "--config-item2" not in out
        assert "--config-item3" in out


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
                f"nargs_with_valid_input, action={action}, nargs={nargs}, "
                f"args=yes_{input_nargs}, type={type.__name__}"
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
                f"nargs_with_invalid_input, action={action}, nargs={nargs}, "
                f"args=yes_{input_nargs}, type={type.__name__}"
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
                f"nargs_with_invalid_const, action={action}, nargs={nargs}, "
                f"args=no, type={type.__name__}, const=yes"
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
                f"nargs_with_default, action={action}, nargs={nargs}, "
                f"args=yes_{nargs}, type={type.__name__}, default=yes"
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
                f"nargs_with_default, action={action}, nargs={nargs}, "
                f"args=no, type={type.__name__}, default=yes"
            ),
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_global_default, action={action}, nargs={nargs}, "
                f"args=yes_{nargs}, type={type.__name__}, global_default=yes"
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
                f"nargs_with_global_default, action={action}, nargs={nargs}, "
                f"args=no, type={type.__name__}, global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={"action": action, "nargs": nargs, "type": type},
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=global_default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_with_default_and_global_default, action={action}, "
                f"nargs={nargs}, args=yes_{nargs}, type={type.__name__},"
                "default=yes, global_default=yes"
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
                f"nargs_with_default_and_global_default, action={action}, "
                f"nargs={nargs}, args=no, type={type.__name__}, "
                "default=yes, global_default=yes"
            ),
            config_parser_args={"config_default": global_default_value},
            config_args={
                "action": action,
                "nargs": nargs,
                "type": type,
                "default": default_value,
            },
            dict_source=mcp.NOT_GIVEN,
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
                f"nargs_0_or_1_with_const, action={action}, nargs=?, args=no, "
                f"type={type.__name__}, const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
            },
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=None,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const, action={action}, nargs=?, "
                f"args=yes1, type={type.__name__}, const=yes"
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
                f"nargs_0_or_1_with_const, action={action}, nargs=?, "
                f"args=yes0, type={type.__name__}, const=yes"
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
                f"nargs_0_or_1_with_const, action={action}, nargs=?, args=no, "
                f"type={type.__name__}, default=yes, const=yes"
            ),
            config_args={
                "action": action,
                "nargs": "?",
                "type": type,
                "const": const_value,
                "default": default_value,
            },
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=default_expected,
        )
    )
    nargs_test_specs.append(
        Spec(
            id=(
                f"nargs_0_or_1_with_const, action={action}, nargs=?, "
                f"args=yes1, type={type.__name__}, default=yes, const=yes"
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
                f"nargs_0_or_1_with_const, action={action}, nargs=?, "
                f"args=yes0, type={type.__name__}, default=yes, const=yes"
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
        id="store_const, args=yes, const=no (missing const)",
        config_args={"action": "store_const"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_const, args=yes",
        config_args={"action": "store_const", "const": "c"},
        dict_source=None,
        argparse_source="--c",
        expected="c",
    ),
    Spec(
        id="store_const, args=invalid1",
        config_args={"action": "store_const", "const": "c"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_const, args=no, default=yes",
        config_args={"action": "store_const", "const": "c", "default": "d"},
        dict_source=mcp.NOT_GIVEN,
        argparse_source="",
        expected="d",
    ),
]
test_specs.extend(store_const_test_specs)

store_true_test_specs = [
    Spec(
        id="store_true, args=yes, const=yes (invalid const)",
        config_args={"action": "store_true", "const": "v"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_true, args=no",
        config_args={"action": "store_true"},
        dict_source=mcp.NOT_GIVEN,
        argparse_source="",
        expected=False,
    ),
    Spec(
        id="store_true, args=yes",
        config_args={"action": "store_true"},
        dict_source=None,
        argparse_source="--c",
        expected=True,
    ),
    Spec(
        id="store_true, args=invalid1",
        config_args={"action": "store_true"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_true, args=no, default=yes",
        config_args={"action": "store_true", "default": "d"},
        dict_source=mcp.NOT_GIVEN,
        argparse_source="",
        expected="d",
    ),
]
test_specs.extend(store_true_test_specs)

store_false_test_specs = [
    Spec(
        id="store_false, args=yes, const=yes (invalid const)",
        config_args={"action": "store_false", "const": "v"},
        dict_source=None,
        argparse_source="--c",
        expected=Exception,
    ),
    Spec(
        id="store_false, args=no",
        config_args={"action": "store_false"},
        dict_source=mcp.NOT_GIVEN,
        argparse_source="",
        expected=True,
    ),
    Spec(
        id="store_false, args=yes",
        config_args={"action": "store_false"},
        dict_source=None,
        argparse_source="--c",
        expected=False,
    ),
    Spec(
        id="store_false, args=invalid1",
        config_args={"action": "store_false"},
        dict_source="v",
        argparse_source="--c v",
        expected=Exception,
    ),
    Spec(
        id="store_false, args=no, default=yes",
        config_args={"action": "store_false", "default": "d"},
        dict_source=mcp.NOT_GIVEN,
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
        nargs_not0_actions,
        (mcp.NOT_GIVEN, 1, 2, "?", "*", "+"),
        (mcp.NOT_GIVEN,),
    ),
    ((a, mcp.NOT_GIVEN, mcp.NOT_GIVEN) for a in nargs_none_actions),
    ((a, mcp.NOT_GIVEN, "c") for a in nargs_none_with_const_actions),
):
    extra_config_args = {}
    if nargs is not mcp.NOT_GIVEN:
        extra_config_args["nargs"] = nargs
    if const is not mcp.NOT_GIVEN:
        extra_config_args["const"] = const

    suppress_test_specs.append(
        Spec(
            id=(
                f"suppress, action={action}, nargs={nargs}, args=no, "
                "default=suppress"
            ),
            config_args={
                "action": action,
                "default": mcp.SUPPRESS,
                **extra_config_args,
            },
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=mcp.NOT_GIVEN,
        )
    )
    suppress_test_specs.append(
        Spec(
            id=(
                f"suppress, action={action}, nargs={nargs}, args=no, "
                "default=none, config_default=suppress"
            ),
            config_args={"action": action, **extra_config_args},
            config_parser_args={"config_default": mcp.SUPPRESS},
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=mcp.NOT_GIVEN,
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
                f"required, action={action}, nargs=no, args=yes, "
                f"type={type.__name__}, required=yes, default=no"
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
                f"required, action={action}, nargs=no, args=no, "
                f"type={type.__name__}, required=yes, default=yes"
            ),
            config_args={
                "action": action,
                "required": True,
                "default": expected,
                "type": type,
            },
            dict_source=mcp.NOT_GIVEN,
            argparse_source="",
            test_without_source=True,
            expected=Exception,
        ),
    )
    required_test_specs.append(
        Spec(
            id=f"required, action={action}, nargs=no, args=no, required=yes",
            config_args={"action": action, "required": True, "type": type},
            dict_source=mcp.NOT_GIVEN,
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
                f"choices, action={action}, nargs=no, args=yes, "
                f"type={type.__name__}, choices={choices}, "
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
        pytest.skip('the "dict" source does not support this test')
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_dict(spec)
    else:
        _test_spec_with_dict(spec)


def _test_spec_with_dict(spec):
    print(spec)
    mcp_parser = mcp.ConfigParser(**spec.config_parser_args)
    mcp_parser.add_config("c", **spec.config_args)
    dict_source = {}
    if spec.dict_source is not mcp.NOT_GIVEN:
        dict_source["c"] = spec.dict_source
    mcp_parser.add_source("dict", dict_source)
    values = mcp_parser.parse_config()
    if spec.expected is mcp.NOT_GIVEN:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_env(spec):
    if spec.dict_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip('the "environment" source does not support this test')
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_env(spec)
    else:
        _test_spec_with_env(spec)


def _test_spec_with_env(spec):
    mcp_parser = mcp.ConfigParser(**spec.config_parser_args)
    mcp_parser.add_config("c", **spec.config_args)
    env = {}
    if spec.dict_source is not mcp.NOT_GIVEN:
        if isinstance(spec.dict_source, list):
            env["MULTICONFIG_TEST_C"] = shlex_join(
                (str(v) for v in spec.dict_source)
            )
        elif spec.dict_source is None:
            env["MULTICONFIG_TEST_C"] = ""
        else:
            env["MULTICONFIG_TEST_C"] = shlex.quote(str(spec.dict_source))
    mcp_parser.add_source(
        "environment", env_var_prefix="MULTICONFIG_TEST_",
    )
    with utm.patch.object(os, "environ", env):
        values = mcp_parser.parse_config()
    if spec.expected is mcp.NOT_GIVEN:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_json(spec):
    if spec.dict_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip('the "json" source does not support this test')
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_json(spec)
    else:
        _test_spec_with_json(spec)


def _test_spec_with_json(spec):
    mcp_parser = mcp.ConfigParser(**spec.config_parser_args)
    mcp_parser.add_config("c", **spec.config_args)
    dict_source = {}
    if spec.dict_source is not mcp.NOT_GIVEN:
        dict_source["c"] = spec.dict_source
    fileobj = io.StringIO(json.dumps(dict_source, cls=JsonEncoderWithPath))
    mcp_parser.add_source("json", fileobj=fileobj)
    values = mcp_parser.parse_config()
    if spec.expected is mcp.NOT_GIVEN:
        assert not hasattr(values, "c")
    else:
        assert getattr(values, "c") == spec.expected


@pytest.mark.parametrize("spec", test_specs, ids=[s.id for s in test_specs])
def test_spec_with_argparse(spec):
    if spec.argparse_source is OMIT_TEST_FOR_SOURCE:
        pytest.skip('the "simple_argparse" source does not support this test')
        return
    if spec.expected is Exception:
        with pytest.raises(Exception):
            _test_spec_with_argparse(spec)
    else:
        _test_spec_with_argparse(spec)


def _test_spec_with_argparse(spec):
    mcp_parser = mcp.ConfigParser(**spec.config_parser_args)
    mcp_parser.add_config("c", **spec.config_args)
    mcp_parser.add_source(
        "simple_argparse", argument_parser_class=RaisingArgumentParser
    )
    argv = ["prog", *spec.argparse_source.split()]
    with utm.patch.object(sys, "argv", argv):
        values = mcp_parser.parse_config()
    if spec.expected is mcp.NOT_GIVEN:
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
        if config_default is mcp.SUPPRESS:
            rap_args["argument_default"] = argparse.SUPPRESS
        elif config_default is not mcp.NOT_GIVEN:
            rap_args["argument_default"] = config_default

    if (
        "default" in spec.config_args
        and spec.config_args["default"] == mcp.SUPPRESS
    ):
        spec.config_args["default"] = argparse.SUPPRESS

    ap_parser = RaisingArgumentParser(**rap_args)
    ap_parser.add_argument("--c", **spec.config_args)
    ap_values = ap_parser.parse_args(spec.argparse_source.split())
    if spec.expected is mcp.NOT_GIVEN:
        assert not hasattr(ap_values, "c")
    else:
        assert getattr(ap_values, "c") == spec.expected


# ------------------------------------------------------------------------------
# simple_argparse source tests
# ------------------------------------------------------------------------------


def test_simple_argparse_source_with_config_added_after_source():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", required=True)
    mcp_parser.add_config("c2", default="v2")
    mcp_parser.add_source("simple_argparse")
    mcp_parser.add_config("c3")
    mcp_parser.add_config("c4", default="v4")
    with utm.patch.object(sys, "argv", "prog --c1 v1 --c3 v3".split()):
        with pytest.raises(SystemExit):
            values = mcp_parser.parse_config()
    with utm.patch.object(sys, "argv", "prog --c1 v1".split()):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
        {"c1": "v1", "c2": "v2", "c3": None, "c4": "v4"}
    )
    assert values == expected_values


def test_simple_argparse_source_with_prog():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_source(
        "simple_argparse",
        argument_parser_class=RaisingArgumentParser,
        prog="PROG_TEST",
    )
    with utm.patch.object(sys, "argv", "prog --c1 v1".split()):
        with pytest.raises(ArgparseError, match="PROG_TEST"):
            mcp_parser.parse_config()


def test_simple_argparse_source_with_count():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count")
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(sys, "argv", "prog --c1 --c1".split()):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 2})
    assert values == expected_values


def test_simple_argparse_source_with_count_with_default():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count", default=10)
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(sys, "argv", "prog --c1 --c1".split()):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 12})
    assert values == expected_values


def test_simple_argparse_source_with_count_missing():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count")
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(sys, "argv", "prog".split()):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": None})
    assert values == expected_values


def test_simple_argparse_source_with_count_missing_with_default():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", action="count", default=10)
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(sys, "argv", "prog".split()):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict({"c1": 10})
    assert values == expected_values


# ------------------------------------------------------------------------------
# "json" source tests
# ------------------------------------------------------------------------------


def test_json_source_with_config_added_after_source():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", required=True)
    mcp_parser.add_config("c2", default="v2")
    fileobj = io.StringIO(
        """{
        "c1": "v1",
        "c3": "v3"
    }"""
    )
    mcp_parser.add_source("json", fileobj=fileobj)
    mcp_parser.add_config("c3")
    mcp_parser.add_config("c4", default="v4")
    values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
        {"c1": "v1", "c2": "v2", "c3": None, "c4": "v4"}
    )
    assert values == expected_values


# ------------------------------------------------------------------------------
# Multiple source tests
# ------------------------------------------------------------------------------


def test_multiple_sources():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config("c1", type=int, choices=[1, 2], required=True)
    mcp_parser.add_config("c2", type=str, default="v2")
    mcp_parser.add_config("c3", type=pathlib.Path)
    mcp_parser.add_config("c4", type=split_str, default="word1 word2".split())
    mcp_parser.add_config("c5", choices=["v5", "v5a"], required=True)
    mcp_parser.add_config("c6", default="v6")
    mcp_parser.add_config("c7", type=json.loads)
    mcp_parser.add_config("c8", default="v8")
    mcp_parser.add_config("c9", action="append", type=int, default=[0, 1])
    mcp_parser.add_config("c10", action="count", default=10)
    fileobj = io.StringIO(
        """{
        "c1": 1,
        "c2": "v2a",
        "c9": 2,
        "c10": null
    }"""
    )
    mcp_parser.add_source("json", fileobj=fileobj)
    mcp_parser.add_source("dict", {"c9": 3, "c10": None})
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(
        sys, "argv", "prog --c5 v5 --c7 [1,2] --c9 4 --c10 --c10".split()
    ):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
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


def test_include_exclude():
    mcp_parser = mcp.ConfigParser()
    mcp_parser.add_config(
        "c1", action="append", include_sources=("simple_argparse", "json"),
    )
    mcp_parser.add_config(
        "c2", action="append", exclude_sources=("dict", "json"),
    )
    mcp_parser.add_config("c3", action="append")
    fileobj = io.StringIO(
        """{
        "c1": "v1_json",
        "c2": "v2_json",
        "c3": "v3_json"
    }"""
    )
    mcp_parser.add_source("json", fileobj=fileobj)
    d = {
        "c1": "v1_dict",
        "c2": "v2_dict",
        "c3": "v3_dict",
    }
    mcp_parser.add_source("dict", d)
    argv = "prog --c1 v1_ap --c2 v2_ap --c3 v3_ap".split()
    mcp_parser.add_source("simple_argparse")
    with utm.patch.object(sys, "argv", argv):
        values = mcp_parser.parse_config()
    expected_values = mcp._namespace_from_dict(
        {
            "c1": ["v1_json", "v1_ap"],
            "c2": ["v2_ap"],
            "c3": ["v3_json", "v3_dict", "v3_ap"],
        }
    )
    assert values == expected_values


# ------------------------------------------------------------------------------
# Free function tests
# ------------------------------------------------------------------------------


def test_getattr_or_none():
    obj = mcp.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mcp.NOT_GIVEN)
    assert mcp._getattr_or_none(obj, "c1") == "v1"
    assert mcp._getattr_or_none(obj, "c2") is None
    assert mcp._getattr_or_none(obj, "c3") is mcp.NOT_GIVEN
    assert mcp._getattr_or_none(obj, "c4") is mcp.NOT_GIVEN


def test_has_nonnone_attr():
    obj = mcp.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mcp.NOT_GIVEN)
    assert mcp._has_nonnone_attr(obj, "c1")
    assert mcp._has_nonnone_attr(obj, "c2")
    assert not mcp._has_nonnone_attr(obj, "c3")
    assert not mcp._has_nonnone_attr(obj, "c4")
