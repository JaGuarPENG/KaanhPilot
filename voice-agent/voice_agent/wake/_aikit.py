"""ctypes declarations matching the bundled AIKit Win64 C headers.

Importing this module does not load or initialize the SDK.
"""
import ctypes as C
import os
import sys


class InitParam(C.Structure):
    _fields_ = [('authType', C.c_int)] + [
        (name, C.c_char_p) for name in
        ('appID', 'apiKey', 'apiSecret', 'workDir', 'resDir',
         'licenseFile', 'batchID', 'UDID', 'cfgFile')
    ]


class CustomData(C.Structure):
    pass


CustomData._fields_ = [
    ('next', C.POINTER(CustomData)), ('key', C.c_char_p), ('value', C.c_void_p),
    ('reserved', C.c_void_p), ('index', C.c_int32), ('len', C.c_int32),
    ('source', C.c_int32),
]


class BaseData(C.Structure):
    pass


BaseData._fields_ = [
    ('next', C.POINTER(BaseData)), ('desc', C.c_void_p), ('key', C.c_char_p),
    ('value', C.c_void_p), ('reserved', C.c_void_p), ('len', C.c_int32),
    ('type', C.c_int32), ('status', C.c_int32), ('source', C.c_int32),
]


class OutputData(C.Structure):
    _fields_ = [('node', C.POINTER(BaseData)), ('count', C.c_int32), ('totalLen', C.c_int32)]


class BuilderData(C.Structure):
    _fields_ = [('type', C.c_int), ('name', C.c_char_p), ('data', C.c_void_p),
                ('len', C.c_int), ('status', C.c_int)]


OnOutput = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(OutputData))
OnEvent = C.CFUNCTYPE(None, C.c_void_p, C.c_int32, C.c_void_p)
OnError = C.CFUNCTYPE(None, C.c_void_p, C.c_int32, C.c_char_p)


class Callbacks(C.Structure):
    _fields_ = [('outputCB', OnOutput), ('eventCB', OnEvent), ('errorCB', OnError)]


class NativeAPI:
    def __init__(self, dll_dir):
        if sys.platform != 'win32' or C.sizeof(C.c_void_p) != 8:
            raise ValueError('XFYun local wake requires Windows and 64-bit Python')
        self.directory = os.add_dll_directory(str(dll_dir))
        try:
            # Preload the ability DLL so SDK dynamic lookup finds this exact Win64 library.
            self.engines = [C.CDLL(str(path)) for path in sorted(dll_dir.glob('*_aee.dll'))]
            self.dll = C.CDLL(str(dll_dir / 'AEE_lib.dll'))
            self._bind()
        except (OSError, AttributeError):
            self.close()
            raise RuntimeError('Cannot load AIKit Win64 DLLs; check SDK and VC++ runtime') from None

    def _bind(self):
        p, s, i = C.c_void_p, C.c_char_p, C.c_int32
        signatures = {
            'AIKIT_Init': ([C.POINTER(InitParam)], i),
            'AIKIT_UnInit': ([], i),
            'AIKIT_SetLogPath': ([s], i), 'AIKIT_SetLogMode': ([i], i),
            'AIKIT_SetLogLevel': ([i], i),
            'AIKIT_SetAuthCheckInterval': ([C.c_uint32], i),
            'AIKIT_RegisterAbilityCallback': ([s, Callbacks], i),
            'AIKIT_EngineInit': ([s, p], i), 'AIKIT_EngineUnInit': ([s], i),
            'AIKIT_LoadData': ([s, C.POINTER(CustomData)], i),
            'AIKIT_UnLoadData': ([s, s, i], i),
            'AIKIT_SpecifyDataSet': ([s, s, C.POINTER(C.c_int), i], i),
            'AIKIT_Start': ([s, p, p, C.POINTER(p)], i),
            'AIKIT_End': ([p], i), 'AIKIT_Write': ([p, C.POINTER(BaseData)], i),
            'AIKITBuilder_Create': ([i], p),
            'AIKITBuilder_AddString': ([p, s, s, i], i),
            'AIKITBuilder_AddBool': ([p, s, C.c_bool], i),
            'AIKITBuilder_BuildParam': ([p], p),
            'AIKITBuilder_AddBuf': ([p, C.POINTER(BuilderData)], i),
            'AIKITBuilder_BuildData': ([p], C.POINTER(BaseData)),
            'AIKITBuilder_Clear': ([p], None), 'AIKITBuilder_Destroy': ([p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result
            setattr(self, name, fn)

    def close(self):
        self.directory.close()
