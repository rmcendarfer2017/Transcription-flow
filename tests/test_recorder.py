import numpy as np
import pytest

from helpers import FakeSoundDevice, PortAudioError, tone
from transcription_flow.recorder import (
    LevelMeter, MicrophoneOpenError, NoMicrophoneError, Recorder, SilentMicrophoneError,
    input_devices, resample,
)


def test_normal_recording_returns_the_audio_and_releases_the_mic(fake_sd):
    rec = Recorder(sd=fake_sd)
    assert not rec.is_open  # nothing opened before the press
    rec.start()
    assert fake_sd.stream.started and fake_sd.stream.samplerate == 16000
    clip = tone(1.0)
    fake_sd.stream.feed(clip[:8000])
    fake_sd.stream.feed(clip[8000:])
    audio = rec.stop()
    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, clip)
    assert fake_sd.stream.closed and not rec.is_open  # open on press, closed on release


def test_stop_returns_a_copy_that_later_audio_cannot_change(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    fake_sd.stream.feed(tone(0.5))
    first = rec.stop()
    snapshot = first.copy()
    rec.start()
    fake_sd.stream.feed(np.ones(8000, dtype=np.float32))
    rec.stop()
    np.testing.assert_array_equal(first, snapshot)


def test_no_input_device_raises_a_clear_error():
    sd = FakeSoundDevice(device_info=False)
    with pytest.raises(NoMicrophoneError, match="No microphone"):
        Recorder(sd=sd).start()
    assert sd.streams == []


def test_device_without_input_channels_counts_as_no_mic():
    sd = FakeSoundDevice(device_info={"name": "speakers", "max_input_channels": 0,
                                      "default_samplerate": 48000})
    with pytest.raises(NoMicrophoneError):
        Recorder(sd=sd).check()


def test_unsupported_16k_falls_back_to_default_rate_and_resamples():
    sd = FakeSoundDevice(accepted_rates={48000}, default_rate=48000)
    rec = Recorder(sd=sd)
    rec.start()
    assert [s.samplerate for s in sd.streams] == [16000, 48000]
    sd.stream.feed(tone(1.0, rate=48000))
    audio = rec.stop()
    assert abs(len(audio) - 16000) <= 1


def test_every_rate_rejected_raises_open_error():
    sd = FakeSoundDevice(accepted_rates=set(), default_rate=44100)
    with pytest.raises(MicrophoneOpenError):
        Recorder(sd=sd).start()


def test_all_zero_audio_means_blocked_microphone(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    fake_sd.stream.feed(np.zeros(16000, dtype=np.float32))
    with pytest.raises(SilentMicrophoneError, match="Privacy"):
        rec.stop()
    assert not rec.is_open


def test_quiet_but_not_zero_audio_is_not_flagged(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    fake_sd.stream.feed(np.full(16000, 1e-4, dtype=np.float32))
    assert len(rec.stop()) == 16000


def test_empty_clip_is_not_flagged_as_silent(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    assert len(rec.stop()) == 0


def test_stereo_input_is_downmixed(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    left, right = tone(0.5), np.zeros(8000, dtype=np.float32)
    fake_sd.stream.feed(np.stack([left, right], axis=1), channels=2)
    np.testing.assert_allclose(rec.stop(), left / 2, rtol=1e-6)


def test_audio_outside_a_recording_is_dropped(fake_sd):
    rec = Recorder(sd=fake_sd, keep_warm=True)
    rec.open()
    rec.start()
    fake_sd.stream.feed(tone(0.5))
    rec.cancel()
    fake_sd.stream.feed(tone(0.5))  # disarmed, warm: goes to pre-roll only
    assert rec.is_open


def test_keep_warm_keeps_the_stream_and_adds_preroll(fake_sd):
    rec = Recorder(sd=fake_sd, keep_warm=True, preroll_seconds=0.25)
    rec.open()
    stream = fake_sd.stream
    for _ in range(10):
        stream.feed(np.full(1600, 0.1, dtype=np.float32))  # 1 s of idle audio
    rec.start()
    stream.feed(np.full(1600, 0.5, dtype=np.float32))
    audio = rec.stop()
    assert not stream.closed and len(fake_sd.streams) == 1
    preroll = audio[:-1600]
    assert 4000 <= len(preroll) <= 4000 + 1600  # about 0.25 s, whole chunks
    assert np.all(preroll == np.float32(0.1)) and np.all(audio[-1600:] == np.float32(0.5))


def test_levels_follow_the_input(fake_sd):
    rec = Recorder(sd=fake_sd)
    rec.start()
    assert rec.levels() == [0.0] * 6
    fake_sd.stream.feed(tone(0.1, amp=0.5))
    assert rec.levels()[-1] > 0.5


def test_resample_keeps_duration():
    assert len(resample(tone(2.0, rate=44100), 44100)) == 32000
    same = tone(0.1)
    out = resample(same, 16000)
    np.testing.assert_array_equal(out, same)
    assert out is not same


# -- microphone choice (M3) ---------------------------------------------------

def mic(name, hostapi=0, inputs=1):
    return {"name": name, "hostapi": hostapi, "max_input_channels": inputs}


DEVICES = [mic("Microsoft Sound Mapper - Input"), mic("Headset mic"), mic("Speakers", inputs=0),
           mic("Laptop mic"), mic("Headset mic", hostapi=1), mic("Laptop mic")]


def test_the_mic_list_is_inputs_on_the_default_host_api_once_each():
    sd = FakeSoundDevice(devices=DEVICES)
    assert input_devices(sd) == ["Headset mic", "Laptop mic"]
    sd.default.hostapi = 1
    assert input_devices(sd) == ["Headset mic"]


def test_the_mic_list_without_a_default_host_api_still_lists_each_name_once():
    sd = FakeSoundDevice(devices=DEVICES)
    del sd.default.hostapi
    assert input_devices(sd) == ["Headset mic", "Laptop mic"]


def test_the_mic_list_is_empty_when_audio_cannot_be_listed():
    assert input_devices(FakeSoundDevice(device_info=False)) == []


def test_the_chosen_mic_is_found_by_name_at_each_open():
    sd = FakeSoundDevice(devices=DEVICES)
    rec = Recorder(sd=sd, device="Laptop mic")
    rec.start()
    rec.stop()
    assert sd.stream.device == 3
    sd.devices.insert(0, dict(mic("New USB mic")))  # indexes move when devices come and go
    sd.devices = [dict(d, index=i) for i, d in enumerate(sd.devices)]
    rec.start()
    rec.stop()
    assert sd.stream.device == 4


def test_a_mic_that_is_not_plugged_in_falls_back_to_the_default(caplog):
    sd = FakeSoundDevice(devices=DEVICES)
    rec = Recorder(sd=sd, device="USB mic I unplugged")
    rec.start()
    rec.stop()
    assert sd.stream.device is None
    assert "not connected; using the Windows default" in caplog.text
    assert "USB mic" not in caplog.text  # device names stay out of the log


def test_no_choice_means_the_default_without_listing():
    sd = FakeSoundDevice(devices=DEVICES)
    sd.query_devices = lambda device=None, kind=None: (
        pytest.fail("listed") if kind is None else sd.device_info)
    rec = Recorder(sd=sd)
    rec.start()
    assert sd.stream.device is None


class RescanningSoundDevice(FakeSoundDevice):
    """Like PortAudio: query_devices() gives the list read at the last
    _initialize(); ``plugged`` is what is plugged in now."""

    def __init__(self, devices, **kw):
        super().__init__(devices=devices, **kw)
        self.plugged = [dict(d) for d in devices]
        self._initialized = 1
        self.restarts = 0
        self.fail_devices = set()  # indexes whose stream won't open

    def _terminate(self):
        assert self._initialized == 1, "terminated twice"
        self._initialized -= 1

    def _initialize(self):
        self._initialized += 1
        self.restarts += 1
        self.devices = [dict(d, index=i) for i, d in enumerate(self.plugged)]

    def InputStream(self, samplerate, channels, dtype, device, callback):  # noqa: N802
        assert self._initialized == 1, "opened a stream with PortAudio stopped"
        gone = device is not None and self.devices[device]["name"] not in {
            d["name"] for d in self.plugged}
        if device in self.fail_devices or gone:
            raise PortAudioError("Device unavailable")
        return super().InputStream(samplerate, channels, dtype, device, callback)


def test_a_mic_plugged_in_while_the_app_runs_is_listed():
    """review-m3 F3: PortAudio's list is read again, not kept from the start."""
    sd = RescanningSoundDevice([mic("Laptop mic")])
    assert input_devices(sd) == ["Laptop mic"]
    sd.plugged.append(mic("New headset"))
    assert input_devices(sd) == ["Laptop mic", "New headset"]


def test_a_mic_unplugged_while_the_app_runs_falls_back_to_the_default():
    sd = RescanningSoundDevice([mic("Laptop mic"), mic("USB mic")])
    rec = Recorder(sd=sd, device="USB mic")
    rec.start()
    rec.stop()
    assert sd.stream.device == 1
    del sd.plugged[1]
    rec.start()
    rec.stop()
    assert sd.stream.device is None and sd.restarts == 1  # not the stale index 1


def test_a_press_with_the_mic_on_the_list_does_not_restart_portaudio():
    """A restart is slow on Windows: never on the hotkey path when it isn't needed."""
    sd = RescanningSoundDevice([mic("Laptop mic"), mic("USB mic")])
    for device in ("USB mic", None):
        rec = Recorder(sd=sd, device=device)
        rec.start()
        rec.stop()
    rec.check()
    assert sd.restarts == 0


def test_an_unplugged_mic_restarts_portaudio_once_not_on_every_press(monkeypatch):
    from transcription_flow import recorder as recorder_mod

    sd = RescanningSoundDevice([mic("Laptop mic"), mic("USB mic")])
    rec = Recorder(sd=sd, device="USB mic")
    del sd.plugged[1]
    for _ in range(3):
        rec.start()
        rec.stop()
        assert sd.stream.device is None
    assert sd.restarts == 1
    monkeypatch.setattr(recorder_mod, "RESCAN_GAP", 0.0)  # a while later: look again
    sd.plugged.append(mic("USB mic"))
    rec.start()
    rec.stop()
    assert sd.restarts == 2 and sd.stream.device == 1


def test_the_device_list_is_not_refreshed_while_a_stream_is_open():
    """A restart would break the warm stream or the open meter."""
    sd = RescanningSoundDevice([mic("Laptop mic")])
    rec = Recorder(sd=sd, keep_warm=True)
    rec.open()
    restarts = sd.restarts
    sd.plugged.append(mic("New headset"))
    assert input_devices(sd) == ["Laptop mic"]  # the old list, the stream is safe
    m = LevelMeter(sd=sd, device="Laptop mic")
    m.start()
    m.stop()
    assert sd.restarts == restarts
    rec.close()
    assert input_devices(sd) == ["Laptop mic", "New headset"]


def test_a_failed_refresh_keeps_working_with_the_old_list(caplog):
    sd = RescanningSoundDevice([mic("Laptop mic")])

    def broken():
        raise PortAudioError("Error initializing PortAudio")

    sd._initialize = broken
    assert input_devices(sd) == ["Laptop mic"]
    assert "could not refresh the device list: PortAudioError" in caplog.text


def test_after_a_failed_start_the_next_refresh_starts_portaudio_again():
    sd = RescanningSoundDevice([mic("Laptop mic")])
    sd._initialized = 0  # the last _initialize() failed
    assert input_devices(sd) == ["Laptop mic"] and sd._initialized == 1


def test_a_named_mic_that_fails_to_open_is_tried_once_as_the_default(caplog):
    """review-m3 F3: a mic gone since the list was read, or one that's busy."""
    sd = RescanningSoundDevice([mic("Laptop mic"), mic("USB mic")])
    sd.fail_devices = {1}
    rec = Recorder(sd=sd, device="USB mic")
    rec.start()
    assert sd.stream.device is None and sd.stream.started
    assert "could not be opened; using the Windows default" in caplog.text
    assert "USB mic" not in caplog.text


def test_the_default_mic_failing_to_open_is_not_retried():
    sd = RescanningSoundDevice([mic("Laptop mic")])
    sd.fail_devices = {None}
    with pytest.raises(MicrophoneOpenError):
        Recorder(sd=sd).start()
    assert sd.streams == []


def test_the_meter_also_uses_a_fresh_list_and_the_default_fallback():
    sd = RescanningSoundDevice([mic("Laptop mic")])
    sd.plugged.append(mic("New headset"))
    m = LevelMeter(sd=sd, device="New headset")
    m.start()
    assert sd.stream.device == 1
    m.stop()
    sd.fail_devices = {1}
    m.start()
    assert sd.stream.device is None


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def meter(sd, **kw):
    clock = Clock()
    m = LevelMeter(sd=sd, clock=clock, **kw)
    m.start()
    return m, clock


def test_meter_hears_speech(fake_sd):
    m, clock = meter(fake_sd)
    assert m.status() == LevelMeter.LISTENING
    fake_sd.stream.feed(tone(0.1, amp=0.3))
    assert m.level() > LevelMeter.HEARD_LEVEL and m.status() == LevelMeter.HEARD
    fake_sd.stream.feed(np.zeros(1600))
    assert m.level() == 0.0 and m.status() == LevelMeter.HEARD  # the peak is kept


def test_meter_calls_exact_silence_blocked_only_after_a_while(fake_sd):
    m, clock = meter(fake_sd)
    fake_sd.stream.feed(np.zeros(1600))
    clock.now += LevelMeter.BLOCKED_AFTER - 0.1
    assert m.status() == LevelMeter.LISTENING
    clock.now += 0.1
    assert m.status() == LevelMeter.BLOCKED


def test_meter_faint_sound_is_quiet_not_blocked(fake_sd):
    m, clock = meter(fake_sd)
    fake_sd.stream.feed(tone(0.1, amp=0.001))
    clock.now += LevelMeter.BLOCKED_AFTER
    assert m.status() == LevelMeter.LISTENING
    clock.now += LevelMeter.QUIET_AFTER - LevelMeter.BLOCKED_AFTER
    assert m.status() == LevelMeter.QUIET


def test_meter_sound_once_then_zeros_is_quiet_not_blocked(fake_sd):
    """review-m3 R10: "any sound yet" is sticky; zeros after it aren't the privacy switch."""
    m, clock = meter(fake_sd)
    fake_sd.stream.feed(tone(0.1, amp=0.001))
    fake_sd.stream.feed(np.zeros(1600))
    clock.now += LevelMeter.QUIET_AFTER
    assert m.status() == LevelMeter.QUIET


def test_meter_restart_forgets_the_last_test_and_stop_closes(fake_sd):
    m, clock = meter(fake_sd)
    fake_sd.stream.feed(tone(0.1))
    first = fake_sd.stream
    m.start()
    assert first.closed and m.status() == LevelMeter.LISTENING and m.level() == 0.0
    m.stop()
    assert fake_sd.stream.closed


def test_meter_uses_the_chosen_mic_and_reports_a_missing_one():
    sd = FakeSoundDevice(devices=DEVICES)
    m, _ = meter(sd, device="Headset mic")
    assert sd.stream.device == 1
    with pytest.raises(NoMicrophoneError):
        LevelMeter(sd=FakeSoundDevice(device_info=False)).start()
