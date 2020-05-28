#!/usr/bin/env python3

from multiconfig import multiconfig as mc

import io
import pytest
import sys
import unittest.mock as utm

# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------

def namespace_from_dict(d):
    ns = mc.Namespace()
    for k, v in d.items():
        setattr(ns, k, v)
    return ns


# ------------------------------------------------------------------------------
# ConfigParser tests
# ------------------------------------------------------------------------------

def test_default_value_without_subparsers():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1")
    mc_parser.add_config("c2", default="v2")
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": None,
        "c2": "v2",
    })
    assert values == expected_values

def test_default_value_from_config_default():
    mc_parser = mc.ConfigParser(config_default="v1")
    mc_parser.add_config("c1")
    mc_parser.add_config("c2", default="v2")
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2",
    })
    assert values == expected_values

def test_suppress():
    mc_parser = mc.ConfigParser(config_default=mc.SUPPRESS)
    mc_parser.add_config("c1")
    mc_parser.add_config("c2", default="v2")
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c2": "v2",
    })
    assert values == expected_values

def test_required_config_not_found():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2")
    with pytest.raises(mc.RequiredConfigNotFoundError):
        values = mc_parser.parse_config()

def test_required_config_found():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2")
    mc_parser.add_preparsed_values(namespace_from_dict({"c1": "v1"}))
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": None,
    })
    assert values == expected_values

def test_partially_parse_config():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2")
    mc_parser.add_preparsed_values(namespace_from_dict({"c2": "v2"}))
    values = mc_parser.partially_parse_config()
    expected_values = namespace_from_dict({
        "c1": None,
        "c2": "v2",
    })
    assert values == expected_values

# ------------------------------------------------------------------------------
# SimpleArgparseSubparser tests
# ------------------------------------------------------------------------------

def test_simple_argparse_subparser():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    mc_parser.add_subparser(mc.SimpleArgparseSubparser)
    with utm.patch.object(sys, 'argv', "prog --c1 v1 --c2 v2a".split()):
        values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2a",
        "c3": None,
        "c4": "v4",
    })
    assert values == expected_values

def test_simple_argparse_subparser_with_missing_required_config():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1")
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3", required=True)
    mc_parser.add_config("c4", default="v4")
    mc_parser.add_subparser(mc.SimpleArgparseSubparser)
    with utm.patch.object(sys, 'argv', "prog --c1 v1 --c2 v2a".split()):
        with pytest.raises(mc.RequiredConfigNotFoundError):
            values = mc_parser.parse_config()

def test_simple_argparse_subparser_with_suppress():
    mc_parser = mc.ConfigParser(config_default=mc.SUPPRESS)
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    mc_parser.add_subparser(mc.SimpleArgparseSubparser)
    with utm.patch.object(sys, 'argv', "prog --c1 v1 --c2 v2a".split()):
        values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2a",
        "c4": "v4",
    })
    assert values == expected_values

def test_simple_argparse_subparser_with_config_added_after_subparser():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_subparser(mc.SimpleArgparseSubparser)
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    with utm.patch.object(sys, 'argv', "prog --c1 v1 --c3 v3".split()):
        with pytest.raises(SystemExit):
            values = mc_parser.parse_config()
    with utm.patch.object(sys, 'argv', "prog --c1 v1".split()):
        values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2",
        "c3": None,
        "c4": "v4",
    })
    assert values == expected_values

# ------------------------------------------------------------------------------
# JsonSubparser tests
# ------------------------------------------------------------------------------

def test_json_subparser():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    fileobj = io.StringIO("""{
        "c1": "v1",
        "c2": "v2a"
    }""")
    mc_parser.add_subparser(mc.JsonSubparser, fileobj=fileobj)
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2a",
        "c3": None,
        "c4": "v4",
    })
    assert values == expected_values

def test_json_subparser_with_missing_required_config():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1")
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3", required=True)
    mc_parser.add_config("c4", default="v4")
    fileobj = io.StringIO("""{
        "c1": "v1",
        "c2": "v2a"
    }""")
    mc_parser.add_subparser(mc.JsonSubparser, fileobj=fileobj)
    with pytest.raises(mc.RequiredConfigNotFoundError):
        values = mc_parser.parse_config()

def test_json_subparser_with_suppress():
    mc_parser = mc.ConfigParser(config_default=mc.SUPPRESS)
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    fileobj = io.StringIO("""{
        "c1": "v1",
        "c2": "v2a"
    }""")
    mc_parser.add_subparser(mc.JsonSubparser, fileobj=fileobj)
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2a",
        "c4": "v4",
    })
    assert values == expected_values

def test_json_subparser_with_config_added_after_subparser():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    fileobj = io.StringIO("""{
        "c1": "v1",
        "c3": "v3"
    }""")
    mc_parser.add_subparser(mc.JsonSubparser, fileobj=fileobj)
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2",
        "c3": None,
        "c4": "v4",
    })
    assert values == expected_values

# ------------------------------------------------------------------------------
# Multiple subparser tests
# ------------------------------------------------------------------------------

def test_multiple_subparsers():
    mc_parser = mc.ConfigParser()
    mc_parser.add_config("c1", required=True)
    mc_parser.add_config("c2", default="v2")
    mc_parser.add_config("c3")
    mc_parser.add_config("c4", default="v4")
    mc_parser.add_config("c5", required=True)
    mc_parser.add_config("c6", default="v6")
    mc_parser.add_config("c7")
    mc_parser.add_config("c8", default="v8")
    fileobj = io.StringIO("""{
        "c1": "v1",
        "c2": "v2a"
    }""")
    mc_parser.add_subparser(mc.JsonSubparser, fileobj=fileobj)
    mc_parser.add_subparser(mc.SimpleArgparseSubparser)
    with utm.patch.object(sys, 'argv', "prog --c5 v5 --c6 v6a".split()):
        values = mc_parser.parse_config()
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c2": "v2a",
        "c3": None,
        "c4": "v4",
        "c5": "v5",
        "c6": "v6a",
        "c7": None,
        "c8": "v8",
    })
    assert values == expected_values


# ------------------------------------------------------------------------------
# Free function tests
# ------------------------------------------------------------------------------

def test_getattr_or_none():
    obj = mc.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mc.NONE)
    assert mc.getattr_or_none(obj, "c1") == "v1"
    assert mc.getattr_or_none(obj, "c2") is None
    assert mc.getattr_or_none(obj, "c3") is mc.NONE
    assert mc.getattr_or_none(obj, "c4") is mc.NONE

def test_has_nonnone_attr():
    obj = mc.Namespace()
    setattr(obj, "c1", "v1")
    setattr(obj, "c2", None)
    setattr(obj, "c3", mc.NONE)
    assert mc.has_nonnone_attr(obj, "c1") == True
    assert mc.has_nonnone_attr(obj, "c2") == True
    assert mc.has_nonnone_attr(obj, "c3") == False
    assert mc.has_nonnone_attr(obj, "c4") == False

def test_namespace():
    config_specs = [
        mc.ConfigSpec("c1"),
        mc.ConfigSpec("c2"),
        mc.ConfigSpec("c4"),
    ]
    ns = mc.Namespace()
    setattr(ns, "c1", "v1")
    setattr(ns, "c3", "v3")
    setattr(ns, "c4", "v4")
    values = mc.namespace(ns, config_specs)
    expected_values = namespace_from_dict({
        "c1": "v1",
        "c4": "v4",
    })
    assert values == expected_values
