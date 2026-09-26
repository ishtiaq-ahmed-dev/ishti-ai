"""
ISHTI Task State Machine
Explicit state management for task execution lifecycle.
"""

import time
import uuid
from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from threading import Lock


class TaskState(Enum):
    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    SEARCHING = "SEARCHING"
    RESEARCHING = "RESEARCHING"
    EXECUTING = "EXECUTING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    RESPONDING = "RESPONDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


# Valid state transitions
VALID_TRANSITIONS = {
    TaskState.IDLE: [TaskState.UNDERSTANDING],
    TaskState.UNDERSTANDING: [TaskState.PLANNING, TaskState.SEARCHING, TaskState.RESEARCHING, TaskState.EXECUTING, TaskState.FAILED],
    TaskState.PLANNING: [TaskState.SEARCHING, TaskState.RESEARCHING, TaskState.EXECUTING, TaskState.FAILED],
    TaskState.SEARCHING: [TaskState.RESEARCHING, TaskState.EXECUTING, TaskState.VERIFYING, TaskState.FAILED, TaskState.WAITING_TOOL],
    TaskState.RESEARCHING: [TaskState.EXECUTING, TaskState.VERIFYING, TaskState.FAILED, TaskState.WAITING_TOOL],
    TaskState.EXECUTING: [TaskState.EXECUTING, TaskState.WAITING_TOOL, TaskState.WAITING_APPROVAL, TaskState.VERIFYING, TaskState.FAILED, TaskState.PAUSED],
    TaskState.WAITING_TOOL: [TaskState.EXECUTING, TaskState.WAITING_TOOL, TaskState.FAILED],
    TaskState.WAITING_APPROVAL: [TaskState.EXECUTING, TaskState.CANCELLED, TaskState.FAILED],
    TaskState.VERIFYING: [TaskState.VERIFYING, TaskState.RESPONDING, TaskState.COMPLETED, TaskState.FAILED],
    TaskState.RESPONDING: [TaskState.RESPONDING, TaskState.COMPLETED, TaskState.FAILED],
    TaskState.COMPLETED: [TaskState.IDLE],
    TaskState.FAILED: [TaskState.IDLE],  # Can retry
    TaskState.CANCELLED: [TaskState.IDLE],
    TaskState.PAUSED: [TaskState.EXECUTING, TaskState.CANCELLED],
}


@dataclass
class TaskStateRecord:
    task_id: str
    state: TaskState
    timestamp: str
    details: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "timestamp": self.timestamp,
            "details": self.details,
            "metadata": self.metadata,
        }


class TaskStateMachine:
    """Manages task execution state with explicit transitions and history."""
    
    def __init__(self):
        self._current_state = TaskState.IDLE
        self._state_history: List[TaskStateRecord] = []
        self._lock = Lock()
        self._task_id: Optional[str] = None
        self._subtasks: Dict[str, TaskState] = {}
        self._paused_state: Optional[TaskState] = None

    def start_task(self, task_id: str = None) -> str:
        """Start a new task, returning the task ID."""
        with self._lock:
            self._task_id = task_id or f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            self._current_state = TaskState.IDLE
            self._state_history.clear()
            self._subtasks.clear()
            self._transition_to(TaskState.UNDERSTANDING, "Task started")
            return self._task_id

    def get_current_state(self) -> TaskState:
        return self._current_state

    def get_task_id(self) -> Optional[str]:
        return self._task_id

    def get_history(self) -> List[TaskStateRecord]:
        return list(self._state_history)

    def get_subtask_state(self, subtask_id: str) -> Optional[TaskState]:
        return self._subtasks.get(subtask_id)

    def set_subtask_state(self, subtask_id: str, state: TaskState, details: str = "") -> bool:
        with self._lock:
            self._subtasks[subtask_id] = state
            return True

    def _transition_to(self, new_state: TaskState, details: str = "", metadata: Dict = None) -> bool:
        """Internal method to transition state with validation."""
        if new_state not in VALID_TRANSITIONS.get(self._current_state, []):
            # Allow certain transitions regardless (e.g., FAILED from anywhere)
            if new_state != TaskState.FAILED and new_state != TaskState.CANCELLED:
                print(f"[TaskStateMachine] Invalid transition: {self._current_state.value} -> {new_state.value}")
                return False
        
        record = TaskStateRecord(
            task_id=self._task_id or "",
            state=new_state,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            details=details,
            metadata=metadata or {},
        )
        self._state_history.append(record)
        self._current_state = new_state
        print(f"[TaskStateMachine] State: {new_state.value} - {details}")
        return True

    def to_understanding(self, details: str = "") -> bool:
        return self._transition_to(TaskState.UNDERSTANDING, details)

    def to_planning(self, details: str = "") -> bool:
        return self._transition_to(TaskState.PLANNING, details)

    def to_searching(self, details: str = "") -> bool:
        return self._transition_to(TaskState.SEARCHING, details)

    def to_researching(self, details: str = "") -> bool:
        return self._transition_to(TaskState.RESEARCHING, details)

    def to_executing(self, details: str = "") -> bool:
        return self._transition_to(TaskState.EXECUTING, details)

    def to_waiting_tool(self, tool_name: str, details: str = "") -> bool:
        return self._transition_to(TaskState.WAITING_TOOL, details, {"tool": tool_name})

    def to_waiting_approval(self, action_details: str = "") -> bool:
        return self._transition_to(TaskState.WAITING_APPROVAL, action_details)

    def to_verifying(self, details: str = "") -> bool:
        return self._transition_to(TaskState.VERIFYING, details)

    def to_responding(self, details: str = "") -> bool:
        return self._transition_to(TaskState.RESPONDING, details)

    def to_completed(self, details: str = "") -> bool:
        return self._transition_to(TaskState.COMPLETED, details)

    def to_failed(self, error: str = "", details: str = "") -> bool:
        return self._transition_to(TaskState.FAILED, details or error, {"error": error})

    def to_cancelled(self, details: str = "") -> bool:
        return self._transition_to(TaskState.CANCELLED, details)

    def to_paused(self, details: str = "") -> bool:
        with self._lock:
            self._paused_state = self._current_state
            return self._transition_to(TaskState.PAUSED, details)

    def resume(self, details: str = "") -> bool:
        with self._lock:
            if self._paused_state:
                return self._transition_to(self._paused_state, details or "Resumed from pause")
            return self._transition_to(TaskState.EXECUTING, details or "Resumed")

    def is_terminal(self) -> bool:
        return self._current_state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]

    def is_active(self) -> bool:
        return self._current_state not in [TaskState.IDLE, TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]

    def get_summary(self) -> Dict[str, Any]:
        return {
            "task_id": self._task_id,
            "current_state": self._current_state.value,
            "is_terminal": self.is_terminal(),
            "is_active": self.is_active(),
            "state_history": [r.to_dict() for r in self._state_history],
            "subtasks": {k: v.value for k, v in self._subtasks.items()},
        }


_task_state_machine_instance: Optional[TaskStateMachine] = None


def get_task_state_machine() -> TaskStateMachine:
    global _task_state_machine_instance
    if _task_state_machine_instance is None:
        _task_state_machine_instance = TaskStateMachine()
    return _task_state_machine_instance


if __name__ == "__main__":
    sm = TaskStateMachine()
    tid = sm.start_task("test_task_001")
    print(f"Started task: {tid}")
    
    sm.to_planning("Planning the task")
    sm.to_searching("Searching for information")
    sm.to_executing("Executing steps")
    sm.set_subtask_state("step_1", TaskState.EXECUTING)
    sm.to_verifying("Verifying results")
    sm.to_responding("Generating response")
    sm.to_completed("Task completed successfully")
    
    print("\nState History:")
    for record in sm.get_history():
        print(f"  {record.timestamp} - {record.state.value}: {record.details}")
    
    print(f"\nFinal State: {sm.get_current_state().value}")
    print(f"Is Terminal: {sm.is_terminal()}")