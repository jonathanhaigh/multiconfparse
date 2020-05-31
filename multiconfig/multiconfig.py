#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import abc
import argparse
import copy
import json
import re

# Make argparse.FileType available in this module
FileType = argparse.FileType


# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------


class ParseError(RuntimeError):
    """
    Base class for exceptions indicating a configuration error.
    """


class RequiredConfigNotFoundError(ParseError):
    """
    Exception raised when a required config value could not be found from any
    source.
    """


class InvalidChoiceError(ParseError):
    """
    Exception raised when a config value is not from a specified set of values.
    """

    def __init__(self, spec, value):
        choice_str = ",".join((str(c) for c in spec.choices))
        super().__init__(
            f"invalid choice '{value}' for config item '{spec.name}'; "
            f"valid choices are ({choice_str})"
        )


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


class Source(abc.ABC):
    """
    ABC for Source classes.
    """

    @abc.abstractmethod
    def parse_config(self):
        """
        Parse this config source.

        Returns: a multiconfig.Namespace object containing the values parsed
        from this config source.

        The values should *not* be coerced to the type specified by their
        _ConfigSpec.

        Subclasses must implement this method.
        """


class DictSource(Source):
    def __init__(self, config_specs, d):
        self._config_specs = config_specs
        self._dict = d

    def parse_config(self):
        return _namespace_from_dict(self._dict, self._config_specs)


class ArgparseSource(Source):
    def __init__(self, config_specs):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        self._config_specs = config_specs
        self._parsed_values = None

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        return self._parsed_values

    def add_configs_to_argparse_parser(self, argparse_parser):
        """
        Add arguments to an argparse.ArgumentParser object (or an object of a
        subclass) to obtain config values from the command line.
        """
        for spec in self._config_specs:
            if spec.action == "store":
                argparse_parser.add_argument(
                    self._config_name_to_arg_name(spec.name),
                    default=NONE,
                    type=str,
                    help=spec.help,
                )
            elif spec.action in ("store_const", "store_true", "store_false"):
                argparse_parser.add_argument(
                    self._config_name_to_arg_name(spec.name),
                    action="store_true",
                    default=NONE,
                    help=spec.help,
                )
            else:
                # Ignore actions that this source can't handle - maybe another
                # source can instead.
                pass

    def notify_parsed_args(self, argparse_namespace):
        """
        Call this method with the argparse.Namespace object returned by
        argparse.ArgumentParser.parse_args() to notify this ArgparseSource
        object of the results.
        """
        self._parsed_values = _namespace(
            argparse_namespace, self._config_specs
        )

    @staticmethod
    def _config_name_to_arg_name(config_name):
        return f"--{config_name.replace('_', '-')}"


class SimpleArgparseSource(Source):
    """
    Obtains config values from the command line using argparse.ArgumentParser.

    This class is simpler to use than ArgparseSource but does not allow adding
    arguments beside those added to the ConfigParser.

    Do not create objects of this class directly - create them via
    ConfigParser.add_source() instead:
    config_parser.add_source(multiconfig.SimpleArgparseSource, **options)

    Extra options that can be passed to ConfigParser.add_source() for
    SimpleArgparseSource are:
    * argument_parser_class: a class derived from argparse.ArgumentParser to
      use instead of ArgumentParser itself. This can be useful if you want to
      override ArgumentParser's exit() or error() methods.

    * Extra arguments to pass to ArgumentParser.__init__() (or the __init__()
      method for the class specified by the 'argument_parser_class' option.
      E.g.  'prog', 'allow_help'. You probably don't want to use the
      'argument_default' option though - see ConfigParser.__init__()'s
      'config_default' option instead.
    """

    def __init__(
        self,
        config_specs,
        argument_parser_class=argparse.ArgumentParser,
        **kwargs,
    ):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        self._argparse_source = ArgparseSource(config_specs)
        self._argparse_parser = argument_parser_class(**kwargs)
        self._argparse_source.add_configs_to_argparse_parser(
            self._argparse_parser
        )

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        self._argparse_source.notify_parsed_args(
            self._argparse_parser.parse_args()
        )
        return self._argparse_source.parse_config()


class JsonSource(Source):
    """
    Obtains config values from a JSON file.

    Do not create objects of this class directly - create them via
    ConfigParser.add_source() instead:
    config_parser.add_source(multiconfig.JsonSource, **options)

    Extra options that can be passed to ConfigParser.add_source() for
    JsonSource are:
    * path: path to the JSON file to parse.
    * fileobj: a file object representing a stream of JSON data.

    Note: exactly one of the 'path' and 'fileobj' options must be given.
    """

    def __init__(self, config_specs, path=None, fileobj=None):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        self._config_specs = config_specs
        self._path = path
        self._fileobj = fileobj

        if path and fileobj:
            raise ValueError(
                "JsonSource's 'path' and 'fileobj' options were both "
                "specified but only one is expected"
            )

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        return _namespace_from_dict(self._get_json(), self._config_specs)

    def _get_json(self):
        if self._path:
            with open(self._path, mode="r") as f:
                return json.load(f)
        else:
            return json.load(self._fileobj)


class _ConfigSpec(abc.ABC):
    """
    Base class for config specifications.
    """

    # Dict of subclasses that handle specific actions. The name of the action
    # is the dict item's key and the subclass is the dict item's value.
    _subclasses = {}

    def __init_subclass__(cls, **kwargs):
        """
        Automatically register subclasses specialized to handle a particular
        action. For a subclass to be registered it must have the name of the
        action it handles in an 'action' class attribute.
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "action"):
            cls._subclasses[cls.action] = cls

    @classmethod
    def create(cls, action="store", **kwargs):
        """
        Factory to obtain _ConfigSpec objects with the correct subclass to
        handle the given action.
        """
        if action in ("append", "append_const", "count", "extend",):
            raise NotImplementedError(
                f"action '{action}' has not been implemented"
            )
        if action not in cls._subclasses:
            raise ValueError(f"unknown action '{action}'")
        return cls._subclasses[action](**kwargs)

    def __init__(self, name, help=None):
        """
        Don't call this directly - use create() instead.
        """
        self._set_name(name)
        self.help = help

    @abc.abstractmethod
    def accumulate_value(self, current, raw_new):
        """
        Combine a new raw value for this config with any existing value.

        This method must be implemented by subclasses.

        Args:
        * current: The current value for this config item (which may be NONE).
        * raw_new: The new value to combine with the current value for this
          config item. The value has *not* already been coerced to the config's
          type - this function is responsible for doing that if required.

        Returns: the new combined value.
        """

    @abc.abstractmethod
    def apply_default(self, value):
        """
        Returns a value for this config item after applying defaults.

        This method must be implemented by subclasses.

        Args:
        * value: The current value for this config item (which maybe NONE).
          This value has already been coerced to the config's type (unless it
          is NONE).
        """

    def _set_name(self, name):
        if re.match(r"[^0-9A-Za-z_]", name) or re.match(r"^[^a-zA-Z_]", name):
            raise ValueError(
                f"Invalid config name '{name}', "
                "must be a valid Python identifier"
            )
        self.name = name

    def _set_type(self, type):
        """
        Validate and set the type of this config item.

        This is a default implementation that may be called by subclasses.
        """
        if not callable(type):
            raise TypeError("'type' argument must be callable")
        self.type = type


class _StoreConfigSpec(_ConfigSpec):
    action = "store"

    def __init__(
        self,
        nargs=NONE,
        const=NONE,
        default=NONE,
        type=str,
        choices=NONE,
        required=False,
        **kwargs,
    ):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(**kwargs)
        self._set_nargs(nargs)
        self._set_const(const)
        self.default = default
        super()._set_type(type)
        self.choices = choices
        self.required = required

    def accumulate_value(self, current, raw_new):
        if raw_new is NONE:
            return current
        new = self.type(raw_new)
        if self.choices is not NONE and new not in self.choices:
            raise InvalidChoiceError(self, new)
        return new

    def apply_default(self, value):
        if value is NONE and self.default is not NONE:
            return self.default
        return value

    def _set_nargs(self, nargs):
        if nargs is not NONE:
            raise NotImplementedError(
                "'nargs' argument has not been implemented for "
                "'{self.action}' action"
            )
        self.nargs = nargs

    def _set_const(self, const):
        if const is not NONE:
            raise NotImplementedError(
                "'const' argument has not been implemented for "
                f"'{self.action}' action"
            )
        self.const = const


class _StoreConstConfigSpec(_ConfigSpec):
    action = "store_const"

    def __init__(
        self, const, default=NONE, **kwargs,
    ):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(**kwargs)
        self.const = const
        self.default = default
        self.required = False

    def accumulate_value(self, current, raw_new):
        if raw_new is NONE:
            return current
        return self.const

    def apply_default(self, value):
        if value is NONE and self.default is not NONE:
            return self.default
        return value


class _StoreTrueConfigSpec(_StoreConstConfigSpec):
    action = "store_true"

    def __init__(self, default=False, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(const=True, default=default, **kwargs)


class _StoreFalseConfigSpec(_StoreConstConfigSpec):
    action = "store_false"

    def __init__(self, default=True, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(const=False, default=default, **kwargs)


class ConfigParser:
    def __init__(self, config_default=None):
        """
        Create a ConfigParser object.

        Args:
        * config_default: the value to use in the multiconfig.Namespace
          returned by parse_config() for config items for which a value was not
          found in any config source. The default behaviour is to represent
          these config items with None. Set config_default to
          multiconfig.SUPPRESS to prevent these configs from having an
          attribute set in the Namespace at all.
        """
        self._config_specs = []
        self._sources = []
        self._parsed_values = Namespace()
        self._global_default = config_default

    def add_config(self, name, **kwargs):
        """
        Add a config item to this ConfigParser.
        """
        spec = _ConfigSpec.create(name=name, **kwargs)
        self._config_specs.append(spec)
        return spec

    def add_source(self, source_class, *args, **kwargs):
        """
        Add a config source to this ConfigParser.
        """
        source = source_class(copy.copy(self._config_specs), *args, **kwargs)
        self._sources.append(source)
        return source

    def _add_preparsed_values(self, preparsed_values):
        for spec in self._config_specs:
            current = _getattr_or_none(self._parsed_values, spec.name)
            raw_new = _getattr_or_none(preparsed_values, spec.name)
            new = spec.accumulate_value(current, raw_new)
            if new is not NONE:
                setattr(self._parsed_values, spec.name, new)

    def partially_parse_config(self):
        """
        Parse the config sources, but don't raise a RequiredConfigNotFoundError
        exception if a required config is not found in any config source.

        Returns: a multiconfig.Namespace object containing the parsed values.
        """
        for source in self._sources:
            new_values = source.parse_config()
            self._add_preparsed_values(new_values)
        return self._get_configs_with_defaults()

    def parse_config(self):
        """
        Parse the config sources.

        Returns: a multiconfig.Namespace object containing the parsed values.
        """
        values = self.partially_parse_config()
        self._check_required_configs()
        return values

    def _get_configs_with_defaults(self):
        values = copy.copy(self._parsed_values)
        for spec in self._config_specs:
            value = _getattr_or_none(values, spec.name)
            if value is NONE:
                value = spec.apply_default(value)
            if value is not NONE:
                setattr(values, spec.name, value)
            elif self._global_default is NONE:
                setattr(values, spec.name, None)
            elif self._global_default is not SUPPRESS:
                setattr(values, spec.name, self._global_default)
        return values

    def _check_required_configs(self):
        for spec in self._config_specs:
            if not _has_nonnone_attr(self._parsed_values, spec.name):
                if spec.required:
                    raise RequiredConfigNotFoundError(
                        f"Did not find value for config item '{spec.name}'"
                    )


# ------------------------------------------------------------------------------
# Free functions
# ------------------------------------------------------------------------------


def _getattr_or_none(obj, attr):
    if hasattr(obj, attr):
        return getattr(obj, attr)
    return NONE


def _has_nonnone_attr(obj, attr):
    return _getattr_or_none(obj, attr) is not NONE


def _namespace_from_dict(d, config_specs=None):
    ns = Namespace()
    if config_specs is not None:
        for spec in config_specs:
            if spec.name in d:
                setattr(ns, spec.name, d[spec.name])
    else:
        for k, v in d.items():
            setattr(ns, k, v)
    return ns


def _namespace(obj, config_specs=None):
    return _namespace_from_dict(vars(obj), config_specs)
