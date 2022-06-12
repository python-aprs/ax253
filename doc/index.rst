.. ax253 documentation master file, created by
   sphinx-quickstart on Sun Jun 12 11:11:37 2022.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

*****
ax253
*****

``ax253`` (pronounced "Ay-Ex-twenty-five-three") is a python implementation
of the ax25 protocol used in Amateur Radio applications.

This library provides the essential building blocks that other applications
can use to encode and decode frames and addresses in ax25 format (common for
KISS TNCs) or TNC2 (APRS-IS format, for UI frames only).

API
===

.. autoclass:: ax253.address.Address
    :members:

.. autoclass:: ax253.frame.Frame
    :members:

.. autoclass:: ax253.frame.Control
    :members:

.. autoclass:: ax253.frame.FrameType
    :members:

Decoders
--------

.. autoclass:: ax253.decode.GenericDecoder
    :members:

.. autoclass:: ax253.decode.FrameDecodeProtocol
    :members:

.. autoclass:: ax253.decode.SyncFrameDecode
    :members:

.. autoclass:: ax253.frame.AX25BytestreamDecoder
    :members:

.. autoclass:: ax253.tnc2.TNC2Decode
    :members:

.. autoclass:: ax253.tnc2.TNC2Protocol
    :members:

Utility
-------

.. autoclass:: ax253.fcs.FCS
    :members:


.. toctree::
   :maxdepth: 2
   :caption: Contents:



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
