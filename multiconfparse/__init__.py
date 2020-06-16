#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import abc
import argparse
import copy
import functools
import json
import operator
import os
import re
import shlex

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


class InvalidNumberOfValuesError(ParseError):
    """
    Exception raised when the number of values supplied for a config item is
    not valid.
    """

    def __init__(self, spec, value):
        assert spec.nargs != "*"
        if spec.nargs == 1:
            expecting = "1 value"
        elif isinstance(spec.nargs, int):
            expecting = f"{spec.nargs} values"
        elif spec.nargs == "?":
            expecting = "up to 1 value"
        else:
            assert spec.nargs == "+"
            expecting = "1 or more values"

        super().__init__(
            f"invalid number of values for config item {spec.name}; "
            f"expecting {expecting}"
        )


class InvalidValueForNargs0Error(ParseError):
    """
    Exception raised when a value recieved for a config item with nargs=0 is
    not a "none" value.
    """

    def __init__(self, value, none_values):
        none_values_str = ", ".join((str(v) for v in none_values))
        super().__init__(
            f"invalid value '{str(value)}' for config item with nargs=0; "
            f"valid values: ({none_values_str})"
        )


# ------------------------------------------------------------------------------
# Tags
# ------------------------------------------------------------------------------


class _SuppressAttributeCreation:
    def __str__(self):
        return "SUPPRESS"

    __repr__ = __str__


#: Singleton used as a ``default`` value for a config item to indicate that if
#: no value is found for the config item in any source, it should not be given
#: an attribute in the :class:`Namespace` returned by
#: :meth:`ConfigParser.parse_config`. The default behaviour (when a
#: ``default`` value is not given by the user) is for the :class:`Namespace`
#: returned by :meth:`ConfigParser.parse_config` to have an attribute with a
#: value of :data:`None`.
SUPPRESS = _SuppressAttributeCreation()


class _NotGiven:
    def __str__(self):
        return "NOT_GIVEN"

    __repr__ = __str__


#: Singleton used to represent that an option or config item is not present.
#:
#: This is used rather than :data:`None` to distinguish between:
#:
#: * the case where the user has provided an option with the value :data:None`
#:   and the user has not provided an option at all;
#:
#: * the case where a config item has the value :data:`None` and the case
#:   where the config item not been mentioned at all in the config. Contrast
#:   this with :const:`MENTIONED_WITHOUT_VALUE` which represents a config
#:   item that has been mentioned in the config but does not have a value (e.g.
#:   for a config item with ``nargs == 0``).
NOT_GIVEN = _NotGiven()


class _MentionedWithoutValue:
    def __str__(self):
        return "MENTIONED_WITHOUT_VALUE"

    __repr__ = __str__


#: Singleton used to represent that a config item has been mentioned in a
#: config source but does not have a value.
#:
#: This is used rather than :data:`None` to distinguish between: the case
#: where a config item has the value :data:`None` and the case where the
#: config item does not have a value at all (e.g. for a config item with
#: ``nargs == 0``. Contrast this with :const:`NOT_GIVEN` which represents a
# config item that has not been mentioned in the config at all.
MENTIONED_WITHOUT_VALUE = _MentionedWithoutValue()


def mentioned_without_value(v):
    return MENTIONED_WITHOUT_VALUE


# ------------------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------------------


class Namespace:
    """
    An object to hold values of config items.

    :class:`Namespace` objects are essentially plain objects used as the return
    values of :meth:`ConfigParser.parse_config`. Retrieve values with normal
    attribute accesses:

    .. code-block:: python

        config_values = parser.parse_config()
        config_value = config_values.config_name

    To set values (if, for example, you are implementing a :class:`Source`
    subclass), use ``setattr``:

    .. code-block:: python

        ns = multiconfparse.Namespace()
        setattr(ns, config_name, config_value)
    """

    def __str__(self):
        return str(vars(self))

    def __eq__(self, other):
        return other.__class__ == self.__class__ and vars(other) == vars(self)

    __repr__ = __str__


class Source(abc.ABC):
    """
    Abstract base for classes that parse config sources.

    All config source classes should inherit from :class:`Source` and provide
    an implementation for its :meth:`parse_config` method.
    """

    def __init__(self, priority=0):
        self.priority = priority

    @abc.abstractmethod
    def parse_config(self):
        """
        Read the values of config items for this source.

        This is an abstract method that subclasses must implement to return a
        :class:`Namespace` object where:

        * The returned :class:`Namespace` has an attribute for each config item
          found. The name of the attribute for a config item must be the config
          item's ``name`` as specified by the ``name`` attribute of its
          :class:`ConfigSpec`.

        * The value for each attribute is a list, where each element of the
          list is a value given for the config item in the source. The elements
          should be ordered so that values appearing earlier in the source are
          earlier in the list. In the common case where only a single value for
          the config item is given in the source, the attribute's value should
          be a list with a single element.

          For example, if ``config_item1`` is mentioned in the source once with
          value ``"v1"`` and ``config_item2`` is mentioned in the source twice
          with values ``"v2"`` and then ``"v3"``, the returned
          :class:`Namespace` object would have an attribute ``config_item1``
          with value ``["v1"]`` and an attribute ``config_item2`` with value
          ``["v2", "v3"]``.

        * If a config item with ``nargs == 0`` or ``nargs == "?"`` is mentioned
          in the source without a value (or possibly with a source-specific
          value that means ``None``/``null`` for sources where a value must
          always be given for a config item), the value for that mention of the
          config item should be given in the list of values as
          ``MENTIONED_WITHOUT_VALUE``.

        * If a config item with ``nargs == None``, ``nargs == 1`` or
          ``nargs == "?"`` is mentioned in the source with a value, the value
          for that mention of the config item should be given in the list of
          values as the value itself.

        * If a config item with ``nargs >= 2`` , ``nargs == "*"`` or
          ``nargs == "+"`` is mentioned in the source, the value for that
          mention of the config item should be given in the list of values as a
          list of the values/arguments given in that mention.

          For example, if a config item ``config_item1`` with ``nargs == 2``
          appears in the source first with values/arguments of ``"v1a"`` and
          ``"v1b"`` and then again with values/arguments of ``"v2a"`` and
          ``"v2b"``, the ``config_item1`` attribute in the :class:`Namespace`
          should have a value of ``[["v1a", "v1b"], ["v2a", "v2b"]]``.

        * None of the values returned should yet have been coerced into the
          types specified by the user in :meth:`ConfigParser.add_config`.
        """


class DictSource(Source):
    """
    Obtains config values from a Python :class:`dict` object.

    Do not create :class:`DictSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")

        values_dict = {
            "config_item1": "v1",
            "config_item2": [1, 2],
            "config_item3": None,
        }
        parser.add_source(multiconfparse.DictSource, values_dict)
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for :class:`DictSource`
    are:

    * ``source_class`` (required, positional):
      :class:`multiconfparse.DictSource`.

    * ``values_dict`` (required, positional): the :class:`dict` containing
      the config values.

      Note that:

      * Values in ``values_dict`` for config items with ``nargs == 0`` or
        ``nargs == "?"`` (where the ``const`` value should be used rather than
        the value from the dict) should be values from the ``none_values`` list
        described below.

      * Values in ``values_dict`` for config items with ``nargs >= 2``,
        ``nargs == "+"`` or ``nargs == "*"`` should be :class:`list` objects
        with an element for each argument of the config item.

        In the special case where ``nargs == "+"`` or ``nargs == "*"`` and
        there is a single argument for the config item, the value may be given
        without the enclosing :class:`list`, unless the argument is itself a
        :class:`list`.

    * ``none_values`` (optional, keyword): a list of values that, when seen in
      ``values_dict``, should be treated as if they were not present (i.e.
      values for config items with ``nargs == 0`` or ``nargs == "?"`` (where
      the ``const`` value should be used rather than the value from the dict).

      The default ``none_values`` is
      ``[None, multiconfparse.MENTIONED_WITHOUT_VALUE]``. using
      ``none_values=[multiconfparse.MENTIONED_WITHOUT_VALUE]`` is useful if
      you want :data:`None` to be treated as a valid config value.

    * ``priority`` (optional, keyword): The priority for the
      :class:`DictSource`. The default priority for a :class:`DictSource`
      is ``0``.
    """

    def __init__(
        self, config_specs, values_dict, none_values=None, priority=0,
    ):
        super().__init__(priority=priority)
        self._config_specs = config_specs
        self._dict = values_dict
        if none_values is None:
            none_values = [None, MENTIONED_WITHOUT_VALUE]
        self._none_values = none_values

    def parse_config(self):
        ns = Namespace()
        for spec in self._config_specs:
            if spec.name not in self._dict:
                continue
            value = self._dict[spec.name]
            if spec.nargs == "?" and value in self._none_values:
                value = MENTIONED_WITHOUT_VALUE
            if spec.nargs == "*":
                if value in self._none_values:
                    value = []
                elif not isinstance(value, list):
                    value = [value]
            if spec.nargs == "+" and not isinstance(value, list):
                value = [value]
            if spec.nargs == 0:
                if value not in self._none_values:
                    raise InvalidValueForNargs0Error(value, self._none_values)
                value = MENTIONED_WITHOUT_VALUE
            setattr(ns, spec.name, [value])
        return ns


class EnvironmentSource(Source):
    """
    Obtains config values from the environment.

    Do not create :class:`EnvironmentSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        # For demonstration purposes, set some config values in environment
        # variables
        os.environ["MY_APP_CONFIG_ITEM1"] = "v1"
        os.environ["MY_APP_CONFIG_ITEM2"] = "1 2"
        os.environ["MY_APP_CONFIG_ITEM3"] = ""

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")

        parser.add_source(
            multiconfparse.EnvironmentSource,
            env_var_prefix="MY_APP_"
        )
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for
    :class:`EnvironmentSource` are:

    * ``source_class`` (required, positional):
      :class:`multiconfparse.EnvironmentSource`.

    * ``none_values`` (optional, keyword): a list of values that, when seen in
      environment variables, should be treated as if they were not present
      (i.e.  values for config items with ``nargs == 0`` or ``nargs == "?"``
      (where the ``const`` value should be used rather than the value from the
      dict).

      The default ``none_values`` is ``[""]``. using a different value for
      ``none_values`` is useful you want the empty string to be treated as a
      valid config value.

    * ``priority`` (optional, keyword): The priority for the
      :class:`EnvironmentSource`. The default priority for a
      :class:`EnvironmentSource` is ``10``.

    * ``env_var_prefix`` (optional, keyword): a string prefixed to the
      environment variable names that :class:`EnvironmentSource` will look for.
      The default value is ``""``.

    Note that:

    * The name of the environment variable for a config item is the config
      item's name, converted to upper case, then prefixed with
      ``env_var_prefix``.

    * Values in environment variables for config items with ``nargs == 0`` or
      ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the environment variable) should be values from the
      ``none_values`` list described above.

    * Values in environment variables for config items with ``nargs >= 2``,
      ``nargs == "+"`` or ``nargs == "*"`` are split into arguments by
      :func:`shlex.split` (i.e. like arguments given on a command line via a
      shell). See the :mod:`shlex` documentation for full details.
    """

    def __init__(
        self, config_specs, none_values=None, priority=10, env_var_prefix="",
    ):
        super().__init__(priority=priority)
        self._config_specs = config_specs

        if none_values is None:
            none_values = [""]
        self._none_values = none_values
        self._env_var_prefix = env_var_prefix

    def parse_config(self):
        ns = Namespace()
        for spec in self._config_specs:
            env_name = self._config_name_to_env_name(spec.name)
            if env_name not in os.environ:
                continue
            value = os.environ[env_name]
            if spec.nargs == "?" and value in self._none_values:
                value = MENTIONED_WITHOUT_VALUE
            elif spec.nargs == "*":
                if value in self._none_values:
                    value = []
                else:
                    value = shlex.split(value)
            elif spec.nargs == "+":
                value = shlex.split(value)
            elif spec.nargs == 0:
                if value not in self._none_values:
                    raise InvalidValueForNargs0Error(value, self._none_values)
                value = MENTIONED_WITHOUT_VALUE
            elif isinstance(spec.nargs, int) and spec.nargs > 1:
                value = shlex.split(value)
            setattr(ns, spec.name, [value])
        return ns

    def _config_name_to_env_name(self, config_name):
        return f"{self._env_var_prefix}{config_name.upper()}"


class ArgparseSource(Source):
    """
    Obtains config values from an :class:`argparse.ArgumentParser`.

    Do not create :class:`ArgparseSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")
        argparse_source = parser.add_source(multiconfparse.ArgparseSource)

        argparse_parser = argparse.ArgumentParser()
        argparse_parser.add_argument("arg1")
        argparse_parser.add_argument("--opt1", type=int, action="append")
        argparse_source.add_configs_to_argparse_parser(argparse_parser)

        args = argparse_parser.parse_args((
            "arg1_value --config-item1 v1 --config-item2 1 2 --opt1 opt1_v1 "
            "--config-item-3 --opt1 opt1_v2"
        ).split())

        argparse_source.notify_parsed_args(args)

        config_parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    :class:`ArgparseSource` does not create an :class:`argparse.ArgumentParser`
    for you. This is to allow extra command line arguments to be added to an
    :class:`argparse.ArgumentParser` that are not config items. Instead
    :class:`ArgparseSource` provides two methods to provide communication with
    the :class:`argparse.ArgumentParser`:

    .. automethod:: ArgparseSource.add_configs_to_argparse_parser
        :noindex:

    .. automethod:: ArgparseSource.notify_parsed_args
        :noindex:

    If you don't need to add command line arguments other than for config
    items, see :class:`SimpleArgparseSource`.

    The arguments of :meth:`ConfigParser.add_source` for
    :class:`ArgparseSource` are:

    * ``source_class`` (required, positional):
      :class:`multiconfparse.ArgparseSource`.

    * ``priority`` (optional, keyword): The priority for the
      :class:`ArgparseSource`. The default priority for a
      :class:`ArgparseSource` is ``20``.

    Note that:

    * The name of the command line argument for a config item is the config
      item's name with underscores (``_``) converted to hyphens (``-``) and
      prefixed with ``--``.
    """

    def __init__(self, config_specs, priority=20):
        super().__init__(priority=priority)
        self._config_specs = config_specs
        self._parsed_values = None

    def parse_config(self):
        return self._parsed_values

    def add_configs_to_argparse_parser(self, argparse_parser):
        """
        Add arguments to an :class:`argparse.ArgumentParser` for config items.
        """
        for spec in self._config_specs:
            arg_name = self._config_name_to_arg_name(spec.name)
            kwargs = {
                "help": spec.help,
                "default": [],
            }
            if spec.nargs == 0:
                kwargs["action"] = "append_const"
            else:
                kwargs["action"] = "append"

            if spec.nargs is not None and spec.nargs not in (0, 1):
                kwargs["nargs"] = spec.nargs

            if spec.nargs in ("?", 0):
                kwargs["const"] = MENTIONED_WITHOUT_VALUE

            argparse_parser.add_argument(arg_name, **kwargs)

    def notify_parsed_args(self, argparse_namespace):
        """
        Notify the :class:`ArgparseSource` of the :class:`argparse.Namespace`
        object returned by :meth:`argparse.ArgumentParser.parse_args`.
        """
        ns = Namespace()
        for spec in self._config_specs:
            if not hasattr(argparse_namespace, spec.name):
                continue
            values = getattr(argparse_namespace, spec.name)
            if values:
                setattr(ns, spec.name, values)
        self._parsed_values = ns

    @staticmethod
    def _config_name_to_arg_name(config_name):
        return f"--{config_name.replace('_', '-')}"


class SimpleArgparseSource(Source):
    """
    Obtains config values from the command line.

    This class is simpler to use than :class:`ArgparseSource` but does not
    allow adding arguments that are not config items.

    Do not create objects of this class directly - create them via
    :meth:`ConfigParser.add_source` instead. For example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")
        parser.add_source(multiconfparse.SimpleArgparseSource)
        config_parser.parse_config()
        # If the command line looks something like:
        #    PROG_NAME --config-item1 v1 --config-item2 1 2 --config-item-3
        # The result would be:
        # multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for
    :class:`ArgparseSource` are:

    * ``source_class`` (required, positional):
      :class:`multiconfparse.ArgparseSource`.

    * ``argument_parser_class`` (optional, keyword): a class derived from
      :class:`argparse.ArgumentParser` to use instead of
      :class:`ArgumentParser` itself. This can be useful if you want to
      override :meth:`argparse.ArgumentParser.exit` or
      :meth:`argparse.ArgumentParser.error`.

    * ``priority`` (optional, keyword): The priority for the
      :class:`SimpleArgparseSource`. The default priority for a
      :class:`SimpleArgparseSource` is ``20``.

    * Extra keyword arguments to pass to :class:`argparse.ArgumentParser`.
      E.g.  ``prog``, ``allow_help``. Don't use the ``argument_default`` option
      though - :class:`SimpleArgparseSource` sets this internally. See the
      ``config_default`` option for :class:`ConfigParser` instead.

    Note that:

    * The name of the command line argument for a config item is the config
      item's name with underscores (``_``) converted to hyphens (``-``) and
      prefixed with ``--``.
    """

    def __init__(
        self,
        config_specs,
        argument_parser_class=argparse.ArgumentParser,
        priority=20,
        **kwargs,
    ):
        super().__init__(priority=priority)
        self._argparse_source = ArgparseSource(config_specs)
        self._argparse_parser = argument_parser_class(**kwargs)
        self._argparse_source.add_configs_to_argparse_parser(
            self._argparse_parser
        )

    def parse_config(self):
        self._argparse_source.notify_parsed_args(
            self._argparse_parser.parse_args()
        )
        return self._argparse_source.parse_config()


class JsonSource(Source):
    """
    Obtains config values from a JSON file.

    Do not create objects of this class directly - create them via
    :meth:`ConfigParser.add_source`. For example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")

        fileobj = io.StringIO('''
            {
                "config_item1": "v1",
                "config_item2": [1, 2],
                "config_item3": null
            }
        ''')
        parser.add_source(multiconfparse.JsonSource, fileobj=fileobj)

        config_parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for
    :class:`JsonSource` are:

    * ``source_class`` (required, positional):
      :class:`multiconfparse.JsonSource`.

    * ``priority`` (optional, keyword): The priority for the
      :class:`JsonSource`. The default priority for a :class:`JsonSource` is
      ``0``.

    * ``path`` (optional, keyword): path to the JSON file to parse. Exactly one
      of the ``path`` and ``fileobj`` options must be given.

    * ``fileobj`` (optional keyword): a file object representing a stream of
      JSON data. Exactly one of the ``path`` and ``fileobj`` options must be
      given.

    * ``none_values`` (optional, keyword): a list of python values that, when
      seen as config item values after JSON decoding, should be treated as if
      they were not present (i.e.  values for config items with ``nargs == 0``
      or ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the dict). The default ``none_values`` is ``[]``.

    * ``json_none_values`` (optional, keyword): a list of JSON values (as
      strings) that are decoded into Python values and added to
      ``none_values``.  The default ``json_none_values`` is ``["null"]``.

    Notes:

    * The data in the JSON file should be a JSON object. Each config item value
      should be assigned to a field of the object that has the same name as the
      config item.

    * Fields in the JSON object for config items with ``nargs == 0`` or
      ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the dict) should either have values from the
      ``json_none_values`` list or should decode to values in the
      ``none_values`` list.

    * Fields in the JSON object for config items with ``nargs >= 2``,
      ``nargs == "+"`` or ``nargs == "*"`` should be JSON arrays with an
      element for each argument of the config item.

      In the special case where ``nargs == "+"`` or ``nargs == "*"`` and
      there is a single argument for the config item, the value may be given
      without the enclosing JSON array, unless the argument is itself an array.
    """

    def __init__(
        self,
        config_specs,
        path=None,
        fileobj=None,
        none_values=None,
        json_none_values=None,
        priority=0,
    ):
        super().__init__(priority=priority)
        if path and fileobj:
            raise ValueError(
                "JsonSource's 'path' and 'fileobj' options were both "
                "specified but only one is expected"
            )

        if none_values is None:
            none_values = []
        if json_none_values is None:
            json_none_values = ["null"]

        values_dict = self._get_json(path, fileobj)
        self._dict_source = DictSource(
            config_specs,
            values_dict,
            none_values=[json.loads(v) for v in json_none_values]
            + none_values,
        )

    def parse_config(self):
        return self._dict_source.parse_config()

    @staticmethod
    def _get_json(path, fileobj):
        if path:
            with open(path, mode="r") as f:
                return json.load(f)
        else:
            return json.load(fileobj)


class ConfigSpec(abc.ABC):
    """
    Abstract base class for config specifications.

    Classes to support actions should:

    * Inherit from :class:`ConfigSpec`.

    * Implement the :meth:`accumulate_processed_value` method:

      .. automethod:: accumulate_processed_value
        :noindex:

      For example, the :meth:`accumulate_processed_value` method for the
      "append" action is:

      .. code-block:: python

        def accumulate_processed_value(self, current, new):
            assert new is not NOT_GIVEN
            if self.nargs == "?" and new is MENTIONED_WITHOUT_VALUE:
                new = self.const
            if current is NOT_GIVEN:
                return [new]
            return current + [new]

    * Have an ``action`` class attribute set to the name of the action that the
      class implements.

    * Have an ``__init__()`` method that accepts arguments passed to
      :meth:`ConfigParser.add_config` calls by the user (except the ``action``
      argument) and which calls :meth:`ConfigSpec.__init__` with any of those
      arguments which are not specific to the action handled by the class.
      I.e.:

        * ``name``;

        * ``nargs``;

        * ``type``;

        * ``required``;

        * ``default``;

        * ``choices``;

        * ``help``;

        * ``include_sources``;

        * ``exclude_sources``.

      These arguments will be assigned to attributes of the :class:`ConfigSpec`
      object being created (perhaps after some processing or validation) that
      are available for access by subclasses. The names of the attributes are
      the same as the argument names.

      It is recommended that passing the arguments to
      :meth:`ConfigSpec.__init__` is done by the subclass ``__init__`` method
      accepting a ``**kwargs`` argument to collect any arguments that are not
      used or modified by the action class, then passing that ``**kwargs``
      argument to :meth:`ConfigSpec.__init__`. The action class may also want
      pass some arguments that aren't specified by the user if the value of
      those arguments is implied by the action. For example, the
      ``store_const`` action class has the following ``__init__`` method:

      .. code-block:: python

        def __init__(self, const, **kwargs):
            super().__init__(
                nargs=0,
                type=mentioned_without_value,
                required=False,
                choices=None,
                **kwargs,
            )
            self.const = const

      This ensures that an exception is raised if the user specifies ``nargs``,
      ``type``, ``required``, or ``choices`` arguments when adding a
      ``store_const`` action because if the user specifies those arguments they
      will be given twice in the call to :meth:`ConfigSpec.__init__`.


    The full example of the class for the ``store_const`` action is:

    .. code-block:: python

        class StoreConstConfigSpec(ConfigSpec):
            action = "store_const"

            def __init__(self, const, **kwargs):
                super().__init__(
                    nargs=0,
                    type=mentioned_without_value,
                    required=False,
                    choices=None,
                    **kwargs,
                )
                self.const = const

            def accumulate_processed_value(self, current, new):
                assert new is MENTIONED_WITHOUT_VALUE
                return self.const

    """

    # Dict of subclasses that handle specific actions. The name of the action
    # is the dict item's key and the subclass is the dict item's value.
    _subclasses = {}

    def __init_subclass__(cls, **kwargs):
        # Automatically register subclasses specialized to handle a particular
        # action. For a subclass to be registered it must have the name of the
        # action it handles in an 'action' class attribute.
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "action"):
            cls._subclasses[cls.action] = cls

    @classmethod
    def create(cls, action="store", **kwargs):
        # Factory to obtain ConfigSpec objects with the correct subclass to
        # handle the given action.
        if action == "append_const":
            raise NotImplementedError(
                f"action '{action}' has not been implemented"
            )
        if action not in cls._subclasses:
            raise ValueError(f"unknown action '{action}'")
        return cls._subclasses[action](**kwargs)

    def __init__(
        self,
        name,
        nargs=None,
        type=str,
        required=False,
        default=NOT_GIVEN,
        choices=None,
        help=None,
        include_sources=None,
        exclude_sources=None,
    ):
        self._set_name(name)
        self._set_nargs(nargs)
        self._set_type(type)
        self.required = required
        self.default = default
        self.choices = choices
        self.help = help
        self.include_sources = include_sources
        self.exclude_sources = exclude_sources
        if include_sources is not None and exclude_sources is not None:
            raise ValueError(
                "cannot set both include_sources and exclude_sources"
            )

    def accumulate_raw_value(self, current, raw_new):
        return self.accumulate_processed_value(
            current, self._process_value(raw_new)
        )

    @abc.abstractmethod
    def accumulate_processed_value(self, current, new):
        """
        Combine a new processed value for this config with any existing value.

        This method must be implemented by subclasses.

        Arguments:

        * ``current``: the current value for this config item (which may be
          :const:`NOT_GIVEN` or :const:`MENTIONED_WITHOUT_VALUE`).

        * ``new``: The new value (which will never be :const:`NOT_GIVEN` but
          may be :const:`MENTIONED_WITHOUT_VALUE`) to combine with the current
          value for the config item.

        The ``current`` and ``new`` will have:

        * been coerced to the config item's ``type``;

        * been checked for ``nargs`` and ``choices`` validity;

        * had values for ``nargs == 1`` converted to a single element
          :class:`list`.

        Returns: the new combined value, which must not be :const:`NOT_GIVEN`.
        """

    def _set_name(self, name):
        if re.search(r"[^0-9A-Za-z_]", name) or re.search(
            r"^[^a-zA-Z_]", name
        ):
            raise ValueError(
                f"invalid config name '{name}', "
                "must be a valid Python identifier"
            )
        self.name = name

    def _set_nargs(self, nargs):
        if nargs is None or nargs in ("*", "+", "?") or isinstance(nargs, int):
            self.nargs = nargs
        else:
            raise ValueError(f"invalid nargs value {nargs}")

    def _set_type(self, type):
        if not callable(type):
            raise TypeError("'type' argument must be callable")
        self.type = type

    def _process_value(self, value):
        assert value is not NOT_GIVEN
        if self.nargs == 0:
            assert value is MENTIONED_WITHOUT_VALUE
            return value
        if self.nargs is None:
            new = self.type(value)
            self._validate_choice(new)
            return new
        if self.nargs == "?":
            if value is MENTIONED_WITHOUT_VALUE:
                return value
            else:
                new = self.type(value)
                self._validate_choice(new)
                return new
        if self.nargs == 1:
            new = [self.type(value)]
            self._validate_choices(new)
            return new
        new = [self.type(v) for v in value]
        self._validate_choices(new)
        if self.nargs == "+" and not new:
            raise InvalidNumberOfValuesError(self, new)
        elif isinstance(self.nargs, int) and len(new) != self.nargs:
            raise InvalidNumberOfValuesError(self, new)
        return new

    def _validate_choice(self, value):
        if self.choices is not None and value not in self.choices:
            raise InvalidChoiceError(self, value)

    def _validate_choices(self, values):
        for v in values:
            self._validate_choice(v)


class StoreConfigSpec(ConfigSpec):
    action = "store"

    def __init__(self, const=None, **kwargs):
        super().__init__(**kwargs)
        self._set_const(const)

    def accumulate_processed_value(self, current, new):
        assert new is not NOT_GIVEN
        if self.nargs == "?" and new is MENTIONED_WITHOUT_VALUE:
            return self.const
        return new

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action} action"
            )

    def _set_const(self, const):
        if const is not None and self.nargs != "?":
            raise ValueError(
                f"const cannot be supplied to the {self.action} action "
                f'unless nargs is "?"'
            )
        self.const = const


class StoreConstConfigSpec(ConfigSpec):
    action = "store_const"

    def __init__(self, const, **kwargs):
        super().__init__(
            nargs=0,
            type=mentioned_without_value,
            required=False,
            choices=None,
            **kwargs,
        )
        self.const = const

    def accumulate_processed_value(self, current, new):
        assert new is MENTIONED_WITHOUT_VALUE
        return self.const


class StoreTrueConfigSpec(StoreConstConfigSpec):
    action = "store_true"

    def __init__(self, default=False, **kwargs):
        super().__init__(const=True, default=default, **kwargs)


class StoreFalseConfigSpec(StoreConstConfigSpec):
    action = "store_false"

    def __init__(self, default=True, **kwargs):
        super().__init__(const=False, default=default, **kwargs)


class AppendConfigSpec(ConfigSpec):
    action = "append"

    def __init__(self, const=None, **kwargs):
        super().__init__(**kwargs)
        self._set_const(const)

    def accumulate_processed_value(self, current, new):
        assert new is not NOT_GIVEN
        if self.nargs == "?" and new is MENTIONED_WITHOUT_VALUE:
            new = self.const
        if current is NOT_GIVEN:
            return [new]
        return current + [new]

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action} action"
            )

    def _set_const(self, const):
        if const is not None and self.nargs != "?":
            raise ValueError(
                f"const cannot be supplied to the {self.action} action "
                f'unless nargs is "?"'
            )
        self.const = const


class CountConfigSpec(ConfigSpec):
    action = "count"

    def __init__(
        self, **kwargs,
    ):
        super().__init__(
            nargs=0, type=mentioned_without_value, choices=None, **kwargs
        )

    def accumulate_processed_value(self, current, new):
        assert new is MENTIONED_WITHOUT_VALUE
        if current is NOT_GIVEN:
            return 1
        return current + 1


class ExtendConfigSpec(AppendConfigSpec):
    action = "extend"

    def __init__(self, **kwargs):
        if "nargs" not in kwargs:
            kwargs["nargs"] = "+"
        super().__init__(**kwargs)

    def accumulate_processed_value(self, current, new):
        assert new is not NOT_GIVEN
        if self.nargs == "?" and new is MENTIONED_WITHOUT_VALUE:
            new = self.const
        if not isinstance(new, list):
            new = [new]
        if current is NOT_GIVEN:
            return new
        return current + new


class ConfigParser:
    """
    Create a new ConfigParser object. Options are:

    * ``config_default``: the default value to use in the :class:`Namespace`
      returned by :meth:`parse_config` for config items for which no value was
      found.

      The default behaviour (when ``config_default`` is
      :const:`NOT_GIVEN`) is to represent these config items with the
      value ``None``.

      Set ``config_default`` to :const:`SUPPRESS` to prevent
      these configs from having an attribute set in the :class:`Namespace` at
      all.
    """

    class ValueWithPriority:
        def __init__(self, value, priority):
            self.value = value
            self.priority = priority

        def __str__(self):
            return f"ValueWithPriority({self.value}, {self.priority})"

        __repr__ = __str__

    def __init__(self, config_default=NOT_GIVEN):
        self._config_specs = []
        self._sources = []
        self._parsed_values = {}
        self._global_default = config_default

    def add_config(self, name, **kwargs):
        """
        Add a config item to the :class:`ConfigParser`.

        The arguments that apply to all config items are:

        * ``name`` (required, positional): the name of the config item.

          In the :class:`Namespace` object returned by :meth:`parse_config`,
          the name of the attribute used for this config item will be ``name``
          and must be a valid Python identifier.

          ``name`` is also used by :class:`Source` classes to generate the
          strings that will be used to find the config in config sources. The
          `Source` classes may, use a modified version of ``name``, however.
          For example, the :class:`ArgparseSource` and
          :class:`SimpleArgparseSource` will convert underscores (``_``) to
          hyphens (``-``) and add a ``--`` prefix, so if a config item had the
          name ``"config_item1"``, :class:`ArgparseSource` and
          :class:`SimpleArgparseSource` would use the option string
          ``"--config-item1"``.

        * ``action`` (optional, keyword): the name of the action that should be
          performed when a config item is found in a config source. The default
          action is ``"store"``, and the built-in actions are described briefly
          below. See :ref:`Actions` for more detailed information about
          actions. The built-in actions are all based on :mod:`argparse`
          actions so the :mod:`argparse documentation<argparse>` may also
          provide useful information.

          * ``store``: this action just stores the highest priority value for
            config item.

          * ``store_const``: this stores the value specified in the ``const``
            argument.

          * ``store_true``: this stores the value :data:`True` and sets the
            ``default`` argument to :data:`False`. It is a special case of
            ``store_const``.

          * ``store_false``: this stores the value :data:`False` and sets the
            ``default`` argument to :data:`True`. It is a special case of
            ``store_const``.

          * ``append``: this creates a :class:`list` containing every value
            seen (with lower priority values first). When ``nargs >= 1``,
            ``nargs == "+"`` or ``nargs == "*"``, each value in the list s
            iteself a list containing the arguments for a mention of the
            config item.

          * ``count``: this stores the number of mentions of the config item.

          * ``extend``: this creates a :class:`list` containing every value
            seen (with lower priority values first). Unlike ``append``, when
            ``nargs >= 1``, ``nargs == "+"`` or ``nargs == "*"``, arguments for
            mentions of the config item are not placed in separate sublists for
            each mention.

        * ``default``: the default value for this config item. Note that some
          actions will incorporate the ``default`` value into the final value
          for the config item even if the config item is mentioned in one of
          the sources (e.g. ``append``, ``count`` and ``extend``).

          Note that the default value for all config items can also be set by
          passing a value for the ``config_default`` argument of
          :class:`ConfigParser`. If both the ``config_default`` argument to
          :class:`ConfigParser` and the ``default`` argument to
          :meth:`add_config` are used then only the ``default`` argument to
          :meth:`add_config` is used.

          If a default value is not provided for the config item by the
          ``default`` argument, the ``config_default`` argument or by the
          action class (like e.g. `store_true` does), then the final value for
          the config will be :data:`None` if the config item is not mentioned
          in any source.

          The special value :const:`SUPPRESS` can be passed as the ``default``
          argument. In this case, if the config item is not mentioned in any
          source, it will not be given an attribute in the :class:`Namespace`
          object returned by :meth:`parse_config`.

        * ``exclude_sources`` (optional, keyword): a collection of
          :class:`Source` classes that should ignore this config item. This
          argument is mutually exclusive with ``include_sources``. If neither
          ``exclude_sources`` nor ``include_sources`` is given, the config item
          will be looked for by all sources added to the :class:`ConfigParser`.

        * ``include_sources`` (optional, keyword): a collection of
          :class:`Source` classes that should look for this config item. This
          argument is mutually exclusive with ``exclude_sources``.  If neither
          ``exclude_sources`` nor ``include_sources`` is given, the config item
          will be looked for by all sources added to the :class:`ConfigParser`.

        * ``help``: the help text/description for the config item.

        The other arguments are all keyword arguments and are passed on to the
        class that implements the config items action and may have different
        default values or may not even be valid for all actions. See
        :ref:`Actions` for action specific documentation.

        * ``nargs``: specifies the number of arguments that the config item
          accepts. The values that ``nargs`` can take are:

          * :data:`None`: the config item will take a single argument.

          * ``0``: the config item will take no arguments. This value is
            usually not given to :meth:`add_config` but may be implicit for
            an action (e.g. ``store_const`` or ``count``).

          * An :class:`int` ``N >= 1``: the config item will take ``N``
            arguments and the value for a mention of the config item will be a
            :class:`list` containing each argument. In particular, when ``nargs
            == 1`` the value for each mention of a config item will be a
            :class:`list` containing a single element.

          * ``"?"``: The config item will take a single optional argument. When
            the config item is mentioned without an accompanying value, the
            value for the mention is the value of the config item's ``const``
            argument.

          * ``"*"``: The config item will take zero or more arguments and the
            value for a mention of the config item will be a :class:`list`
            containing each argument.

          * ``"+"`` The config item will take one or more arguments and the
            value for a mention of the config item will be a :class:`list`
            containing each argument.

        * ``const``: The value to use for a mention of the config item where
          there is no accompanying argument. This is only ever used when
          ``nargs == 0`` or ``nargs == "+"``.

        * ``type``: The type to which each argument of the config item should
          be converted. This can be any callable object that takes a single
          argument (an object with a ``__call__(self, arg)`` method), including
          classes like :class:`int` and functions that take a single argument.
          Note that some sources that read typed data may produce config item
          argument values that aren't always :class:`str` objects.

          The default ``type`` is :class:`str` unless that doesn't make sense
          (e.g. when ``nargs == 0``.

        * ``required``: specifies whether an exception should be raised if a
          value for this config item cannot be found in any source.

          The default ``required`` is :data:`False`.

        * ``choices``: specifies a collection of valid values for the arguments
          of the config item. If ``choices`` is specified, an exception is
          raised if the config item is mentioned in a source with an argument
          that is not in ``choices``.
        """
        if "default" not in kwargs and self._global_default is not NOT_GIVEN:
            kwargs["default"] = self._global_default
        spec = ConfigSpec.create(name=name, **kwargs)
        self._config_specs.append(spec)
        return spec

    def add_source(self, source_class, *args, **kwargs):
        """
        Add a new config source to the ConfigParser.

        Return the created config source object.
        """
        source = source_class(copy.copy(self._config_specs), *args, **kwargs)
        self._sources.append(source)
        return source

    def _add_parsed_values(self, values, new_values, source):
        for spec in self._config_specs:
            if not hasattr(new_values, spec.name):
                continue
            if self._ignore_config_for_source(spec, source):
                continue
            if not hasattr(values, spec.name):
                setattr(values, spec.name, [])
            new_vals = getattr(new_values, spec.name)
            getattr(values, spec.name).extend(
                (self.ValueWithPriority(v, source.priority) for v in new_vals)
            )

    def _accumulate_parsed_values(self, parsed_values):
        for spec in self._config_specs:
            # Sort the values according to the priorities of the sources,
            # lowest priority first. When accumulating, sources should give the
            # so-far-accumulated value less priority than a new value.
            #
            # sorted() is guaranteed to use a stable sort so the order in which
            # values are given for any particular source is preserved.
            raw_values = (
                v.value
                for v in sorted(
                    getattr(parsed_values, spec.name),
                    key=operator.attrgetter("priority"),
                )
            )
            accumulated_value = functools.reduce(
                spec.accumulate_raw_value, raw_values
            )
            setattr(parsed_values, spec.name, accumulated_value)
        return parsed_values

    def _parse_config(self, check_required):
        parsed_values = Namespace()
        self._collect_defaults(parsed_values)
        configs_seen = {}
        for source in self._sources:
            new_values = source.parse_config()
            configs_seen.update(vars(new_values))
            self._add_parsed_values(parsed_values, new_values, source)
        self._accumulate_parsed_values(parsed_values)
        if check_required:
            self._check_required_configs(configs_seen)
        self._process_missing(parsed_values)
        return parsed_values

    def _collect_defaults(self, ns):
        for spec in self._config_specs:
            default = spec.default
            if spec.default is SUPPRESS:
                default = NOT_GIVEN
            setattr(ns, spec.name, [self.ValueWithPriority(default, -1)])

    def partially_parse_config(self):
        """
        Parse the config sources, but don't raise a RequiredConfigNotFoundError
        exception if a required config is not found in any config source.

        Returns: a Namespace object containing the parsed
        values.
        """
        return self._parse_config(check_required=False)

    def parse_config(self):
        """
        Parse the config sources.

        Returns: a Namespace object containing the parsed
        values.
        """
        return self._parse_config(check_required=True)

    def _process_missing(self, parsed_values):
        for spec in self._config_specs:
            if getattr(parsed_values, spec.name) is NOT_GIVEN:
                if spec.default is SUPPRESS:
                    delattr(parsed_values, spec.name)
                else:
                    setattr(parsed_values, spec.name, None)

    def _check_required_configs(self, configs_seen):
        for spec in self._config_specs:
            if spec.required and spec.name not in configs_seen:
                raise RequiredConfigNotFoundError(
                    f"Did not find value for config item '{spec.name}'"
                )

    @staticmethod
    def _ignore_config_for_source(config, source):
        if config.exclude_sources is not None:
            return source.__class__ in config.exclude_sources
        if config.include_sources is not None:
            return source.__class__ not in config.include_sources
        return False


# ------------------------------------------------------------------------------
# Free functions
# ------------------------------------------------------------------------------


def _getattr_or_none(obj, attr):
    if hasattr(obj, attr):
        return getattr(obj, attr)
    return NOT_GIVEN


def _has_nonnone_attr(obj, attr):
    return _getattr_or_none(obj, attr) is not NOT_GIVEN


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
