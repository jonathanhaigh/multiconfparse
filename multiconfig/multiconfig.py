#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import argparse
import copy
import json
import operator
import re

# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------


class RequiredConfigNotFoundError(RuntimeError):
    pass


# ------------------------------------------------------------------------------
# Tags
# ------------------------------------------------------------------------------


class _SuppressAttributeCreation:
    def __str__(self):
        return "==SUPPRESS=="


SUPPRESS = _SuppressAttributeCreation()


class _None:
    def __str__(self):
        return "==NONE=="


NONE = _None()

# ------------------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------------------


class Namespace:
    def __str__(self):
        return str(vars(self))

    def __eq__(self, other):
        return other.__class__ == self.__class__ and vars(other) == vars(self)

    __repr__ = __str__


class ArgparseSubparser:
    def __init__(self, config_specs):
        self._config_specs = config_specs
        self._parsed_values = None

    def add_configs_to_argparse_parser(self, argparse_parser):
        for spec in self._config_specs:
            argparse_parser.add_argument(
                self._config_name_to_arg_name(spec.name),
                action=spec.action,
                nargs=spec.nargs,
                const=spec.const,
                default=NONE,
                type=spec.type,
                help=spec.help,
                **spec.parser_specific_options(self.__class__),
            )

    def notify_parsed_args(self, argparse_namespace):
        self._parsed_values = namespace(argparse_namespace, self._config_specs)

    def parse_config(self):
        return self._parsed_values

    @staticmethod
    def _config_name_to_arg_name(config_name):
        return f"--{config_name.replace('_', '-')}"


class SimpleArgparseSubparser(ArgparseSubparser):
    def __init__(self, config_specs):
        super().__init__(config_specs)
        self._argparse_parser = argparse.ArgumentParser()
        super().add_configs_to_argparse_parser(self._argparse_parser)

    def parse_config(self):
        super().notify_parsed_args(self._argparse_parser.parse_args())
        return super().parse_config()


class JsonSubparser:
    def __init__(self, config_specs, path=None, fileobj=None):
        self._config_specs = config_specs
        self._path = path
        self._fileobj = fileobj

        if path and fileobj:
            raise ValueError(
                "JsonSubparser's 'path' and 'fileobj' options were both "
                "specified but only one is expected"
            )

    def parse_config(self):
        json_values = self._get_json()
        values = Namespace()
        for spec in self._config_specs:
            if spec.name in json_values:
                setattr(values, spec.name, json_values[spec.name])
        return namespace(values, self._config_specs)

    def _get_json(self):
        if self._path:
            with open(self._path, mode="r") as f:
                return json.load(f)
        else:
            return json.load(self._fileobj)


class ConfigSpec:
    def __init__(
        self,
        name,
        action="store",
        nargs=None,
        const=None,
        default=NONE,
        type=str,
        required=False,
        help=None,
        parser_specific_options=None,
    ):
        self._name = name
        self._validate_name(name)

        self._action = action
        self._validate_action(action)

        self._nargs = nargs
        self._validate_nargs(nargs)

        self._const = const
        self._validate_const(const)

        self._default = default
        self._type = type
        self._required = required
        self._help = help
        self._parser_specific_options = parser_specific_options or {}

    name = property(operator.attrgetter("_name"))
    action = property(operator.attrgetter("_action"))
    nargs = property(operator.attrgetter("_nargs"))
    const = property(operator.attrgetter("_const"))
    default = property(operator.attrgetter("_default"))
    type = property(operator.attrgetter("_type"))
    required = property(operator.attrgetter("_required"))
    help = property(operator.attrgetter("_help"))

    @staticmethod
    def _validate_name(name):
        if re.match(r"[^0-9A-Za-z_]", name) or re.match(r"^[^a-zA-Z_]", name):
            raise ValueError(
                f"Invalid config name '{name}', "
                "must be a valid Python identifier"
            )

    @staticmethod
    def _validate_action(action):
        if action in (
            "store_const",
            "store_true",
            "store_false",
            "append",
            "append_const",
            "count",
            "extend",
        ):
            raise NotImplementedError(
                f"action '{action}' has not been implemented"
            )
        if action != "store":
            raise ValueError(f"unknown action '{action}'")

    @staticmethod
    def _validate_nargs(nargs):
        if nargs is not None:
            raise NotImplementedError(
                "nargs argument has not been implemented"
            )

    @staticmethod
    def _validate_const(const):
        if const is not None:
            raise NotImplementedError(
                "const argument has not been implemented"
            )

    def parser_specific_options(self, parser_class):
        opts = {}
        for candidate in self._parser_specific_options:
            if issubclass(candidate, parser_class):
                opts.update(self._parser_specific_options[candidate])
        return opts


class ConfigParser:
    def __init__(self, config_default=NONE):
        self._config_specs = []
        self._subparsers = []
        self._parsed_values = Namespace()
        self._config_default = config_default

    def add_config(self, name, **kwargs):
        extra_kwargs = {}
        if "default" not in kwargs:
            extra_kwargs["default"] = self._config_default
        spec = ConfigSpec(name, **kwargs, **extra_kwargs)
        self._config_specs.append(spec)
        return spec

    def add_subparser(self, subparser_class, **kwargs):
        subparser = subparser_class(copy.copy(self._config_specs), **kwargs)
        self._subparsers.append(subparser)
        return subparser

    def add_preparsed_values(self, preparsed_values):
        for spec in self._config_specs:
            value = getattr_or_none(preparsed_values, spec.name)
            if value is not NONE:
                setattr(self._parsed_values, spec.name, value)

    def partially_parse_config(self):
        for parser in self._subparsers:
            new_values = parser.parse_config()
            self.add_preparsed_values(new_values)
        return self._get_configs_with_defaults()

    def parse_config(self):
        values = self.partially_parse_config()
        self._check_required_configs()
        return values

    def _get_configs_with_defaults(self):
        values = copy.copy(self._parsed_values)
        for spec in self._config_specs:
            if not has_nonnone_attr(values, spec.name):
                if spec.default is NONE:
                    setattr(values, spec.name, None)
                elif spec.default is not SUPPRESS:
                    setattr(values, spec.name, spec.default)
        return values

    def _check_required_configs(self):
        for spec in self._config_specs:
            if not has_nonnone_attr(self._parsed_values, spec.name):
                if spec.required:
                    raise RequiredConfigNotFoundError(
                        f"Did not find value for config item '{spec.name}'"
                    )


# ------------------------------------------------------------------------------
# Free functions
# ------------------------------------------------------------------------------


def getattr_or_none(obj, attr):
    if hasattr(obj, attr):
        return getattr(obj, attr)
    return NONE


def has_nonnone_attr(obj, attr):
    return getattr_or_none(obj, attr) is not NONE


def namespace(obj, config_specs):
    ns = Namespace()
    for spec in config_specs:
        if has_nonnone_attr(obj, spec.name):
            setattr(ns, spec.name, getattr(obj, spec.name))
    return ns
