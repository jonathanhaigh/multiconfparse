.. multiconfparse documentation master file, created by
   sphinx-quickstart on Thu Jun 11 23:06:26 2020.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Multiconfparse
==============

.. toctree::
   :maxdepth: 2
   :caption: Contents:

Multiconfparse is a Python3 library for specifying and reading configuration
data from multiple sources, including the command line, environment variables
and various config file formats. The API is very similar to the Argparse_ API.

Installation
------------
.. code-block:: none

   python -m pip install multiconfparse

Quick start
-----------

1. Import the :py:mod:`multiconfparse` module:

   .. code-block:: python

      import multiconfparse.multiconfparse as mcp

2. Create a :class:`ConfigParser` object:

   .. code-block:: python

      import multiconfparse.multiconfparse as mcp

      config_parser = mcp.ConfigParser()

   :class:`ConfigParser` objects:

   * contain the specifications of your configuration items;
   * have :class:`Source` - objects that can obtain configuration values
     from different sources;
   * coordinate the parsing done by :class:`Source` objects;
   * merge configuration values from :class:`Source` objects into a single
     set of values.

4. Add specifications of your config items:

   .. code-block:: python

      config_parser.add_config("config_item1", required=True)
      config_parser.add_config("config_item2", default="default_value")

5. Add :class:`Source` objects:

   .. code-block:: python

      config_parser.add_source(mcp.SimpleArgparseSource)
      config_parser.add_source(mcp.JsonSource, "/path/to/config/file.json")


6. Parse config from all :class:`Source` objects:

   .. code-block:: python

      config = config_parser.parse_config()

   :py:meth:`ConfigParser.parse_config()` returns a :class:`Namespace` object
   which is essentially just a plain object with attributes for each config
   item.

7. Use the config

   .. code-block:: python

      item1 = config.config_item1
      item2 = config.config_item2


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

.. _argparse: https://docs.python.org/3/library/argparse.html
