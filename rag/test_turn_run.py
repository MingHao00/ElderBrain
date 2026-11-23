"""一轮的暂停、继续、断开和新问题。存储由测试注入，不连接数据库。"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from agent_memory import CONFIGURABLE_KEY, THREAD_ID_KEY, thread_config
from turn_run import (
    ModelStep,
    ToolObservation,
    TurnCheckpoint,
    TurnRun,
    TurnState,
)

CONVERSATION_ID = "conv-turn"
RUNNING_ID = "conv-running"
PAUSED_ID = "conv-already-paused"
QUESTION = "火球术怎么施放？"
NEW_QUESTION = "治疗轻伤怎么施放？"
PARTIAL_TEXT = "半截：正在查规则。"
CONTINUED_TEXT = "半截：同一轮还留着工具结果。"
TOOL_NAME = "rule_lookup"
TOOL_CONTENT = "父节：火球术造成伤害。"
SEEDED_TURN_ID = "turn-already-paused"
SEEDED_PARTIAL = "已暂停的半截正文。"
SEEDED_TOOL = "已暂停的工具结果。"
HANDLED_BEFORE_RETURN = "before_return"
HANDLED_AFTER_RETURN = "after_return"
WAIT_SECONDS = 2.0
JOIN_SECONDS = 2.0
TOOL_STUCK = "工具没有被放行"
PAUSE_STUCK = "暂停没有开始"
THREAD_STUCK = "后台轮次没有结束"
TOOL_UNBOUND = "假工具还没有绑到这一轮"
MODEL_OVERCALLED = "这一步不能再调用模型"
MISSING_CHECKPOINT = "没有暂停检查点"


class MemoryStore:
    """测试注入的检查点。只留在进程里。"""

    def __init__(self) -> None:
        self._saved: dict[str, TurnCheckpoint] = {}
        self.save_count = 0

    def load(self, thread_id: str) -> TurnCheckpoint | None:
        return self._saved.get(thread_id)

    def save(self, checkpoint: TurnCheckpoint) -> None:
        self.save_count += 1
        self._saved[checkpoint.thread_id] = checkpoint

    def delete(self, thread_id: str) -> None:
        self._saved.pop(thread_id, None)


class ScriptedModel:
    """按脚本回答。脚本用尽后再被调用就算多走了模型循环。"""

    def __init__(self, steps: list[ModelStep]) -> None:
        self._steps = list(steps)
        self.seen: list[TurnState] = []

    def __call__(self, state: TurnState) -> ModelStep:
        index = len(self.seen)
        self.seen.append(state)
        if index >= len(self._steps):
            raise AssertionError(MODEL_OVERCALLED)
        return self._steps[index]


class RecordingTool:
    """挡住不返回，并记下暂停是在返回前还是返回后被处理的。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.handled = ""
        self.saw_pause_request = False
        self._turn: TurnRun | None = None

    def bind(self, turn: TurnRun) -> None:
        self._turn = turn

    def __call__(self, name: str) -> str:
        self.calls += 1
        self.started.set()
        if not self.release.wait(WAIT_SECONDS):
            raise TimeoutError(TOOL_STUCK)
        turn = self._bound_turn()
        self.saw_pause_request = turn.pause_requested
        self.handled = _handled_when(turn.pause_processed)
        return TOOL_CONTENT

    def _bound_turn(self) -> TurnRun:
        if self._turn is None:
            raise RuntimeError(TOOL_UNBOUND)
        return self._turn


class BlockingTool:
    """只挡住返回，用来观察断开时有没有写检查点。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, name: str) -> str:
        self.calls += 1
        self.started.set()
        if not self.release.wait(WAIT_SECONDS):
            raise TimeoutError(TOOL_STUCK)
        return TOOL_CONTENT


def _handled_when(pause_processed: bool) -> str:
    if pause_processed:
        return HANDLED_BEFORE_RETURN
    return HANDLED_AFTER_RETURN


def _spawn(
    target: Callable[[], None],
    errors: list[BaseException],
) -> threading.Thread:
    def _run() -> None:
        try:
            target()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    return thread


def _join(thread: threading.Thread) -> None:
    thread.join(JOIN_SECONDS)
    if thread.is_alive():
        raise TimeoutError(THREAD_STUCK)


def _finish(threads: list[threading.Thread], errors: list[BaseException]) -> None:
    for thread in threads:
        _join(thread)
    if errors:
        raise errors[0]


def _pause_during_tool(
    steps: list[ModelStep],
) -> tuple[MemoryStore, ScriptedModel, RecordingTool, TurnRun, TurnCheckpoint | None]:
    store = MemoryStore()
    model = ScriptedModel(steps)
    tool = RecordingTool()
    turn = TurnRun(CONVERSATION_ID, store, model, tool)
    tool.bind(turn)
    errors: list[BaseException] = []
    starter = _spawn(lambda: turn.start(QUESTION), errors)
    if not tool.started.wait(WAIT_SECONDS):
        raise TimeoutError(TOOL_STUCK)
    pauser = _spawn(turn.pause, errors)
    turn.wait_until_pause_accepted()
    tool.release.set()
    _finish([starter, pauser], errors)
    return store, model, tool, turn, store.load(turn.thread_id)


def _thread_id(conversation_id: str) -> str:
    config = thread_config(conversation_id)
    return config[CONFIGURABLE_KEY][THREAD_ID_KEY]


def test_pause_during_tool_checkpoints_after_return() -> None:
    """工具开始后才暂停。检查点在工具返回后出现，答案仍未提交。"""
    steps = [ModelStep(PARTIAL_TEXT, TOOL_NAME)]
    store, _model, tool, turn, checkpoint = _pause_during_tool(steps)

    assert tool.saw_pause_request is True
    assert tool.handled == HANDLED_AFTER_RETURN
    assert tool.calls == 1
    assert checkpoint is not None
    assert checkpoint.thread_id == CONVERSATION_ID
    assert checkpoint.thread_id == _thread_id(CONVERSATION_ID)
    assert checkpoint.partial_text == PARTIAL_TEXT
    assert checkpoint.user_messages == (QUESTION,)
    assert checkpoint.tool_results == (ToolObservation(TOOL_NAME, TOOL_CONTENT),)
    assert checkpoint.answer_committed is False
    assert turn.answer_committed is False
    assert store.save_count == 1


def test_resume_keeps_same_turn_without_new_user_message() -> None:
    """继续读回同一轮的检查点，不追加用户消息，工具结果还在。"""
    steps = [
        ModelStep(PARTIAL_TEXT, TOOL_NAME),
        ModelStep(CONTINUED_TEXT, None),
    ]
    store, model, tool, _turn, paused = _pause_during_tool(steps)
    assert paused is not None
    continued = TurnRun(CONVERSATION_ID, store, model, tool)
    restored = continued.resume()

    assert restored.turn_id == paused.turn_id
    assert restored.thread_id == paused.thread_id
    assert restored.user_messages == (QUESTION,)
    assert restored.user_messages == paused.user_messages
    assert restored.tool_results == paused.tool_results
    assert restored.partial_text == CONTINUED_TEXT
    assert restored.answer_committed is False
    assert continued.answer_committed is False
    assert model.seen[-1].user_messages == (QUESTION,)
    assert model.seen[-1].tool_results == paused.tool_results
    assert len(model.seen) == 2
    assert tool.calls == 1
    assert store.load(restored.thread_id) == restored


def test_disconnect_does_not_write_checkpoint() -> None:
    """断开取消正在跑的一轮，不写检查点，也不另建已有的暂停检查点。"""
    running_store = MemoryStore()
    running_tool = BlockingTool()
    running = TurnRun(
        RUNNING_ID,
        running_store,
        ScriptedModel([ModelStep(PARTIAL_TEXT, TOOL_NAME)]),
        running_tool,
    )
    errors: list[BaseException] = []
    starter = _spawn(lambda: running.start(QUESTION), errors)
    if not running_tool.started.wait(WAIT_SECONDS):
        raise TimeoutError(TOOL_STUCK)
    running.disconnect()
    running_tool.release.set()
    _finish([starter], errors)

    assert running_tool.calls == 1
    assert running_store.save_count == 0
    assert running_store.load(running.thread_id) is None
    assert running.answer_committed is False

    paused_store = MemoryStore()
    existing = TurnCheckpoint(
        thread_id=_thread_id(PAUSED_ID),
        turn_id=SEEDED_TURN_ID,
        partial_text=SEEDED_PARTIAL,
        user_messages=(QUESTION,),
        tool_results=(ToolObservation(TOOL_NAME, SEEDED_TOOL),),
        answer_committed=False,
    )
    paused_store.save(existing)
    saved = paused_store.save_count
    idle = TurnRun(
        PAUSED_ID,
        paused_store,
        ScriptedModel([]),
        BlockingTool(),
    )
    idle.disconnect()

    assert paused_store.save_count == saved
    assert paused_store.load(idle.thread_id) is existing


def test_new_question_drops_paused_checkpoint_without_model() -> None:
    """暂停中换问题只删除检查点，不启动新的模型循环。"""
    steps = [ModelStep(PARTIAL_TEXT, TOOL_NAME)]
    store, model, tool, turn, paused = _pause_during_tool(steps)
    seen = len(model.seen)
    calls = tool.calls
    assert paused is not None

    turn.discard_for_new_question(NEW_QUESTION)

    assert store.load(turn.thread_id) is None
    assert len(model.seen) == seen
    assert tool.calls == calls
    assert turn.answer_committed is False
    assert all(NEW_QUESTION not in state.user_messages for state in model.seen)
