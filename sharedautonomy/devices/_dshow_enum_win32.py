"""Minimal DirectShow COM interfaces for Windows UVC enumeration.

Adapted from pygrabber (MIT) interface definitions.
"""

from __future__ import annotations

from ctypes.wintypes import _ULARGE_INTEGER

from comtypes import COMMETHOD, GUID, HRESULT, IPersist, IUnknown, POINTER, c_int, c_ulong

CLSID_SystemDeviceEnum = "{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}"
CLSID_VideoInputDeviceCategory = "{860BB310-5D01-11d0-BD3B-00A0C911CE86}"


class IPersistStream(IPersist):
    _case_insensitive_ = True
    _iid_ = GUID("{00000109-0000-0000-C000-000000000046}")
    _idlflags_: list[str] = []


class ISequentialStream(IUnknown):
    _case_insensitive_ = True
    _iid_ = GUID("{0C733A30-2A1C-11CE-ADE5-00AA0044773D}")
    _idlflags_: list[str] = []


class IStream(ISequentialStream):
    _case_insensitive_ = True
    _iid_ = GUID("{0000000C-0000-0000-C000-000000000046}")
    _idlflags_: list[str] = []


IPersistStream._methods_ = [
    COMMETHOD([], HRESULT, "IsDirty"),
    COMMETHOD([], HRESULT, "Load", (["in"], POINTER(IStream), "pstm")),
    COMMETHOD([], HRESULT, "Save", (["in"], POINTER(IStream), "pstm"), (["in"], c_int, "fClearDirty")),
    COMMETHOD([], HRESULT, "GetSizeMax", (["out"], POINTER(_ULARGE_INTEGER), "pcbSize")),
]


class IEnumMoniker(IUnknown):
    _case_insensitive_ = True
    _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
    _idlflags_: list[str] = []


class IMoniker(IPersistStream):
    _case_insensitive_ = True
    _iid_ = GUID("{0000000F-0000-0000-C000-000000000046}")
    _idlflags_: list[str] = []


IMONIKER = POINTER(IMoniker)


class IBindCtx(IUnknown):
    _case_insensitive_ = True
    _iid_ = GUID("{0000000E-0000-0000-C000-000000000046}")
    _idlflags_: list[str] = []


IEnumMoniker._methods_ = [
    COMMETHOD([], HRESULT, "Next", (["in"], c_ulong, "celt"), (["out"], POINTER(POINTER(IMoniker)), "rgelt"), (["out"], POINTER(c_ulong), "pceltFetched")),
    COMMETHOD([], HRESULT, "Skip", (["in"], c_ulong, "celt")),
    COMMETHOD([], HRESULT, "Reset"),
    COMMETHOD([], HRESULT, "Clone", (["out"], POINTER(POINTER(IEnumMoniker)), "ppenum")),
]

IMoniker._methods_ = [
    COMMETHOD([], HRESULT, "BindToObject", (["in"], POINTER(IBindCtx), "pbc"), (["in"], POINTER(IMoniker), "pmkToLeft"), (["in"], POINTER(GUID), "riidResult"), (["out"], POINTER(POINTER(IUnknown)), "ppvResult")),
    COMMETHOD([], HRESULT, "BindToStorage", (["in"], POINTER(IBindCtx), "pbc"), (["in"], POINTER(IMoniker), "pmkToLeft"), (["in"], POINTER(GUID), "riid"), (["out"], POINTER(POINTER(IUnknown)), "ppvObj")),
]


class ICreateDevEnum(IUnknown):
    _case_insensitive_ = True
    _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
    _idlflags_: list[str] = []


ICreateDevEnum._methods_ = [
    COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID), "clsidDeviceClass"), (["out"], POINTER(POINTER(IEnumMoniker)), "ppEnumMoniker"), (["in"], c_int, "dwFlags")),
]


def enumerate_dshow_video_input_devices() -> list[tuple[int, str, str]]:
    """Return DirectShow capture devices as (opencv_index, friendly_name, device_path)."""
    from comtypes import GUID, client
    from comtypes.persist import IPropertyBag

    system_device_enum = client.CreateObject(
        GUID(CLSID_SystemDeviceEnum),
        interface=ICreateDevEnum,
    )
    enum_moniker = system_device_enum.CreateClassEnumerator(
        GUID(CLSID_VideoInputDeviceCategory),
        0,
    )

    devices: list[tuple[int, str, str]] = []
    index = 0
    while True:
        try:
            moniker, count = enum_moniker.Next(1)
        except ValueError:
            break
        if count <= 0:
            break
        property_bag = moniker.BindToStorage(0, 0, IPropertyBag._iid_).QueryInterface(IPropertyBag)
        friendly_name = str(property_bag.Read("FriendlyName", pErrorLog=None))
        device_path = _read_moniker_display_name(moniker)
        devices.append((index, friendly_name, device_path))
        index += 1
    return devices


def _read_moniker_display_name(moniker: object) -> str:
    get_display_name = getattr(moniker, "GetDisplayName", None)
    if get_display_name is None:
        return ""
    try:
        return str(get_display_name(0, 0))
    except Exception:
        return ""
