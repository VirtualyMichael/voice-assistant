from voice_assistant.config import Config
from voice_assistant.gpu_priority import GpuCourtesy, find_ai_processes, set_gpu_priority

PROCS = [(1, "explorer.exe", "explorer.exe"), (2, "llama-server.exe", "llama-server.exe --port 5"), (3, "python.exe", "python.exe chatterbox_server.py --ref x"),
         (4, "python.exe", "python.exe -m voice_assistant"), (5, "VRChat.exe", "VRChat.exe")]


def test_it_finds_only_the_model_server_and_the_cloned_voice_server():
    assert find_ai_processes(lambda: PROCS) == [2, 3]


def test_priority_is_lowered_once_per_process_and_again_after_a_model_reload():
    calls, live = [], [2, 3]
    g = GpuCourtesy("below_normal", apply=lambda pid, lvl: calls.append((pid, lvl)) or True, finder=lambda: list(live))
    assert g.run_once() == 2 and g.run_once() == 0 and calls == [(2, "below_normal"), (3, "below_normal")]
    live[:] = [3, 9]  # LM Studio reloaded the model: a new process
    assert g.run_once() == 1 and calls[-1] == (9, "below_normal") and g.lowered == {3, 9}


def test_a_refused_process_is_retried_and_normal_priority_touches_nothing():
    ok = [False]
    g = GpuCourtesy("idle", apply=lambda pid, lvl: ok[0], finder=lambda: [2])
    assert g.run_once() == 0 and g.lowered == set()
    ok[0] = True
    assert g.run_once() == 1
    calls = []
    assert GpuCourtesy("normal", apply=lambda *a: calls.append(a), finder=lambda: [2]).run_once() == 0 and calls == []


def test_unknown_levels_and_missing_processes_are_refused_safely():
    assert set_gpu_priority(2**31 - 1, "below_normal") is False and set_gpu_priority(1, "realtime") is False


def test_default_is_below_normal():
    assert Config().gpu_priority == "below_normal"
