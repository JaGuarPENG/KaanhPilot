"""Local AIKit wake detector using the listener's existing PCM microphone frames."""
import ctypes as C
from pathlib import Path
import threading
import re

from ._aikit import BuilderData, Callbacks, CustomData, InitParam, NativeAPI
from ._aikit import OnError, OnEvent, OnOutput
from .keyword import KeywordWakeDetector, canonical

ABILITY = b'e867a88f2'
VOICE_ROOT = Path(__file__).resolve().parents[2]
_sdk_lock = threading.Lock()  # AIKit wake does not support concurrent instances.


class WakeWords:
    """Strip actual SDK keywords, including a wake-only suffix retained by pre-roll."""

    def __init__(self, text):
        words = {word.strip() for word in re.split(r'[;\r\n]', text) if word.strip()}
        self.detectors = [KeywordWakeDetector(word) for word in sorted(words, key=len, reverse=True)]
        if not self.detectors:
            raise ValueError('AIKit keyword list contains no wake words')

    def extract_command(self, text):
        for detector in self.detectors:
            command = detector.extract_command(text)
            if command is not None:
                return command
        normalized = canonical(text)
        if normalized and any(d.phrase.endswith(normalized) for d in self.detectors):
            return ''
        return None


def resolve_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else VOICE_ROOT / path


def sdk_paths(value):
    root = resolve_path(value)
    candidates = [root] if (root / 'libs/64/AEE_lib.dll').is_file() else [
        p.parent.parent.parent for p in root.glob('*/libs/64/AEE_lib.dll')
    ]
    if len(candidates) != 1:
        raise ValueError('Set XFYUN_WAKE_SDK_DIR to one unpacked AIKit SDK directory')
    sdk = candidates[0]
    models = sdk / 'bin/resource/ivw70'
    if not all((models / name).is_file() for name in
               ('IVW_FILLER_1', 'IVW_GRAM_1', 'IVW_KEYWORD_1', 'IVW_MLP_1')):
        raise ValueError('Incomplete AIKit wake model resources')
    if not list((sdk / 'libs/64').glob('*_aee.dll')):
        raise ValueError('AIKit Win64 ability DLL is missing')
    return sdk / 'libs/64', models


class XFYunWakeDetector:
    # Listener keeps a short local pre-roll only for this asynchronous detector.
    use_preroll = True

    def __init__(self, settings, *, api=None):
        credentials = [getattr(settings, 'xfyun_' + key).get_secret_value().strip()
                       for key in ('app_id', 'api_key', 'api_secret')]
        if not all(credentials):
            raise ValueError('XFYun local wake requires XFYUN_APP_ID/API_KEY/API_SECRET')
        dll_dir, models = sdk_paths(settings.xfyun_wake_sdk_dir)
        keyword = resolve_path(settings.xfyun_wake_keyword_path)
        if not keyword.is_file() or not keyword.read_text(encoding='utf-8-sig').strip():
            raise ValueError('XFYUN_WAKE_KEYWORD_PATH must point to a nonempty UTF-8 word list')
        self.keyword = WakeWords(keyword.read_text(encoding='utf-8-sig'))
        if not _sdk_lock.acquire(blocking=False):
            raise RuntimeError('Only one XFYun local wake detector may run per process')
        self._api = None
        self._sdk = self._engine = self._loaded = self._accept = False
        self._closed = False
        self._handle = C.c_void_p()
        self._builder = None
        self._buffer = bytearray()
        self._hit = threading.Event()
        self._callback_lock = threading.Lock()
        self._error = None
        self._threshold = settings.xfyun_wake_threshold.encode('ascii')
        # Strong references must survive until SDK shutdown (callbacks run on native threads).
        self._callbacks = Callbacks(OnOutput(self._on_output), OnEvent(self._on_event),
                                    OnError(self._on_error))
        try:
            work = resolve_path(settings.xfyun_wake_work_dir)
            work.mkdir(parents=True, exist_ok=True)
            self._api = api if api is not None else NativeAPI(dll_dir)
            self._call('AIKIT_SetLogMode', 2)
            self._call('AIKIT_SetLogLevel', 3)
            self._call('AIKIT_SetLogPath', str(work / 'aikit.log').encode('utf-8'))
            self._call('AIKIT_SetAuthCheckInterval', 600)
            self._init = InitParam(0, *(v.encode('utf-8') for v in credentials),
                                   str(work).encode('utf-8'), str(models).encode('utf-8'),
                                   None, None, None, None)
            self._call('AIKIT_Init', C.byref(self._init))
            self._sdk = True
            self._call('AIKIT_RegisterAbilityCallback', ABILITY, self._callbacks)
            self._call('AIKIT_EngineInit', ABILITY, None)
            self._engine = True
            self._keyword_buf = C.create_string_buffer(str(keyword).encode('utf-8'))
            data = CustomData(None, b'key_word', C.cast(self._keyword_buf, C.c_void_p),
                              None, 0, len(self._keyword_buf.value), 2)
            self._call('AIKIT_LoadData', ABILITY, C.byref(data))
            self._loaded = True
            self._builder = self._api.AIKITBuilder_Create(1)
            if not self._builder:
                raise RuntimeError('AIKit audio builder creation failed')
            self._start()
        except BaseException:
            self.close()
            raise

    def _call(self, name, *args):
        code = getattr(self._api, name)(*args)
        if code:
            raise RuntimeError(f'{name} failed (AIKit code {code})')

    def _start(self):
        indices = (C.c_int * 1)(0)
        self._call('AIKIT_SpecifyDataSet', ABILITY, b'key_word', indices, 1)
        builder = self._api.AIKITBuilder_Create(0)
        if not builder:
            raise RuntimeError('AIKit parameter builder creation failed')
        try:
            self._call('AIKITBuilder_AddString', builder, b'wdec_param_nCmThreshold',
                       self._threshold, len(self._threshold))
            self._call('AIKITBuilder_AddBool', builder, b'gramLoad', True)
            params = self._api.AIKITBuilder_BuildParam(builder)
            if not params:
                raise RuntimeError('AIKit parameter construction failed')
            self._call('AIKIT_Start', ABILITY, params, None, C.byref(self._handle))
            if not self._handle:
                raise RuntimeError('AIKit returned an empty session handle')
            self._first = True
            self._accept = True
        finally:
            self._api.AIKITBuilder_Destroy(builder)

    def _on_output(self, handle, output):
        with self._callback_lock:
            if not self._accept or handle != self._handle.value or not output:
                return
            result = output.contents
            if result.count > 0 and result.node:
                node = result.node.contents
                if node.key and node.value and node.len > 0:
                    self._hit.set()

    def _on_event(self, handle, kind, data):
        # Lifecycle notifications are not recognition results. Errors arrive via onError.
        pass

    def _on_error(self, handle, code, description):
        with self._callback_lock:
            if not self._closed and (handle is None or handle == self._handle.value):
                # Null handles may report SDK-wide authorization errors. Ignore old sessions.
                self._error = f'AIKit wake error {code}'  # Never expose SDK body or credentials.

    def process(self, pcm):
        if self._closed:
            raise RuntimeError('XFYun wake detector is closed')
        if len(pcm) % 2:
            raise ValueError('Wake audio must contain complete mono PCM16 samples')
        if self._error:
            raise RuntimeError(self._error)
        if not self._handle:
            self._start()
        self._buffer.extend(pcm)
        # SDK sample uses 640-byte / 20-ms frames; microphone frames may be 10/20/30 ms.
        while len(self._buffer) >= 640 and not self._hit.is_set():
            chunk = C.create_string_buffer(bytes(self._buffer[:640]))
            del self._buffer[:640]
            self._api.AIKITBuilder_Clear(self._builder)
            data = BuilderData(1, b'wav', C.cast(chunk, C.c_void_p), 640,
                               0 if self._first else 1)
            self._call('AIKITBuilder_AddBuf', self._builder, C.byref(data))
            audio = self._api.AIKITBuilder_BuildData(self._builder)
            if not audio:
                raise RuntimeError('AIKit audio construction failed')
            self._call('AIKIT_Write', self._handle, audio)
            self._first = False
        if self._error:
            raise RuntimeError(self._error)
        if self._hit.is_set():
            self.reset()  # Stop SDK session during command recording / TTS.
            return True
        return False

    def reset(self):
        with self._callback_lock:
            self._accept = False
        try:
            if self._handle:
                handle, self._handle = self._handle, C.c_void_p()
                self._call('AIKIT_End', handle)
        finally:
            self._buffer.clear()
            self._hit.clear()
        # Errors are deliberately not swallowed; a broken engine must not silently stay idle.

    def close(self):
        if self._closed:
            return
        self._closed = True
        with self._callback_lock:
            self._accept = False
        try:
            # Cleanup each acquired resource even when an earlier cleanup operation fails.
            if self._api is not None:
                operations = []
                if self._handle:
                    operations.append(('AIKIT_End', (self._handle,)))
                if self._builder:
                    operations.append(('AIKITBuilder_Destroy', (self._builder,)))
                if self._loaded:
                    operations.append(('AIKIT_UnLoadData', (ABILITY, b'key_word', 0)))
                if self._engine:
                    operations.append(('AIKIT_EngineUnInit', (ABILITY,)))
                if self._sdk:
                    operations.append(('AIKIT_UnInit', ()))
                for name, args in operations:
                    try:
                        getattr(self._api, name)(*args)
                    except Exception:
                        pass
                self._api.close()
        finally:
            _sdk_lock.release()
