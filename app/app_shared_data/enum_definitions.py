from enum import Enum, unique

@unique
class MissionStates(Enum):
    IDLE = 0
    ERROR = 1
    PAUSED = 2
    LOADING_WAYPOINTS = 3
    NAVIGATING_TO_WAYPOINT = 4
    SCANNING_SCENE = 5
    SCANNED_SCENE = 6
    APPROACHING_TARGET = 7
    AWAITING_GAZE_TARGET_INPUT = 8
    INSPECTING = 9
    INSPECTION_COMPLETE = 10
    MISSION_COMPLETE = 11


@unique
class MissionCommandSignals(Enum):
    MISSION_START = 0
    MISSION_PAUSE = 1
    MISSION_ABORT = 2
    MISSION_RESUME = 3

@unique
class ProcessingModes(Enum):
    VIDEO_RENDERING = 0 # Video rendering mode, blank banner
    INITIAL_FIXATION = 1 # Initial fixation mode, banner buttons: "Make Waypoint", "Segment", "Cancel"
    PROCESSING = 2 # Processing data mode, blank banner
    SEGMENTATION_RESULTS = 3 # Segmentation results mode, banner buttons: "Accept", "Cancel" "Cycle left", "Cycle right"
    WAYPOINT_RESULTS = 4 # Waypoint results mode, banner buttons: "Accept", "Cancel"

@unique
class ProcessingModeActions(Enum):
    MAKE_WAYPOINT = 0
    SEGMENT = 1
    CANCEL = 2
    ACCEPT = 3
    CYCLE_LEFT = 4
    CYCLE_RIGHT = 5