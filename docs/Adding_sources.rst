.. py:currentmodule:: multiconfparse

Adding config sources
=====================

To add config sources to a :class:`ConfigParser`, use :meth:`ConfigParser.add_source` method:

.. automethod:: ConfigParser.add_source
   :noindex:

The built-in config source classes are:

.. autoclass:: ArgparseSource
   :noindex:

.. autoclass:: SimpleArgparseSource
   :noindex:

.. autoclass:: EnvironmentSource
   :noindex:

.. autoclass:: JsonSource
   :noindex:

.. autoclass:: DictSource
   :noindex:
