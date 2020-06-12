Quick start
===========


Import the :py:mod:`multiconfparse` module
------------------------------------------

   .. code-block:: python

      import multiconfparse.multiconfparse as mcp


Create a :class:`ConfigParser` object
----------------------------------------

   .. code-block:: python

      config_parser = mcp.ConfigParser()

   :class:`ConfigParser` objects:

   * contain the specifications of your configuration items;
   * have :class:`Source` - objects that can obtain configuration values
     from different sources;
   * coordinate the parsing done by :class:`Source` objects;
   * merge configuration values from :class:`Source` objects into a single
     set of values.


Add specifications of your config items
----------------------------------------

   .. code-block:: python

      config_parser.add_config("config_item1", required=True)
      config_parser.add_config("config_item2", default="default_value")
      config_parser.add_config("config_item3", action="append", type=int)

   :meth:`ConfigParser.add_config` has a single required parameter - the name
   of the config item. This is used as the name of the config item's attribute
   in the object returned by the parse and must be a valid Python identifier.
   The optional parameters used above are:

   * ``required`` - if a ``required`` config item cannot be found in any config
     source then :class:`ConfigParser.parse` will raise an exception.
   * ``default_value`` - the value the config item will take if it is not found
     in any source.
   * ``action`` - this specifies the way the values found during parsing will
     be processed. The default action is ``store``, which means that the value
     from the highest priority source will be set as the config item's value in
     the output of the parse. The ``append`` action creates a list of all of
     the values seen from all sources during the parse.
   * ``type`` - this specifies the type of the value that should be returned by
     the parse.

   The :meth:`add_config` method was modeled on the
   :meth:`argparse.ArgumentParser.add_argument` method and accepts mostly the
   same options.


Add config sources
------------------

   .. code-block:: python

      config_parser.add_source(mcp.SimpleArgparseSource)
      config_parser.add_source(mcp.EnvironmentSource, env_var_prefix="MY_APP_")
      config_parser.add_source(mcp.JsonSource, "/path/to/config/file.json")

   :meth:`ConfigParser.add_source`'s first parameter is a class that knows how
   to parse a config source. Other parameters are passed on to that class's
   :meth:`__init__` method.

   In the example above, three sources are added:

   * :class:`SimpleArgparseSource` - a source that reads config values from the
     command line. :class:`SimpleArgparseSource` creates an
     :class:`argparse.ArgumentParser` that will accept ``--config-item1`` and
     ``--config-item2`` options.
   * :class:`EnvironmentSource` - a source that reads config values from
     environment variables. :class:`EnvironmentSource` will look for config
     values in the ``MY_APP_CONFIG_ITEM1`` and ``MY_APP_CONFIG_ITEM2``
     environment variables.
   * :class:`JsonSource` - a source that reads config values from a JSON file.
     In this example it will look for a JSON object in
     "/path/to/config/file.json" and obtain values from the ``"config_item1"``
     and ``"config_item2"`` keys.


Parse config from all :class:`Source` objects
---------------------------------------------

   .. code-block:: python

      config = config_parser.parse_config()

   :py:meth:`ConfigParser.parse_config()` returns a :class:`Namespace` object
   which is essentially just a plain object with attributes for each config
   item. If a config item was not found in any source, and was not a
   ``required`` option then it will (by default) be given a value of ``None``
   in the returned :class:`Namespace` object.


Use the config
--------------

   .. code-block:: python

      item1 = config.config_item1
      item2 = config.config_item2
      item3 = config.config_item3


